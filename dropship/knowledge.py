"""pgvector store for policies (Amazon, G1), product knowledge and wholesaler docs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import EMBEDDING_DIMENSIONS, KnowledgeChunk


@dataclass(frozen=True)
class Match:
    id: int
    source: str
    content: str
    metadata: dict[str, Any]
    distance: float  # cosine distance: 0 = identical


def _check(embedding: list[float]) -> None:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"embedding has {len(embedding)} dimensions; expected {EMBEDDING_DIMENSIONS}")


class KnowledgeStore:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def add(self, source: str, content: str, embedding: list[float], metadata: dict[str, Any] | None = None) -> int:
        _check(embedding)
        chunk = KnowledgeChunk(source=source, content=content, embedding=embedding, meta=metadata or {})
        async with self._sessions() as session, session.begin():
            session.add(chunk)
        return chunk.id

    async def search(self, embedding: list[float], limit: int = 5, source: str | None = None) -> list[Match]:
        _check(embedding)
        distance = KnowledgeChunk.embedding.cosine_distance(embedding).label("distance")
        query = select(KnowledgeChunk, distance).order_by(distance).limit(limit)
        if source:
            query = query.where(KnowledgeChunk.source == source)
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        return [Match(c.id, c.source, c.content, c.meta, float(d)) for c, d in rows]
