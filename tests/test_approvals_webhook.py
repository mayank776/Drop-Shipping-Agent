import json
from datetime import date

from foundation.approvals import APPROVAL_EVENT, ApprovalKind, ApprovalStatus, callback_data
from tests.conftest import OWNER, SECRET


def make_approval(approvals, instance_id="inst-1"):
    return approvals.request(
        ApprovalKind.PRICE_CHANGE, "SKU-12 price ₹499 → ₹549", ["Margin 18% → 24%"], "Catalog", instance_id
    )


def callback(approval_id, approve=True, chat_id=OWNER, from_id=OWNER, message_id=101):
    return json.dumps(
        {
            "update_id": 1,
            "callback_query": {
                "id": "cb1",
                "from": {"id": from_id},
                "message": {"message_id": message_id, "chat": {"id": chat_id}},
                "data": callback_data(approval_id, approve),
            },
        }
    ).encode()


def test_request_sends_buttons_and_stores_message_id(approvals, tg_api):
    a = make_approval(approvals)
    method, payload = tg_api.calls[0]
    assert method == "sendMessage" and payload["chat_id"] == OWNER
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["Approve", "Reject"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)
    assert approvals.get(a.id).message_id == 101


def test_wrong_secret_rejected(webhook, approvals, tg_api):
    a = make_approval(approvals)
    assert webhook.handle(None, callback(a.id)).status == 401
    assert webhook.handle("wrong", callback(a.id)).status == 401
    assert approvals.get(a.id).status is ApprovalStatus.PENDING


def test_approve_records_edits_and_raises_event(webhook, approvals, tg_api, audit):
    a = make_approval(approvals)
    result = webhook.handle(SECRET, callback(a.id, approve=True))
    assert result.status == 200
    assert result.event.instance_id == "inst-1"
    assert result.event.name == APPROVAL_EVENT
    assert result.event.data == {"approval_id": a.id, "approved": True}

    stored = approvals.get(a.id)
    assert stored.status is ApprovalStatus.APPROVED
    assert tg_api.methods() == ["sendMessage", "answerCallbackQuery", "editMessageText"]
    edited = tg_api.calls[-1][1]
    assert "✅ Approved 30 Sep 12:00 IST" in edited["text"] and "reply_markup" not in edited
    assert [e.action for e in audit.for_day(date(2026, 9, 30))][-1] == "approval.approved"


def test_second_tap_does_not_flip_decision(webhook, approvals, tg_api):
    a = make_approval(approvals)
    webhook.handle(SECRET, callback(a.id, approve=False))
    result = webhook.handle(SECRET, callback(a.id, approve=True))
    assert result.event is None
    assert approvals.get(a.id).status is ApprovalStatus.REJECTED
    assert tg_api.calls[-1] == ("answerCallbackQuery", {"callback_query_id": "cb1", "text": "Already rejected"})


def test_non_owner_ignored(webhook, approvals, tg_api):
    a = make_approval(approvals)
    for chat_id, from_id in [(999, 999), (OWNER, 999), (999, OWNER)]:
        result = webhook.handle(SECRET, callback(a.id, chat_id=chat_id, from_id=from_id))
        assert result.status == 200 and result.event is None
    assert approvals.get(a.id).status is ApprovalStatus.PENDING
    assert tg_api.methods() == ["sendMessage"]


def test_unknown_approval_and_bad_data(webhook, tg_api):
    webhook.handle(SECRET, callback("deadbeef"))
    assert tg_api.calls[-1][1]["text"] == "Unknown approval"
    body = json.loads(callback("x"))
    body["callback_query"]["data"] = "evil"
    webhook.handle(SECRET, json.dumps(body).encode())
    assert tg_api.calls[-1][1]["text"] == "Unknown action"


def test_telegram_failure_after_decision_keeps_decision(webhook, approvals, tg_api):
    a = make_approval(approvals)
    tg_api.fail = {"answerCallbackQuery", "editMessageText"}
    result = webhook.handle(SECRET, callback(a.id))
    assert result.status == 200 and result.event is not None
    assert approvals.get(a.id).status is ApprovalStatus.APPROVED


def test_no_event_without_instance(webhook, approvals):
    a = make_approval(approvals, instance_id=None)
    assert webhook.handle(SECRET, callback(a.id)).event is None


def test_owner_message_gets_liveness_reply_stranger_does_not(webhook, tg_api):
    msg = lambda cid: json.dumps({"update_id": 2, "message": {"chat": {"id": cid}, "from": {"id": cid}, "text": "/start"}}).encode()
    webhook.handle(SECRET, msg(999))
    assert tg_api.calls == []
    webhook.handle(SECRET, msg(OWNER))
    assert tg_api.calls[0][0] == "sendMessage" and tg_api.calls[0][1]["chat_id"] == OWNER


def test_malformed_body(webhook):
    assert webhook.handle(SECRET, b"{not json").status == 400
    assert webhook.handle(SECRET, b"[]").status == 400
