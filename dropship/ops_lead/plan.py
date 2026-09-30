"""Daily plan: each agent contributes items through a provider; Ops Lead assembles one per day."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Protocol, Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..models import Approval, DailyPlan

log = logging.getLogger(__name__)


@dataclass
class PlanItem:
    agent: str
    summary: str
    due: str | None = None  # e.g. "14:00 IST"
    blocking: bool = False  # needs Mayank or blocks another agent


class PlanProvider(Protocol):
    agent: str

    async def items(self, day: date) -> list[PlanItem]: ...


class ApprovalsProvider:
    """Ops Lead's own items: approvals waiting on Mayank."""

    agent = "Ops Lead"

    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def items(self, day: date) -> list[PlanItem]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    select(Approval.released_at.is_(None), func.count())
                    .where(Approval.status == "pending")
                    .group_by(Approval.released_at.is_(None))
                )
            ).all()
        counts = {bool(queued): n for queued, n in rows}
        items = []
        if counts.get(True):
            items.append(PlanItem(self.agent, f"{counts[True]} approval(s) to send in today's brief", blocking=True))
        if counts.get(False):
            items.append(PlanItem(self.agent, f"{counts[False]} approval(s) sent earlier, still undecided", blocking=True))
        return items


async def build_plan(day: date, providers: Sequence[PlanProvider]) -> list[PlanItem]:
    items: list[PlanItem] = []
    for provider in providers:
        try:
            items.extend(await provider.items(day))
        except Exception as exc:  # one broken provider must not sink the brief
            log.exception("Plan provider %s failed", provider.agent)
            items.append(PlanItem(provider.agent, f"plan unavailable ({type(exc).__name__})", blocking=True))
    return items


class PlanStore:
    def __init__(self, sessions: async_sessionmaker) -> None:
        self._sessions = sessions

    async def save(self, day: date, items: list[PlanItem]) -> None:
        data = [asdict(i) for i in items]
        async with self._sessions() as session, session.begin():
            await session.execute(
                insert(DailyPlan)
                .values(day=day, items=data)
                .on_conflict_do_update(index_elements=[DailyPlan.day], set_={"items": data, "created_at": func.now()})
            )

    async def get(self, day: date) -> list[PlanItem] | None:
        async with self._sessions() as session:
            plan = await session.get(DailyPlan, day)
        return None if plan is None else [PlanItem(**i) for i in plan.items]


def format_plan(items: Sequence[PlanItem]) -> str:
    if not items:
        return "Nothing planned."
    lines = []
    for item in items:
        due = f" (by {item.due})" if item.due else ""
        mark = "❗" if item.blocking else "•"
        lines.append(f"{mark} {item.agent}: {item.summary}{due}")
    return "\n".join(lines)
