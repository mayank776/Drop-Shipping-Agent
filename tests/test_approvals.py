"""End to end: Temporal approval workflow + Telegram webhook + PostgreSQL + Redis."""

import asyncio
import itertools
import json
import uuid
import httpx
import pytest
from sqlalchemy import select

from dropship.api import create_app
from dropship.approvals import ApprovalRequest, ApprovalWorkflow, callback_data, new_approval_id, workflow_id
from dropship.models import Approval, AuditEntry
from dropship.telegram.webhook import SECRET_HEADER
from tests.conftest import OWNER, SECRET

_update_ids = itertools.count(1000)


async def wait_for(predicate, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met in time")


async def start(temporal_env, queue, tg_api, kind="price_change", urgent=True):
    request = ApprovalRequest(new_approval_id(), kind, "SKU-12 price ₹499 → ₹549", "Catalog", ["Margin 18% → 24%"], urgent)
    handle = await temporal_env.client.start_workflow(
        ApprovalWorkflow.run, request, id=workflow_id(request.approval_id), task_queue=queue
    )
    if urgent:
        await wait_for(lambda: "sendMessage" in tg_api.methods())
    return request, handle


def tap(approval_id, approve=True, chat_id=OWNER, from_id=OWNER, update_id=None):
    return json.dumps(
        {
            "update_id": next(_update_ids) if update_id is None else update_id,
            "callback_query": {
                "id": "cb1",
                "from": {"id": from_id},
                "message": {"message_id": 101, "chat": {"id": chat_id}},
                "data": callback_data(approval_id, approve),
            },
        }
    )


async def post(http, body, secret=SECRET):
    headers = {SECRET_HEADER: secret} if secret else {}
    return await http.post("/telegram/webhook", content=body, headers=headers)


def answers(tg_api):
    return [p["text"] for m, p in tg_api.calls if m == "answerCallbackQuery"]


async def test_approve_end_to_end(stack, temporal_env, sessions, tg_api):
    queue, http = stack
    request, handle = await start(temporal_env, queue, tg_api)

    method, payload = tg_api.calls[0]
    assert method == "sendMessage" and payload["chat_id"] == OWNER
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Approve", "Reject"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)

    assert (await post(http, tap(request.approval_id))).status_code == 200
    result = await handle.result()
    assert result.approved and result.actor == "mayank"

    async with sessions() as session:
        row = await session.get(Approval, request.approval_id)
        actions = (
            await session.scalars(
                select(AuditEntry.action).where(AuditEntry.subject == request.approval_id).order_by(AuditEntry.id)
            )
        ).all()
    assert row.status == "approved" and row.telegram_message_id == 101 and row.decided_by == "mayank"
    assert actions == ["approval.requested", "approval.approved"]
    assert answers(tg_api) == ["Approved"]
    edited = [p for m, p in tg_api.calls if m == "editMessageText"][-1]
    assert "✅ Approved" in edited["text"] and "IST" in edited["text"] and "reply_markup" not in edited


async def test_second_tap_cannot_flip_decision(stack, temporal_env, sessions, tg_api):
    queue, http = stack
    request, handle = await start(temporal_env, queue, tg_api, kind="refund")
    await post(http, tap(request.approval_id, approve=False))
    assert not (await handle.result()).approved
    await post(http, tap(request.approval_id, approve=True))
    assert answers(tg_api) == ["Rejected", "Already rejected"]
    async with sessions() as session:
        assert (await session.get(Approval, request.approval_id)).status == "rejected"


async def test_rejects_bad_secret_and_ignores_strangers(stack, temporal_env, sessions, tg_api):
    queue, http = stack
    request, handle = await start(temporal_env, queue, tg_api)
    assert (await post(http, tap(request.approval_id), secret=None)).status_code == 401
    assert (await post(http, tap(request.approval_id), secret="wrong")).status_code == 401
    for chat_id, from_id in [(999, 999), (OWNER, 999), (999, OWNER)]:
        assert (await post(http, tap(request.approval_id, chat_id=chat_id, from_id=from_id))).status_code == 200
    assert await handle.query(ApprovalWorkflow.status) == "pending"
    assert answers(tg_api) == []
    await handle.terminate("test cleanup")


async def test_retried_update_is_handled_once(stack, temporal_env, sessions, tg_api):
    queue, http = stack
    request, handle = await start(temporal_env, queue, tg_api)
    body = tap(request.approval_id, update_id=5)
    await post(http, body)
    await handle.result()
    await post(http, body)  # Telegram retry of the same update
    assert answers(tg_api) == ["Approved"]


async def test_unknown_approval_and_bad_data(stack, temporal_env, sessions, tg_api):
    _, http = stack
    await post(http, tap("f" * 32))
    body = json.loads(tap("x"))
    body["callback_query"]["data"] = "evil"
    await post(http, json.dumps(body))
    assert (await post(http, "{not json")).status_code == 400
    assert answers(tg_api) == ["Unknown approval", "Unknown action"]


async def test_owner_message_gets_liveness_reply(stack, temporal_env, sessions, tg_api):
    def msg(cid):
        return json.dumps({"update_id": next(_update_ids), "message": {"chat": {"id": cid}, "from": {"id": cid}, "text": "/start"}})

    _, http = stack
    await post(http, msg(999))
    assert tg_api.calls == []
    await post(http, msg(OWNER))
    assert tg_api.calls[0][0] == "sendMessage" and tg_api.calls[0][1]["chat_id"] == OWNER


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        ApprovalRequest(new_approval_id(), "free_money", "t", "Ads")


async def test_health():
    app = create_app(deps=object())  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        assert (await http.get("/health")).json() == {"status": "ok"}
