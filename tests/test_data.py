import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from dropship.audit import AuditLog
from dropship.cache import LockBusy, lock, mark_seen, seen
from dropship.config import Settings
from dropship.knowledge import KnowledgeStore
from dropship.models import EMBEDDING_DIMENSIONS


def unit(i: int) -> list[float]:
    v = [0.0] * EMBEDDING_DIMENSIONS
    v[i] = 1.0
    return v


async def test_audit_records_in_order(sessions):
    audit = AuditLog(sessions)
    cid = uuid.uuid4().hex
    start = datetime.now(timezone.utc) - timedelta(seconds=1)
    await audit.record("Catalog", "listing.updated", "SKU-1", {"field": "title"}, cid)
    await audit.record("Finance", "settlement.prepared", "week-39", correlation_id=cid)
    entries = [e for e in await audit.between(start, datetime.now(timezone.utc) + timedelta(seconds=1)) if e.correlation_id == cid]
    assert [(e.actor, e.action) for e in entries] == [("Catalog", "listing.updated"), ("Finance", "settlement.prepared")]
    assert entries[0].details == {"field": "title"}


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_log SET actor = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"],
)
async def test_audit_log_refuses_rewrites(sessions, statement):
    await AuditLog(sessions).record("Ops Lead", "test.entry")
    async with sessions() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin():
                await session.execute(text(statement))


async def test_knowledge_search_by_similarity_and_source(sessions):
    store = KnowledgeStore(sessions)
    await store.add("g1-policy", "Never promise a refund without approval.", unit(0))
    await store.add("g1-policy", "Reply within 24 hours.", unit(1))
    await store.add("product", "Steel water bottle, 1 litre.", unit(0))

    near_zero = unit(0)
    near_zero[1] = 0.1
    matches = await store.search(near_zero, limit=2, source="g1-policy")
    assert [m.content for m in matches] == ["Never promise a refund without approval.", "Reply within 24 hours."]
    assert matches[0].distance < matches[1].distance


async def test_knowledge_rejects_wrong_dimensions(sessions):
    with pytest.raises(ValueError):
        await KnowledgeStore(sessions).add("product", "x", [1.0, 0.0])


async def test_redis_once_markers(redis):
    assert not await seen(redis, "tg-update:1")
    await mark_seen(redis, "tg-update:1", ttl_seconds=60)
    assert await seen(redis, "tg-update:1")


async def test_redis_lock_is_exclusive(redis):
    async with lock(redis, "settlement", ttl_seconds=5):
        with pytest.raises(LockBusy):
            async with lock(redis, "settlement"):
                pass
    async with lock(redis, "settlement"):
        pass  # free again


BASE_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@db/x",
    "REDIS_URL": "redis://r",
    "TELEGRAM_BOT_TOKEN": "tok-secret",
    "TELEGRAM_WEBHOOK_SECRET": "hook-secret",
    "TELEGRAM_OWNER_CHAT_ID": "42",
    "OPENAI_API_KEY": "sk-secret",
}


def test_settings_load_and_hide_secrets(monkeypatch):
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    s = Settings(_env_file=None)
    assert s.telegram_owner_chat_id == 42 and s.temporal_address == "localhost:7233"
    assert not any(secret in repr(s) for secret in ("tok-secret", "hook-secret", "sk-secret"))


def test_settings_require_core_values(monkeypatch):
    for k in BASE_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    missing = {e["loc"][0] for e in exc.value.errors()}
    assert {"database_url", "redis_url", "telegram_bot_token", "telegram_owner_chat_id"} <= missing
