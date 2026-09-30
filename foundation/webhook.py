"""Telegram webhook handling, independent of the Functions runtime so it can be tested."""

from __future__ import annotations

import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any

from .approvals import APPROVAL_EVENT, ApprovalService, parse_callback_data
from .store import NotFound
from .telegram import TelegramClient, TelegramError

log = logging.getLogger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@dataclass(frozen=True)
class RaiseEvent:
    instance_id: str
    name: str
    data: dict[str, Any]


@dataclass(frozen=True)
class WebhookResult:
    status: int
    event: RaiseEvent | None = None


class TelegramWebhook:
    def __init__(self, secret: str, owner_chat_id: int, telegram: TelegramClient, approvals: ApprovalService) -> None:
        self._secret = secret
        self._owner = owner_chat_id
        self._telegram = telegram
        self._approvals = approvals

    def handle(self, secret_header: str | None, body: bytes) -> WebhookResult:
        if not secret_header or not hmac.compare_digest(secret_header.encode(), self._secret.encode()):
            return WebhookResult(401)
        try:
            update = json.loads(body)
        except ValueError:
            return WebhookResult(400)
        if not isinstance(update, dict):
            return WebhookResult(400)

        # Telegram retries non-2xx responses, so anything past authentication returns 200.
        if "callback_query" in update:
            return self._on_callback(update["callback_query"])
        if "message" in update:
            self._on_message(update["message"])
        return WebhookResult(200)

    def _is_owner(self, chat: dict | None, sender: dict | None) -> bool:
        return bool(chat and sender and chat.get("id") == self._owner and sender.get("id") == self._owner)

    def _on_message(self, message: dict) -> None:
        if not self._is_owner(message.get("chat"), message.get("from")):
            log.warning("Ignoring message from non-owner chat")
            return
        self._safe(self._telegram.send_message, self._owner, "Bot is online. Approval requests will appear here.")

    def _on_callback(self, query: dict) -> WebhookResult:
        message = query.get("message") or {}
        if not self._is_owner(message.get("chat"), query.get("from")):
            log.warning("Ignoring callback from non-owner chat")
            return WebhookResult(200)

        parsed = parse_callback_data(query.get("data") or "")
        if parsed is None:
            self._safe(self._telegram.answer_callback, query["id"], "Unknown action")
            return WebhookResult(200)
        approval_id, approve = parsed

        try:
            decision = self._approvals.decide(approval_id, approve)
        except NotFound:
            self._safe(self._telegram.answer_callback, query["id"], "Unknown approval")
            return WebhookResult(200)

        approval = decision.approval
        if not decision.changed:
            self._safe(self._telegram.answer_callback, query["id"], f"Already {approval.status.value}")
            return WebhookResult(200)

        self._safe(self._telegram.answer_callback, query["id"], approval.status.value.capitalize())
        if "message_id" in message:
            self._safe(self._telegram.edit_message_text, self._owner, message["message_id"], approval.message_text())

        event = None
        if approval.instance_id:
            event = RaiseEvent(
                approval.instance_id,
                APPROVAL_EVENT,
                {"approval_id": approval.id, "approved": approve},
            )
        return WebhookResult(200, event)

    @staticmethod
    def _safe(fn, *args) -> None:
        # The decision is already recorded; a failed UI update must not undo or retry it.
        try:
            fn(*args)
        except TelegramError as exc:
            log.error("Telegram call failed: %s", exc)
