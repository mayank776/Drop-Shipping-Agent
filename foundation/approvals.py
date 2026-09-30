"""Approval requests to Mayank over Telegram (D3, D13, D14, D17, D21).

An approval is stored, sent as a Telegram message with Approve/Reject buttons, and decided
exactly once. If it carries a Durable Functions instance ID, the webhook raises
``APPROVAL_EVENT`` on that instance when it's decided.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Sequence

from .audit import AuditLog
from .store import Conflict, Store
from .telegram import TelegramClient

TABLE = "approvals"
PARTITION = "approval"
APPROVAL_EVENT = "ApprovalDecision"
CALLBACK_PREFIX = "ap"
IST = timezone(timedelta(hours=5, minutes=30))


class ApprovalKind(str, Enum):
    PRICE_CHANGE = "price_change"
    PRODUCT_CHANGE = "product_change"
    REFUND = "refund"
    DISCOUNT = "discount"
    AD_SPEND = "ad_spend"
    PAYMENT = "payment"
    RESUME = "resume"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Approval:
    id: str
    kind: ApprovalKind
    title: str
    lines: list[str]
    requested_by: str
    created_at: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    instance_id: str | None = None
    message_id: int | None = None
    decided_at: str | None = None

    def to_data(self) -> dict:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_data(cls, data: dict) -> Approval:
        return cls(**{**data, "kind": ApprovalKind(data["kind"]), "status": ApprovalStatus(data["status"])})

    def message_text(self) -> str:
        header = f"[{self.kind.value.replace('_', ' ').upper()}] from {self.requested_by}"
        text = "\n".join([header, self.title, *self.lines, f"ref {self.id[:8]}"])
        if self.status is ApprovalStatus.PENDING or not self.decided_at:
            return text
        when = datetime.fromisoformat(self.decided_at).astimezone(IST).strftime("%d %b %H:%M IST")
        mark = "✅ Approved" if self.status is ApprovalStatus.APPROVED else "❌ Rejected"
        return f"{text}\n\n{mark} {when}"


@dataclass(frozen=True)
class Decision:
    approval: Approval
    changed: bool  # False if it was already decided


def callback_data(approval_id: str, approve: bool) -> str:
    return f"{CALLBACK_PREFIX}:{approval_id}:{'a' if approve else 'r'}"


def parse_callback_data(data: str) -> tuple[str, bool] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX or parts[2] not in ("a", "r") or not parts[1]:
        return None
    return parts[1], parts[2] == "a"


class ApprovalService:
    def __init__(
        self,
        store: Store,
        audit: AuditLog,
        telegram: TelegramClient,
        owner_chat_id: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._telegram = telegram
        self._owner = owner_chat_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get(self, approval_id: str) -> Approval:
        return Approval.from_data(self._store.get(TABLE, PARTITION, approval_id).data)

    def request(
        self,
        kind: ApprovalKind,
        title: str,
        lines: Sequence[str],
        requested_by: str,
        instance_id: str | None = None,
    ) -> Approval:
        approval = Approval(
            id=secrets.token_hex(16),
            kind=kind,
            title=title,
            lines=list(lines),
            requested_by=requested_by,
            created_at=self._clock().isoformat(),
            instance_id=instance_id,
        )
        record = self._store.insert(TABLE, PARTITION, approval.id, approval.to_data())
        self._audit.record(requested_by, "approval.requested", approval.id, {"kind": kind.value, "title": title})

        message_id = self._telegram.send_message(
            self._owner,
            approval.message_text(),
            [("Approve", callback_data(approval.id, True)), ("Reject", callback_data(approval.id, False))],
        )
        approval = replace(approval, message_id=message_id)
        self._store.replace(TABLE, record, approval.to_data())
        return approval

    def decide(self, approval_id: str, approve: bool, actor: str = "mayank") -> Decision:
        for _ in range(3):
            record = self._store.get(TABLE, PARTITION, approval_id)
            approval = Approval.from_data(record.data)
            if approval.status is not ApprovalStatus.PENDING:
                return Decision(approval, changed=False)
            decided = replace(
                approval,
                status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
                decided_at=self._clock().isoformat(),
            )
            try:
                self._store.replace(TABLE, record, decided.to_data())
            except Conflict:
                continue  # changed under us; re-read and re-check
            self._audit.record(
                actor, f"approval.{decided.status.value}", approval_id, {"kind": decided.kind.value, "title": decided.title}
            )
            return Decision(decided, changed=True)
        raise Conflict(f"approval {approval_id} kept changing; giving up")
