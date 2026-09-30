"""Telegram webhook: turns Approve/Reject taps into Temporal signals."""

from __future__ import annotations

import hmac
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from ..approvals import ApprovalWorkflow, DecisionSignal, parse_callback_data, workflow_id
from ..cache import mark_seen, seen
from ..ops_lead.brief import BriefInput, DailyBriefWorkflow, ist_today
from ..ops_lead.plan import PlanStore, format_plan
from .client import TelegramClient, TelegramError

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@dataclass
class WebhookDeps:
    secret: str
    owner_chat_id: int
    telegram: TelegramClient
    temporal: Client
    redis: Redis
    sessions: async_sessionmaker
    task_queue: str


def make_router(get_deps) -> APIRouter:
    """``get_deps`` returns the ``WebhookDeps`` for the running app."""
    router = APIRouter()

    @router.post("/telegram/webhook")
    async def telegram_webhook(request: Request) -> Response:
        deps: WebhookDeps = get_deps(request)
        header = request.headers.get(SECRET_HEADER)
        if not header or not hmac.compare_digest(header.encode(), deps.secret.encode()):
            return Response(status_code=401)
        try:
            update = json.loads(await request.body())
        except ValueError:
            return Response(status_code=400)
        if not isinstance(update, dict):
            return Response(status_code=400)

        # Telegram re-sends an update until it gets a 2xx; handle each update_id once.
        key = f"tg-update:{update.get('update_id')}"
        if "update_id" in update and await seen(deps.redis, key):
            return Response(status_code=200)
        if "callback_query" in update:
            await _on_callback(deps, update["callback_query"])
        elif "message" in update:
            await _on_message(deps, update["message"], update.get("update_id"))
        if "update_id" in update:
            await mark_seen(deps.redis, key)
        return Response(status_code=200)

    return router


def _is_owner(deps: WebhookDeps, chat: Any, sender: Any) -> bool:
    return (
        isinstance(chat, dict) and isinstance(sender, dict)
        and chat.get("id") == deps.owner_chat_id and sender.get("id") == deps.owner_chat_id
    )


HELP = (
    "Bot is online. Approval requests arrive with the daily brief.\n"
    "/queue – send the approval queue now\n"
    "/plan – today's plan"
)


async def _on_message(deps: WebhookDeps, message: dict, update_id: Any) -> None:
    if not _is_owner(deps, message.get("chat"), message.get("from")):
        log.warning("Ignoring message from a non-owner chat")
        return
    words = (message.get("text") or "").split()
    command = words[0].split("@")[0].lower() if words else ""  # "/queue@MyBot" → "/queue"
    if command == "/queue":
        try:
            await deps.temporal.start_workflow(
                DailyBriefWorkflow.run, BriefInput(manual=True), id=f"brief-manual-{update_id if update_id is not None else uuid.uuid4().hex}", task_queue=deps.task_queue
            )
        except WorkflowAlreadyStartedError:
            pass  # same update delivered twice
        await _safe(deps.telegram.send_message(deps.owner_chat_id, "Sending the queue now."))
    elif command == "/plan":
        plan = await PlanStore(deps.sessions).get(ist_today().date())
        text = "No plan yet today; it's built with the daily brief." if plan is None else "Today's plan:\n" + format_plan(plan)
        await _safe(deps.telegram.send_message(deps.owner_chat_id, text))
    else:
        await _safe(deps.telegram.send_message(deps.owner_chat_id, HELP))


async def _on_callback(deps: WebhookDeps, query: dict) -> None:
    if not _is_owner(deps, (query.get("message") or {}).get("chat"), query.get("from")):
        log.warning("Ignoring callback from a non-owner chat")
        return
    query_id = query.get("id", "")
    parsed = parse_callback_data(query.get("data") or "")
    if parsed is None:
        await _safe(deps.telegram.answer_callback(query_id, "Unknown action"))
        return
    approval_id, approve = parsed
    handle = deps.temporal.get_workflow_handle(workflow_id(approval_id))

    try:
        status = await handle.query(ApprovalWorkflow.status)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            await _safe(deps.telegram.answer_callback(query_id, "Unknown approval"))
            return
        status = "pending"  # worker busy or down: signals are durable, so send it anyway
    if status != "pending":
        await _safe(deps.telegram.answer_callback(query_id, f"Already {status}"))
        return

    try:
        await handle.signal(ApprovalWorkflow.decide, DecisionSignal(approved=approve))
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise  # 500 → Telegram retries the update
        await _safe(deps.telegram.answer_callback(query_id, "Already decided"))
        return
    await _safe(deps.telegram.answer_callback(query_id, "Approved" if approve else "Rejected"))


async def _safe(call) -> None:
    # Button feedback is cosmetic; the decision already lives in Temporal.
    try:
        await call
    except TelegramError as exc:
        log.error("Telegram call failed: %s", exc)
