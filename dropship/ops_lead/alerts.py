"""Alert routing: critical → Telegram now, warning → next daily brief, info → audit only."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from redis.asyncio import Redis
from temporalio import activity
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit import AuditLog
from ..cache import mark_seen, seen
from ..models import Alert
from ..telegram.client import TelegramClient, TelegramError

log = logging.getLogger(__name__)

SEVERITIES = ("critical", "warning", "info")
ICONS = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
SUPPRESS_SECONDS = 3_600
SEND_ATTEMPTS = 3


@dataclass
class AlertRequest:
    severity: str
    source: str  # the agent raising it
    title: str
    body: str = ""
    dedupe_key: str | None = None  # repeated critical alerts with this key are suppressed for an hour

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")


def alert_text(source: str, title: str, body: str, severity: str) -> str:
    return "\n".join(filter(None, [f"{ICONS[severity]} {source}: {title}", body]))


class AlertService:
    def __init__(
        self, sessions: async_sessionmaker, audit: AuditLog, telegram: TelegramClient, redis: Redis, owner_chat_id: int,
        retry_delay: float = 1.0,
    ) -> None:
        self._sessions = sessions
        self._audit = audit
        self._telegram = telegram
        self._redis = redis
        self._owner = owner_chat_id
        self._retry_delay = retry_delay

    async def raise_alert(self, alert: AlertRequest) -> str:
        """Returns what happened: "sent", "queued", "logged", "suppressed" or "undelivered"."""
        details = {"severity": alert.severity, "title": alert.title, "dedupe_key": alert.dedupe_key}
        key = f"alert:{alert.dedupe_key}" if alert.dedupe_key else None
        if alert.severity == "critical" and key and await seen(self._redis, key):
            await self._audit.record(alert.source, "alert.suppressed", alert.title, details)
            return "suppressed"

        row = Alert(
            severity=alert.severity, source=alert.source, title=alert.title, body=alert.body, dedupe_key=alert.dedupe_key,
            delivered_at=datetime.now(timezone.utc) if alert.severity == "info" else None,
        )
        async with self._sessions() as session, session.begin():
            session.add(row)
        await self._audit.record(alert.source, "alert.raised", alert.title, {**details, "alert_id": row.id})

        if alert.severity == "info":
            return "logged"
        if alert.severity == "warning":
            return "queued"

        text = alert_text(alert.source, alert.title, alert.body, alert.severity)
        for attempt in range(1, SEND_ATTEMPTS + 1):
            try:
                await self._telegram.send_message(self._owner, text)
                break
            except TelegramError as exc:
                log.error("Critical alert send failed (attempt %d): %s", attempt, exc)
                if attempt == SEND_ATTEMPTS:
                    return "undelivered"  # stays undelivered, so the next brief carries it
                await asyncio.sleep(self._retry_delay * attempt)
        await self.mark_delivered([row.id])
        if key:
            await mark_seen(self._redis, key, SUPPRESS_SECONDS)
        return "sent"

    async def undelivered(self) -> list[Alert]:
        async with self._sessions() as session:
            result = await session.scalars(
                select(Alert).where(Alert.delivered_at.is_(None)).order_by(Alert.created_at, Alert.id)
            )
            return list(result)

    async def mark_delivered(self, ids: list[int]) -> None:
        if not ids:
            return
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Alert).where(Alert.id.in_(ids), Alert.delivered_at.is_(None)).values(delivered_at=datetime.now(timezone.utc))
            )


class AlertActivities:
    """Lets any workflow raise an alert: ``workflow.execute_activity("ops.alert.raise", AlertRequest(...))``."""

    def __init__(self, service: AlertService) -> None:
        self._service = service

    @activity.defn(name="ops.alert.raise")
    async def raise_alert(self, alert: AlertRequest) -> str:
        return await self._service.raise_alert(alert)

    def all(self) -> list:
        return [self.raise_alert]
