"""Runs TableStore against Azurite; skipped when it isn't listening on 127.0.0.1:10002."""

import socket
import uuid

import pytest

from foundation.store import Conflict, NotFound, TableStore


def _azurite_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 10002), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _azurite_up(), reason="Azurite table service not running")


def test_table_store_round_trip():
    store = TableStore(connection_string="UseDevelopmentStorage=true")
    table = "t" + uuid.uuid4().hex[:12]
    first = store.insert(table, "p", "b", {"v": 1})
    store.insert(table, "p", "a", {"v": 0})
    with pytest.raises(Conflict):
        store.insert(table, "p", "b", {})

    store.replace(table, first, {"v": 2})
    with pytest.raises(Conflict):
        store.replace(table, first, {"v": 3})
    assert store.get(table, "p", "b").data == {"v": 2}
    assert [r.rk for r in store.query(table, "p")] == ["a", "b"]
    with pytest.raises(NotFound):
        store.get(table, "p", "missing")
