from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

from foundation.llm import LLMJudge, LLMValidationError, forbid_patterns, in_range, max_length, validate_output


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    confidence: float


RULES = [
    max_length("body", 50),
    in_range("confidence", 0, 1),
    forbid_patterns("body", [r"\brefund(ed)?\b"], "must not promise refunds"),
]


def test_valid_output():
    value, errors = validate_output('{"body": "Your order ships today.", "confidence": 0.9}', Reply, RULES)
    assert errors == [] and value.body == "Your order ships today."


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "empty output"),
        ("not json", "Invalid JSON"),
        ('{"body": "hi"}', "confidence: Field required"),
        ('{"body": "hi", "confidence": 0.5, "extra": 1}', "extra"),
        ('{"body": "We have refunded you.", "confidence": 0.5}', "must not promise refunds"),
        ('{"body": "ok", "confidence": 3}', "outside"),
        ('{"body": "' + "x" * 60 + '", "confidence": 0.5}', "longer than 50"),
    ],
)
def test_invalid_output(raw, expected):
    value, errors = validate_output(raw, Reply, RULES)
    assert value is None
    assert any(expected in e for e in errors), errors


def test_crashing_rule_fails_closed():
    def broken(_):
        raise RuntimeError("boom")

    value, errors = validate_output('{"body": "ok", "confidence": 0.5}', Reply, [broken])
    assert value is None and "crashed" in errors[0]


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        content, finish = self.replies.pop(0)
        message = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def judge(replies, audit):
    chat = FakeChat(replies)
    return LLMJudge(SimpleNamespace(chat=chat), "gpt", audit), chat


def ask(j):
    return j.decide(agent="Support", task="reply", system="s", user="u", schema=Reply, rules=RULES)


def test_retry_then_success(audit):
    j, chat = judge([('{"body": "refund done", "confidence": 0.5}', "stop"), ('{"body": "ok", "confidence": 0.5}', "stop")], audit)
    assert ask(j).body == "ok"
    retry_prompt = chat.requests[1]["messages"][-1]["content"]
    assert "must not promise refunds" in retry_prompt
    assert chat.requests[0]["response_format"]["type"] == "json_schema"
    events = audit.for_day(date(2026, 9, 30))
    assert [e.details["ok"] for e in events] == [False, True]


def test_gives_up_after_max_attempts(audit):
    j, _ = judge([("nope", "stop"), ('{"body": "x", "confidence": 0.5}', "length")], audit)
    with pytest.raises(LLMValidationError) as exc:
        ask(j)
    assert exc.value.errors == ["output truncated"]
