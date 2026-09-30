from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from dropship.llm import LLM, LLMValidationError, Provider, build_providers, forbid_patterns, in_range, max_length, validate_output


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str
    confidence: float


RULES = [
    max_length("body", 50),
    in_range("confidence", 0, 1),
    forbid_patterns("body", [r"\brefund(ed)?\b"], "must not promise refunds"),
]
GOOD = '{"body": "Your order ships today.", "confidence": 0.9}'
PROMISES_REFUND = '{"body": "We have refunded you.", "confidence": 0.5}'


def test_valid_output():
    value, errors = validate_output(GOOD, Reply, RULES)
    assert errors == [] and value.body == "Your order ships today."


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, "empty output"),
        ("not json", "Invalid JSON"),
        ('{"body": "hi"}', "confidence: Field required"),
        ('{"body": "hi", "confidence": 0.5, "extra": 1}', "extra"),
        (PROMISES_REFUND, "must not promise refunds"),
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

    value, errors = validate_output(GOOD, Reply, [broken])
    assert value is None and "crashed" in errors[0]


class FakeProviderAPI:
    """Stands in for AsyncOpenAI.chat.completions; each reply is (content, finish_reason) or an exception."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content, finish = reply
        message = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


class RecordingAudit:
    def __init__(self):
        self.entries = []

    async def record(self, actor, action, subject="", details=None, correlation_id=None):
        self.entries.append((actor, action, subject, details or {}))


def outage():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))


async def ask(llm):
    return await llm.decide(agent="Support", task="reply", system="s", user="u", schema=Reply, rules=RULES)


async def test_retry_with_feedback_then_success():
    primary = FakeProviderAPI([(PROMISES_REFUND, "stop"), (GOOD, "stop")])
    audit = RecordingAudit()
    llm = LLM([Provider("openai", primary, "m1")], audit)
    assert (await ask(llm)).confidence == 0.9
    assert "must not promise refunds" in primary.requests[1]["messages"][-1]["content"]
    assert primary.requests[0]["response_format"]["type"] == "json_schema"
    assert [e[3]["ok"] for e in audit.entries] == [False, True]


async def test_falls_back_to_ollama_on_outage():
    primary = FakeProviderAPI([outage()])
    fallback = FakeProviderAPI([(GOOD, "stop")])
    audit = RecordingAudit()
    llm = LLM([Provider("openai", primary, "m1"), Provider("ollama", fallback, "qwen")], audit)
    assert (await ask(llm)).body.startswith("Your order")
    assert [(e[3]["provider"], e[3]["ok"]) for e in audit.entries] == [("openai", False), ("ollama", True)]
    # The fallback starts fresh, without the primary's conversation.
    assert len(fallback.requests[0]["messages"]) == 2


async def test_falls_back_after_repeated_invalid_output():
    primary = FakeProviderAPI([("nope", "stop"), (PROMISES_REFUND, "stop")])
    fallback = FakeProviderAPI([(GOOD, "stop")])
    llm = LLM([Provider("openai", primary, "m1"), Provider("ollama", fallback, "qwen")], RecordingAudit())
    assert (await ask(llm)).confidence == 0.9


async def test_raises_when_nothing_valid():
    primary = FakeProviderAPI([("nope", "stop"), (GOOD, "length")])
    fallback = FakeProviderAPI([outage()])
    llm = LLM([Provider("openai", primary, "m1"), Provider("ollama", fallback, "qwen")], RecordingAudit())
    with pytest.raises(LLMValidationError) as exc:
        await ask(llm)
    assert "ollama unavailable" in exc.value.errors[0]


def test_build_providers_order_and_skips():
    settings = SimpleNamespace(
        openai_api_key=SecretStr("sk-test"), openai_model="gpt-x",
        ollama_base_url="http://ollama:11434/", ollama_model="qwen2.5:7b-instruct",
    )
    providers = build_providers(settings)
    assert [(p.name, p.model) for p in providers] == [("openai", "gpt-x"), ("ollama", "qwen2.5:7b-instruct")]
    assert str(providers[1].client.base_url).rstrip("/") == "http://ollama:11434/v1"

    settings.openai_api_key = None
    assert [p.name for p in build_providers(settings)] == ["ollama"]
