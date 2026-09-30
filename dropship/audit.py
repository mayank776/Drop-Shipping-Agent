"""Append-only audit log of every agent action and every approval decision.

There is no update or delete here, and the database refuses them too: corrections are new entries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import AuditEntry


class AuditLog:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def record(
        self,
        actor: str,
        action: str,
        subject: str = "",
        details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                AuditEntry(
                    actor=actor,
                    action=action,
                    subject=subject,
                    details=details or {},
                    correlation_id=correlation_id,
                )
            )

    async def between(self, start: datetime, end: datetime) -> list[AuditEntry]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(AuditEntry)
                .where(AuditEntry.at >= start, AuditEntry.at < end)
                .order_by(AuditEntry.at, AuditEntry.id)
            )
            return list(result)
