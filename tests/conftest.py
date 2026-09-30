import itertools
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from foundation.approvals import ApprovalService
from foundation.audit import AuditLog
from foundation.store import MemoryStore
from foundation.telegram import TelegramClient
from foundation.webhook import TelegramWebhook

OWNER = 111222333
SECRET = "test-secret"


class FakeTelegramAPI:
    """Records Bot API calls and answers like Telegram does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail: set[str] = set()
        self._next_message_id = 100

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content)
        self.calls.append((method, payload))
        if method in self.fail:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})
        if method == "sendMessage":
            self._next_message_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self._next_message_id}})
        return httpx.Response(200, json={"ok": True, "result": True})

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


@pytest.fixture
def clock():
    # Ticks 1 ms per call so audit entries have a deterministic order.
    ticks = itertools.count()
    start = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)
    return lambda: start + timedelta(milliseconds=next(ticks))


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def audit(store, clock):
    return AuditLog(store, clock)


@pytest.fixture
def tg_api():
    return FakeTelegramAPI()


@pytest.fixture
def telegram(tg_api):
    return TelegramClient("123:ABC", http=httpx.Client(transport=httpx.MockTransport(tg_api.handler)))


@pytest.fixture
def approvals(store, audit, telegram, clock):
    return ApprovalService(store, audit, telegram, OWNER, clock)


@pytest.fixture
def webhook(telegram, approvals):
    return TelegramWebhook(SECRET, OWNER, telegram, approvals)
