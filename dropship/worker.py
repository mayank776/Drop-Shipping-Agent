"""Temporal worker. Run with `python -m dropship.worker`."""

from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from .approvals import ApprovalActivities, ApprovalWorkflow
from .audit import AuditLog
from .cache import make_redis
from .config import get_settings
from .db import make_engine, make_sessionmaker
from .ops_lead.alerts import AlertActivities, AlertService
from .ops_lead.brief import BriefActivities, DailyBriefWorkflow
from .ops_lead.plan import ApprovalsProvider
from .ops_lead.schedule import ensure_brief_schedule
from .telegram.client import TelegramClient
from .telemetry import setup_tracing
from .temporal import connect

WORKFLOWS = [ApprovalWorkflow, DailyBriefWorkflow]


def build_worker(client: Client, task_queue: str, sessions, telegram: TelegramClient, redis, owner_chat_id: int) -> Worker:
    audit = AuditLog(sessions)
    alerts = AlertService(sessions, audit, telegram, redis, owner_chat_id)
    activities = [
        *ApprovalActivities(sessions, audit, telegram, owner_chat_id).all(),
        *AlertActivities(alerts).all(),
        # Each agent spec adds its plan provider here.
        *BriefActivities(sessions, telegram, alerts, owner_chat_id, [ApprovalsProvider(sessions)]).all(),
    ]
    return Worker(client, task_queue=task_queue, workflows=WORKFLOWS, activities=activities)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    setup_tracing(f"{settings.service_name}-worker")
    engine = make_engine(settings.database_url)
    redis = make_redis(settings.redis_url)
    client = await connect(settings)
    await ensure_brief_schedule(client, settings.temporal_task_queue, settings.daily_brief_time)
    worker = build_worker(
        client,
        settings.temporal_task_queue,
        make_sessionmaker(engine),
        TelegramClient(settings.telegram_bot_token.get_secret_value()),
        redis,
        settings.telegram_owner_chat_id,
    )
    try:
        await worker.run()
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
