"""Minimal async Telegram Bot API client (plain text only, so no escaping pitfalls)."""

from __future__ import annotations

from typing import Any, Sequence

import httpx

API = "https://api.telegram.org"

Button = tuple[str, str]  # (label, callback_data)


class TelegramError(RuntimeError):
    pass


class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None) -> None:
        self._base = f"{API}/bot{token}"
        self._http = http or httpx.AsyncClient(timeout=10)

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        try:
            response = await self._http.post(f"{self._base}/{method}", json=payload)
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never include the URL: it contains the bot token.
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(f"{method} failed: {body.get('description', response.status_code)}")
        return body["result"]

    async def send_message(self, chat_id: int, text: str, buttons: Sequence[Button] = ()) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]
            }
        return (await self._call("sendMessage", payload))["message_id"]

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        # Omitting reply_markup removes the buttons.
        try:
            await self._call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})
        except TelegramError as exc:
            if "message is not modified" not in str(exc):
                raise

    async def answer_callback(self, callback_query_id: str, text: str) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    async def set_webhook(self, url: str, secret_token: str) -> None:
        await self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            },
        )
