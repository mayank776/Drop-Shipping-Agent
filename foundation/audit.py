"""Append-only audit log.

Every agent action and every approval decision is recorded here. There is deliberately
no update or delete: corrections are new entries.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from .store import Store

TABLE = "audit"


@dataclass(frozen=True)
class AuditEvent:
    at: str
    actor: str
    action: str
    subject: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


class AuditLog:
    def __init__(self, store: Store, clock: Callable[[], datetime] | None = None) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def record(
        self,
        actor: str,
        action: str,
        subject: str = "",
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> AuditEvent:
        now = self._clock().astimezone(timezone.utc)
        event = AuditEvent(
            at=now.isoformat(timespec="microseconds"),
            actor=actor,
            action=action,
            subject=subject,
            details=details or {},
            correlation_id=correlation_id,
        )
        # Row keys sort by time within the day; the suffix keeps same-instant writes apart.
        rk = f"{now.strftime('%H%M%S%f')}-{secrets.token_hex(4)}"
        self._store.insert(TABLE, now.date().isoformat(), rk, event.__dict__)
        return event

    def for_day(self, day: date) -> list[AuditEvent]:
        return [AuditEvent(**r.data) for r in self._store.query(TABLE, day.isoformat())]
