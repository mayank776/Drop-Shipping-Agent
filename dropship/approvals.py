"""Approvals as Temporal workflows (D3, D13, D14, D17, D21).

``ApprovalWorkflow`` stores the request, sends it to Mayank on Telegram with Approve/Reject
buttons, waits for the ``decide`` signal, records the first decision (later ones are ignored),
audits it and edits the message to show the outcome.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import update
    from sqlalchemy.dialects.postgresql import insert
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from .audit import AuditLog
    from .models import Approval
    from .telegram.client import TelegramClient

KINDS = ("price_change", "product_change", "refund", "discount", "ad_spend", "payment", "resume")
CALLBACK_PREFIX = "ap"
IST = timezone(timedelta(hours=5, minutes=30))


def workflow_id(approval_id: str) -> str:
    return f"approval-{approval_id}"


def new_approval_id() -> str:
    return secrets.token_hex(16)


def callback_data(approval_id: str, approve: bool) -> str:
    return f"{CALLBACK_PREFIX}:{approval_id}:{'a' if approve else 'r'}"


def parse_callback_data(data: str) -> tuple[str, bool] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[2] not in ("a", "r") or not parts[1]:
        return None
    return parts[1], parts[2] == "a"


@dataclass
class ApprovalRequest:
    approval_id: str
    kind: str
    title: str
    requested_by: str
    lines: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown approval kind {self.kind!r}")


@dataclass
class DecisionSignal:
    approved: bool
    actor: str = "mayank"


@dataclass
class ApprovalResult:
    approval_id: str
    approved: bool
    actor: str
    decided_at: str


@dataclass
class OutcomeArgs:
    request: ApprovalRequest
    result: ApprovalResult


def message_text(request: ApprovalRequest, result: ApprovalResult | None = None) -> str:
    header = f"[{request.kind.replace('_', ' ').upper()}] from {request.requested_by}"
    text = "\n".join([header, request.title, *request.lines, f"ref {request.approval_id[:8]}"])
    if result is None:
        return text
    when = datetime.fromisoformat(result.decided_at).astimezone(IST).strftime("%d %b %H:%M IST")
    return f"{text}\n\n{'✅ Approved' if result.approved else '❌ Rejected'} {when}"


class ApprovalActivities:
    def __init__(self, sessions: async_sessionmaker, audit: AuditLog, telegram: TelegramClient, owner_chat_id: int) -> None:
        self._sessions = sessions
        self._audit = audit
        self._telegram = telegram
        self._owner = owner_chat_id

    @activity.defn(name="approval.create")
    async def create(self, request: ApprovalRequest) -> None:
        async with self._sessions() as session, session.begin():
            inserted = await session.execute(
                insert(Approval)
                .values(
                    id=request.approval_id,
                    kind=request.kind,
                    title=request.title,
                    lines=request.lines,
                    requested_by=request.requested_by,
                )
                .on_conflict_do_nothing()
                .returning(Approval.id)
            )
            is_new = inserted.first() is not None
        if is_new:  # retries don't duplicate the audit entry
            await self._audit.record(
                request.requested_by, "approval.requested", request.approval_id, {"kind": request.kind, "title": request.title}
            )

    @activity.defn(name="approval.send")
    async def send(self, request: ApprovalRequest) -> None:
        async with self._sessions() as session:
            approval = await session.get(Approval, request.approval_id)
            if approval is None or approval.telegram_message_id is not None:
                return
        message_id = await self._telegram.send_message(
            self._owner,
            message_text(request),
            [("Approve", callback_data(request.approval_id, True)), ("Reject", callback_data(request.approval_id, False))],
        )
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(Approval).where(Approval.id == request.approval_id).values(telegram_message_id=message_id)
            )

    @activity.defn(name="approval.record")
    async def record(self, args: OutcomeArgs) -> None:
        result = args.result
        status = "approved" if result.approved else "rejected"
        async with self._sessions() as session, session.begin():
            changed = await session.execute(
                update(Approval)
                .where(Approval.id == result.approval_id, Approval.status == "pending")
                .values(status=status, decided_at=datetime.fromisoformat(result.decided_at), decided_by=result.actor)
                .returning(Approval.id)
            )
            is_new = changed.first() is not None
        if is_new:
            await self._audit.record(
                result.actor, f"approval.{status}", result.approval_id, {"kind": args.request.kind, "title": args.request.title}
            )

    @activity.defn(name="approval.show_outcome")
    async def show_outcome(self, args: OutcomeArgs) -> None:
        async with self._sessions() as session:
            approval = await session.get(Approval, args.result.approval_id)
        if approval is not None and approval.telegram_message_id is not None:
            await self._telegram.edit_message_text(
                self._owner, approval.telegram_message_id, message_text(args.request, args.result)
            )

    def all(self) -> list:
        return [self.create, self.send, self.record, self.show_outcome]


_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), maximum_interval=timedelta(minutes=5))
_TIMEOUT = timedelta(seconds=30)


@workflow.defn(name="Approval")
class ApprovalWorkflow:
    def __init__(self) -> None:
        self._decision: DecisionSignal | None = None

    @workflow.run
    async def run(self, request: ApprovalRequest) -> ApprovalResult:
        opts = {"start_to_close_timeout": _TIMEOUT, "retry_policy": _RETRY}
        await workflow.execute_activity("approval.create", request, **opts)
        await workflow.execute_activity("approval.send", request, **opts)

        await workflow.wait_condition(lambda: self._decision is not None)
        decision = self._decision
        assert decision is not None
        result = ApprovalResult(
            approval_id=request.approval_id,
            approved=decision.approved,
            actor=decision.actor,
            decided_at=workflow.now().isoformat(),
        )
        await workflow.execute_activity("approval.record", OutcomeArgs(request, result), **opts)
        await workflow.execute_activity("approval.show_outcome", OutcomeArgs(request, result), **opts)
        return result

    @workflow.signal
    def decide(self, decision: DecisionSignal) -> None:
        if self._decision is None:  # first decision wins
            self._decision = decision

    @workflow.query
    def status(self) -> str:
        if self._decision is None:
            return "pending"
        return "approved" if self._decision.approved else "rejected"
