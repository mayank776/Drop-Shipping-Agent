"""S3 Ops Lead: approval queue, daily brief, alert routing, bot commands, schedule."""

import asyncio
import itertools
import json
import uuid

import pytest
from sqlalchemy import select, text

from dropship.approvals import IST, ApprovalWorkflow, request_approval
from dropship.audit import AuditLog
from dropship.models import Alert, AuditEntry
from dropship.ops_lead.alerts import AlertRequest, AlertService
from dropship.ops_lead.brief import (
    TELEGRAM_LIMIT, BriefData, BriefInput, DailyBriefWorkflow, QueuedApproval, brief_text, ist_today,
)
from dropship.ops_lead.plan import PlanItem, PlanStore, build_plan
from dropship.ops_lead.schedule import ensure_brief_schedule, parse_brief_time
from tests.conftest import OWNER
from tests.test_approvals import post, wait_for

_update_ids = itertools.count(50_000)


async def eventually(sessions, sql, timeout=10.0):
    """Poll until the SQL returns true."""
    for _ in range(int(timeout / 0.05)):
        async with sessions() as session:
            if (await session.execute(text(sql))).scalar():
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"not true in time: {sql}")


def sent_texts(tg_api):
    return [p["text"] for m, p in tg_api.calls if m == "sendMessage"]


def with_buttons(tg_api):
    return [p for m, p in tg_api.calls if m == "sendMessage" and "reply_markup" in p]


async def run_brief(temporal_env, queue, manual=False):
    return await temporal_env.client.execute_workflow(
        DailyBriefWorkflow.run, BriefInput(manual), id=f"brief-test-{uuid.uuid4().hex[:8]}", task_queue=queue
    )


async def test_approvals_wait_for_the_brief(stack, temporal_env, sessions, tg_api):
    queue, _ = stack
    client = temporal_env.client
    h1 = await request_approval(client, queue, "price_change", "SKU-1 ₹499 → ₹549", "Catalog")
    h2 = await request_approval(client, queue, "price_change", "SKU-2 ₹299 → ₹279", "Catalog")
    h3 = await request_approval(client, queue, "refund", "Order 402-… ₹349", "Support")
    await AlertService(sessions, AuditLog(sessions), None, None, OWNER).raise_alert(  # type: ignore[arg-type]
        AlertRequest("warning", "Finance", "Operating loss at 50% of ₹50,000")
    )
    await eventually(sessions, "SELECT count(*) = 3 FROM approvals")  # all three are in the queue
    assert tg_api.calls == []  # nothing goes out before the brief

    assert await run_brief(temporal_env, queue) == 3
    await eventually(sessions, "SELECT bool_and(released_at IS NOT NULL) FROM approvals")
    summary = tg_api.calls[0][1]["text"]
    assert summary.startswith("☀️ Daily brief")
    assert "Approvals: 3 to decide below" in summary
    assert "price change ×2, refund ×1" in summary
    assert "⚠️ Finance: Operating loss at 50% of ₹50,000" in summary
    assert "❗ Ops Lead: 3 approval(s) to send in today's brief" in summary

    assert len(with_buttons(tg_api)) == 3
    async with sessions() as session:
        assert all((await session.scalars(select(Alert.delivered_at))).all())

    # The next brief doesn't resend them; it lists them as still waiting.
    assert await run_brief(temporal_env, queue) == 0
    second = sent_texts(tg_api)[-1]
    assert "0 to decide below, 3 still waiting from earlier" in second
    assert "Alerts" not in second  # already delivered
    assert len(with_buttons(tg_api)) == 3
    for h in (h1, h2, h3):
        await h.terminate("test cleanup")


async def test_urgent_kinds_skip_the_queue(stack, temporal_env, tg_api):
    queue, _ = stack
    handle = await request_approval(temporal_env.client, queue, "resume", "Resume listings after kill switch", "Ops Lead")
    await wait_for(lambda: len(with_buttons(tg_api)) == 1)
    assert await handle.query(ApprovalWorkflow.status) == "pending"
    await handle.terminate("test cleanup")


class FlakyTelegram:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.sent = []

    async def send_message(self, chat_id, text, buttons=()):
        from dropship.telegram.client import TelegramError

        if self.fail_times:
            self.fail_times -= 1
            raise TelegramError("sendMessage failed: Bad Gateway")
        self.sent.append(text)
        return 1


async def test_alert_routing_and_suppression(sessions, redis, clean_queue):
    telegram = FlakyTelegram(fail_times=0)
    service = AlertService(sessions, AuditLog(sessions), telegram, redis, OWNER, retry_delay=0)  # type: ignore[arg-type]
    key = f"kill-switch-{uuid.uuid4().hex[:6]}"

    assert await service.raise_alert(AlertRequest("critical", "Kill Switch", "ODR at 75% of limit", "Listings paused", key)) == "sent"
    assert await service.raise_alert(AlertRequest("critical", "Kill Switch", "ODR at 75% of limit", "", key)) == "suppressed"
    assert await service.raise_alert(AlertRequest("warning", "Catalog", "SKU-7 out of stock")) == "queued"
    assert await service.raise_alert(AlertRequest("info", "Fulfillment", "12 orders handed off")) == "logged"
    assert telegram.sent == ["🚨 Kill Switch: ODR at 75% of limit\nListings paused"]
    assert [a.title for a in await service.undelivered()] == ["SKU-7 out of stock"]

    async with sessions() as session:
        actions = (
            await session.scalars(select(AuditEntry.action).where(AuditEntry.actor == "Kill Switch").order_by(AuditEntry.id))
        ).all()
    assert actions[-2:] == ["alert.raised", "alert.suppressed"]


async def test_undeliverable_critical_alert_goes_into_the_brief(sessions, redis, clean_queue):
    telegram = FlakyTelegram(fail_times=5)
    service = AlertService(sessions, AuditLog(sessions), telegram, redis, OWNER, retry_delay=0)  # type: ignore[arg-type]
    assert await service.raise_alert(AlertRequest("critical", "Finance", "Stop-loss reached")) == "undelivered"
    assert [(a.severity, a.title) for a in await service.undelivered()] == [("critical", "Stop-loss reached")]


def message(text, update_id=None):
    return json.dumps(
        {
            "update_id": next(_update_ids) if update_id is None else update_id,
            "message": {"chat": {"id": OWNER}, "from": {"id": OWNER}, "text": text},
        }
    )


async def test_queue_and_plan_commands(stack, temporal_env, tg_api):
    queue, http = stack
    await post(http, message("/plan"))
    assert sent_texts(tg_api)[-1].startswith("No plan yet today")

    handle = await request_approval(temporal_env.client, queue, "discount", "10% off SKU-3 for Diwali", "Support")
    await post(http, message("/queue@DropshipBot"))
    await wait_for(lambda: len(with_buttons(tg_api)) == 1)
    texts = sent_texts(tg_api)
    assert "Sending the queue now." in texts
    assert any(t.startswith("📋 Queue on request") for t in texts)

    await post(http, message("/plan"))
    assert sent_texts(tg_api)[-1].startswith("Today's plan:")
    await post(http, message("hello"))
    assert "/queue" in sent_texts(tg_api)[-1]
    await handle.terminate("test cleanup")


async def test_broken_plan_provider_does_not_sink_the_plan():
    class Broken:
        agent = "Finance"

        async def items(self, day):
            raise RuntimeError("db down")

    class Fine:
        agent = "Fulfillment"

        async def items(self, day):
            return [PlanItem("Fulfillment", "12 orders to dispatch", due="14:00 IST")]

    items = await build_plan(ist_today().date(), [Broken(), Fine()])
    assert [(i.agent, i.blocking) for i in items] == [("Finance", True), ("Fulfillment", False)]
    assert "plan unavailable (RuntimeError)" in items[0].summary


async def test_plan_store_upserts(sessions, clean_queue):
    store = PlanStore(sessions)
    day = ist_today().date()
    await store.save(day, [PlanItem("Ops Lead", "first")])
    await store.save(day, [PlanItem("Ops Lead", "second")])
    assert [i.summary for i in await store.get(day)] == ["second"]


def test_brief_text_is_truncated_to_telegram_limit():
    data = BriefData(
        day="2026-09-30", manual=False,
        waiting=[QueuedApproval(uuid.uuid4().hex, "price_change") for _ in range(400)],
    )
    assert len(brief_text(data)) <= TELEGRAM_LIMIT


@pytest.mark.parametrize("value", ["9", "25:00", "09:60", "nine"])
def test_bad_brief_time(value):
    with pytest.raises(ValueError):
        parse_brief_time(value)


async def test_schedule_created_then_updated(temporal_env):
    client = temporal_env.client
    await ensure_brief_schedule(client, "dropship", "09:00")
    await ensure_brief_schedule(client, "dropship", "08:30")
    desc = await client.get_schedule_handle("daily-brief").describe()
    assert desc.schedule.spec.time_zone_name == "Asia/Kolkata"
    next_run = desc.info.next_action_times[0].astimezone(IST)
    assert (next_run.hour, next_run.minute) == (8, 30)
    await client.get_schedule_handle("daily-brief").delete()
