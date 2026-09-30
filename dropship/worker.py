"""Temporal worker. Run with `python -m dropship.worker`."""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from .approvals import ApprovalActivities, ApprovalWorkflow
from .audit import AuditLog
from .config import get_settings
from .db import make_engine, make_sessionmaker
from .telegram.client import TelegramClient
from .telemetry import setup_tracing
from .temporal import connect

WORKFLOWS = [ApprovalWorkflow]


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    setup_tracing(f"{settings.service_name}-worker")
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    activities = ApprovalActivities(
        sessions,
        AuditLog(sessions),
        TelegramClient(settings.telegram_bot_token.get_secret_value()),
        settings.telegram_owner_chat_id,
    )
    client = await connect(settings)
    worker = Worker(client, task_queue=settings.temporal_task_queue, workflows=WORKFLOWS, activities=activities.all())
    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
