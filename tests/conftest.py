"""Shared fixtures.

Integration tests need PostgreSQL (with pgvector), Redis and a Temporal CLI binary, and are
skipped when those aren't available:

- TEST_DATABASE_URL  default postgresql+asyncpg://dropship:dropship@127.0.0.1:5432/dropship_test
- TEST_REDIS_URL     default redis://127.0.0.1:6379/15
- TEMPORAL_CLI_PATH  path to a `temporal` binary; otherwise the SDK tries to download one
- REQUIRE_SERVICES   set to 1 (as CI does) to fail instead of skip when a service is missing
"""

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from dropship.db import make_sessionmaker
from dropship.telegram.client import TelegramClient

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://dropship:dropship@127.0.0.1:5432/dropship_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:6379/15")
OWNER = 111222333
SECRET = "test-secret"


def unavailable(reason: str) -> None:
    if os.environ.get("REQUIRE_SERVICES") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


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
def tg_api():
    return FakeTelegramAPI()


@pytest.fixture
def telegram(tg_api):
    return TelegramClient("123:ABC", http=httpx.AsyncClient(transport=httpx.MockTransport(tg_api.handler)))


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            # A fresh schema per run; dropping is allowed even though the audit log refuses deletes.
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    except (OSError, Exception) as exc:  # noqa: BLE001 - any connection failure means "no database"
        await engine.dispose()
        unavailable(f"PostgreSQL not available: {exc}")
    migrate = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "alembic", "upgrade", "head",
        env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    out, _ = await migrate.communicate()
    assert migrate.returncode == 0, out.decode()
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessionmaker(engine)


@pytest.fixture
async def redis():
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError

    client = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await client.flushdb()
    except (RedisConnectionError, OSError) as exc:
        unavailable(f"Redis not available: {exc}")
    yield client
    await client.aclose()


@pytest.fixture(scope="session")
async def temporal_env():
    from temporalio.testing import WorkflowEnvironment

    try:
        env = await WorkflowEnvironment.start_local(dev_server_existing_path=os.environ.get("TEMPORAL_CLI_PATH"))
    except RuntimeError as exc:
        unavailable(f"Temporal dev server not available: {exc}")
    yield env
    await env.shutdown()


@pytest.fixture
async def clean_queue(sessions):
    """The brief looks at every pending approval and alert, so start those tables empty."""
    async with sessions() as session, session.begin():
        for table in ("approvals", "alerts", "daily_plans"):
            await session.execute(text(f"DELETE FROM {table}"))


@pytest.fixture
async def stack(temporal_env, sessions, telegram, redis, clean_queue):
    """A running worker plus the FastAPI app wired to it. Yields (task_queue, http client)."""
    import uuid

    from dropship.api import create_app
    from dropship.telegram.webhook import WebhookDeps
    from dropship.worker import build_worker

    queue = f"test-{uuid.uuid4().hex[:8]}"
    worker = build_worker(temporal_env.client, queue, sessions, telegram, redis, OWNER)
    deps = WebhookDeps(SECRET, OWNER, telegram, temporal_env.client, redis, sessions, queue)
    async with worker:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(deps)), base_url="http://test") as http:
            yield queue, http
