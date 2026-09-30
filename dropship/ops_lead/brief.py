"""The daily brief (D16): one summary message, then each queued approval as its own message.

A Temporal Schedule runs ``DailyBriefWorkflow`` every day at ``DAILY_BRIEF_TIME`` IST; ``/queue``
runs it on demand.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..approvals import IST, workflow_id
    from ..models import Approval
    from ..telegram.client import TelegramClient
    from .alerts import ICONS, AlertService
    from .plan import PlanItem, PlanProvider, PlanStore, build_plan, format_plan

TELEGRAM_LIMIT = 4_096
SCHEDULE_ID = "daily-brief"


@dataclass
class BriefInput:
    manual: bool = False


@dataclass
class QueuedApproval:
    id: str
    kind: str


@dataclass
class BriefAlert:
    id: int
    severity: str
    source: str
    title: str


@dataclass
class BriefData:
    day: str
    manual: bool
    queued: list[QueuedApproval] = field(default_factory=list)
    waiting: list[QueuedApproval] = field(default_factory=list)
    alerts: list[BriefAlert] = field(default_factory=list)
    plan: list[dict] = field(default_factory=list)


def ist_today() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def brief_text(data: BriefData) -> str:
    day = datetime.fromisoformat(data.day).strftime("%a %d %b")
    lines = [f"📋 Queue on request · {day}" if data.manual else f"☀️ Daily brief · {day}", ""]

    if data.queued or data.waiting:
        lines.append(f"Approvals: {len(data.queued)} to decide below" + (f", {len(data.waiting)} still waiting from earlier" if data.waiting else ""))
        if data.queued:
            kinds = Counter(q.kind.replace("_", " ") for q in data.queued)
            lines.append("  " + ", ".join(f"{k} ×{n}" for k, n in sorted(kinds.items())))
        if data.waiting:
            lines.append("  still waiting: " + ", ".join(f"ref {w.id[:8]} ({w.kind.replace('_', ' ')})" for w in data.waiting))
    else:
        lines.append("Approvals: nothing to decide.")

    if data.alerts:
        lines += ["", f"Alerts ({len(data.alerts)}):"]
        lines += [f"  {ICONS[a.severity]} {a.source}: {a.title}" for a in data.alerts]

    lines += ["", "Plan:", format_plan([PlanItem(**p) for p in data.plan])]
    text = "\n".join(lines)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 20] + "\n… (truncated)"


class BriefActivities:
    def __init__(
        self, sessions: async_sessionmaker, telegram: TelegramClient, alerts: AlertService, owner_chat_id: int,
        providers: list[PlanProvider],
    ) -> None:
        self._sessions = sessions
        self._telegram = telegram
        self._alerts = alerts
        self._owner = owner_chat_id
        self._providers = providers
        self._plans = PlanStore(sessions)

    @activity.defn(name="ops.brief.collect")
    async def collect(self, brief: BriefInput) -> BriefData:
        today = ist_today().date()
        plan = await build_plan(today, self._providers)
        await self._plans.save(today, plan)
        async with self._sessions() as session:
            pending = (
                await session.scalars(
                    select(Approval).where(Approval.status == "pending").order_by(Approval.created_at, Approval.id)
                )
            ).all()
        alerts = [a for a in await self._alerts.undelivered() if a.severity in ("critical", "warning")]
        return BriefData(
            day=today.isoformat(),
            manual=brief.manual,
            queued=[QueuedApproval(a.id, a.kind) for a in pending if a.released_at is None],
            waiting=[QueuedApproval(a.id, a.kind) for a in pending if a.released_at is not None],
            alerts=[BriefAlert(a.id, a.severity, a.source, a.title) for a in alerts],
            plan=[asdict(i) for i in plan],
        )

    @activity.defn(name="ops.brief.send")
    async def send(self, data: BriefData) -> None:
        await self._telegram.send_message(self._owner, brief_text(data))
        await self._alerts.mark_delivered([a.id for a in data.alerts])

    def all(self) -> list:
        return [self.collect, self.send]


_OPTS = {
    "start_to_close_timeout": timedelta(seconds=60),
    "retry_policy": RetryPolicy(initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=5)),
}


@workflow.defn(name="DailyBrief")
class DailyBriefWorkflow:
    @workflow.run
    async def run(self, brief: BriefInput) -> int:
        data: BriefData = await workflow.execute_activity("ops.brief.collect", brief, result_type=BriefData, **_OPTS)
        await workflow.execute_activity("ops.brief.send", data, **_OPTS)
        released = 0
        for queued in data.queued:  # after the summary, so the approvals arrive below it
            try:
                await workflow.get_external_workflow_handle(workflow_id(queued.id)).signal("release")
                released += 1
            except Exception:  # its workflow is gone (e.g. terminated); nothing to release
                workflow.logger.warning("Could not release approval %s", queued.id)
        return released
