"""Record store with optimistic concurrency.

Records are addressed by (table, partition key, row key) and carry a JSON-serialisable
``data`` dict. Writes that replace a record must present the ETag they read, so two
writers can't silently overwrite each other.
"""

from __future__ import annotations

import itertools
import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

# Table Storage caps a string property at 64 KiB (UTF-16), i.e. 32K characters.
MAX_DATA_CHARS = 32_000


class NotFound(KeyError):
    pass


class Conflict(RuntimeError):
    """Record already exists, or changed since it was read."""


@dataclass(frozen=True)
class Record:
    pk: str
    rk: str
    data: dict[str, Any]
    etag: str


class Store(Protocol):
    def insert(self, table: str, pk: str, rk: str, data: dict[str, Any]) -> Record: ...
    def get(self, table: str, pk: str, rk: str) -> Record: ...
    def replace(self, table: str, record: Record, data: dict[str, Any]) -> Record: ...
    def query(self, table: str, pk: str) -> list[Record]: ...


def _encode(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, separators=(",", ":"), sort_keys=True)
    if len(encoded) > MAX_DATA_CHARS:
        raise ValueError(f"record data is {len(encoded)} chars; limit is {MAX_DATA_CHARS}")
    return encoded


class MemoryStore:
    """In-process store for tests and local experiments."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], tuple[str, str]] = {}
        self._etags = itertools.count(1)
        self._lock = threading.Lock()

    def insert(self, table: str, pk: str, rk: str, data: dict[str, Any]) -> Record:
        encoded = _encode(data)
        with self._lock:
            if (table, pk, rk) in self._rows:
                raise Conflict(f"{table}/{pk}/{rk} already exists")
            etag = str(next(self._etags))
            self._rows[(table, pk, rk)] = (encoded, etag)
        return Record(pk, rk, json.loads(encoded), etag)

    def get(self, table: str, pk: str, rk: str) -> Record:
        with self._lock:
            try:
                encoded, etag = self._rows[(table, pk, rk)]
            except KeyError:
                raise NotFound(f"{table}/{pk}/{rk}") from None
        return Record(pk, rk, json.loads(encoded), etag)

    def replace(self, table: str, record: Record, data: dict[str, Any]) -> Record:
        encoded = _encode(data)
        key = (table, record.pk, record.rk)
        with self._lock:
            current = self._rows.get(key)
            if current is None:
                raise NotFound(f"{table}/{record.pk}/{record.rk}")
            if current[1] != record.etag:
                raise Conflict(f"{table}/{record.pk}/{record.rk} changed since it was read")
            etag = str(next(self._etags))
            self._rows[key] = (encoded, etag)
        return Record(record.pk, record.rk, json.loads(encoded), etag)

    def query(self, table: str, pk: str) -> list[Record]:
        with self._lock:
            rows = sorted(
                (rk, encoded, etag)
                for (t, p, rk), (encoded, etag) in self._rows.items()
                if t == table and p == pk
            )
        return [Record(pk, rk, json.loads(encoded), etag) for rk, encoded, etag in rows]


class TableStore:
    """Azure Table Storage backend. Tables are created on first use."""

    def __init__(self, *, endpoint: str | None = None, connection_string: str | None = None) -> None:
        from azure.data.tables import TableServiceClient

        if connection_string:
            self._service = TableServiceClient.from_connection_string(connection_string)
        elif endpoint:
            from azure.identity import DefaultAzureCredential

            self._service = TableServiceClient(endpoint=endpoint, credential=DefaultAzureCredential())
        else:
            raise ValueError("TableStore needs an endpoint or a connection string")
        self._tables: dict[str, Any] = {}
        self._lock = threading.Lock()

    def _table(self, name: str):
        with self._lock:
            if name not in self._tables:
                self._tables[name] = self._service.create_table_if_not_exists(name)
            return self._tables[name]

    @staticmethod
    def _record(entity: Any) -> Record:
        return Record(
            entity["PartitionKey"],
            entity["RowKey"],
            json.loads(entity["data"]),
            entity.metadata["etag"],
        )

    def insert(self, table: str, pk: str, rk: str, data: dict[str, Any]) -> Record:
        from azure.core.exceptions import ResourceExistsError

        entity = {"PartitionKey": pk, "RowKey": rk, "data": _encode(data)}
        try:
            meta = self._table(table).create_entity(entity)
        except ResourceExistsError:
            raise Conflict(f"{table}/{pk}/{rk} already exists") from None
        return Record(pk, rk, json.loads(entity["data"]), meta["etag"])

    def get(self, table: str, pk: str, rk: str) -> Record:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._record(self._table(table).get_entity(pk, rk))
        except ResourceNotFoundError:
            raise NotFound(f"{table}/{pk}/{rk}") from None

    def replace(self, table: str, record: Record, data: dict[str, Any]) -> Record:
        from azure.core import MatchConditions
        from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
        from azure.data.tables import UpdateMode

        entity = {"PartitionKey": record.pk, "RowKey": record.rk, "data": _encode(data)}
        try:
            meta = self._table(table).update_entity(
                entity,
                mode=UpdateMode.REPLACE,
                etag=record.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            raise Conflict(f"{table}/{record.pk}/{record.rk} changed since it was read") from None
        except ResourceNotFoundError:
            raise NotFound(f"{table}/{record.pk}/{record.rk}") from None
        return Record(record.pk, record.rk, json.loads(entity["data"]), meta["etag"])

    def query(self, table: str, pk: str) -> list[Record]:
        entities = self._table(table).query_entities(
            "PartitionKey eq @pk", parameters={"pk": pk}
        )
        return sorted((self._record(e) for e in entities), key=lambda r: r.rk)
