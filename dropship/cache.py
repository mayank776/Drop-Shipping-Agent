"""Redis: shared client, distributed locks and "do once" markers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError


class LockBusy(RuntimeError):
    pass


def make_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


@asynccontextmanager
async def lock(redis: Redis, name: str, ttl_seconds: float = 30, wait_seconds: float = 0) -> AsyncIterator[None]:
    """Hold ``lock:<name>`` across processes. Raises ``LockBusy`` if it can't be taken in time."""
    held = redis.lock(f"lock:{name}", timeout=ttl_seconds, blocking_timeout=wait_seconds)
    if not await held.acquire(blocking=wait_seconds > 0):
        raise LockBusy(name)
    try:
        yield
    finally:
        try:
            await held.release()
        except LockNotOwnedError:
            pass  # expired while held; the TTL did its job


async def seen(redis: Redis, key: str) -> bool:
    return bool(await redis.exists(f"once:{key}"))


async def mark_seen(redis: Redis, key: str, ttl_seconds: int = 86_400) -> None:
    await redis.set(f"once:{key}", "1", ex=ttl_seconds)
