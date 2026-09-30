"""Every LLM judgment goes through ``LLM.decide`` (D18).

Providers are tried in order (OpenAI, then the Ollama fallback). On each provider the output is
validated; a failure is retried once with the errors fed back. A provider error (outage, rate
limit, timeout) moves straight to the next provider. If nothing produces valid output,
``LLMValidationError`` is raised and the calling agent must escalate instead of acting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence, TypeVar

import openai
from pydantic import BaseModel

from .validator import Rule, validate_output

T = TypeVar("T", bound=BaseModel)

AUDIT_OUTPUT_CHARS = 4_000


class AuditSink(Protocol):
    async def record(
        self, actor: str, action: str, subject: str = "", details: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


class LLMValidationError(RuntimeError):
    def __init__(self, task: str, errors: Sequence[str]) -> None:
        super().__init__(f"No valid LLM output for {task!r}: {'; '.join(errors)}")
        self.task = task
        self.errors = list(errors)


@dataclass(frozen=True)
class Provider:
    name: str
    client: Any  # openai.AsyncOpenAI or compatible
    model: str


class LLM:
    def __init__(self, providers: Sequence[Provider], audit: AuditSink, attempts_per_provider: int = 2) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = list(providers)
        self._audit = audit
        self._attempts = attempts_per_provider

    async def decide(
        self,
        *,
        agent: str,
        task: str,
        system: str,
        user: str,
        schema: type[T],
        rules: Iterable[Rule] = (),
        correlation_id: str | None = None,
    ) -> T:
        rules = list(rules)
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        errors: list[str] = []
        for provider in self._providers:
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            for attempt in range(1, self._attempts + 1):
                details: dict[str, Any] = {"provider": provider.name, "model": provider.model, "attempt": attempt}
                try:
                    completion = await provider.client.chat.completions.create(
                        model=provider.model, messages=messages, response_format=response_format
                    )
                except openai.APIError as exc:
                    errors = [f"{provider.name} unavailable: {type(exc).__name__}"]
                    await self._audit.record(agent, "llm.decide", task, {**details, "ok": False, "errors": errors}, correlation_id)
                    break  # next provider

                choice = completion.choices[0]
                raw = choice.message.content
                if choice.finish_reason == "length":
                    value, errors = None, ["output truncated"]
                elif getattr(choice.message, "refusal", None):
                    value, errors = None, [f"model refused: {choice.message.refusal}"]
                else:
                    value, errors = validate_output(raw, schema, rules)

                if value is not None:
                    output = value.model_dump_json()[:AUDIT_OUTPUT_CHARS]
                    await self._audit.record(agent, "llm.decide", task, {**details, "ok": True, "output": output}, correlation_id)
                    return value

                await self._audit.record(agent, "llm.decide", task, {**details, "ok": False, "errors": errors}, correlation_id)
                messages.append({"role": "assistant", "content": raw or ""})
                messages.append(
                    {
                        "role": "user",
                        "content": "Your answer was rejected:\n- " + "\n- ".join(errors)
                        + "\nReply again with JSON that fixes every problem.",
                    }
                )
        raise LLMValidationError(task, errors)


def build_providers(settings: Any) -> list[Provider]:
    """OpenAI first, Ollama second; either is skipped if not configured."""
    providers: list[Provider] = []
    if settings.openai_api_key and settings.openai_model:
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        providers.append(Provider("openai", _traced(client), settings.openai_model))
    if settings.ollama_base_url and settings.ollama_model:
        client = openai.AsyncOpenAI(base_url=settings.ollama_base_url.rstrip("/") + "/v1", api_key="ollama")
        providers.append(Provider("ollama", _traced(client), settings.ollama_model))
    return providers


def _traced(client: Any) -> Any:
    # LangSmith reads LANGSMITH_TRACING / LANGSMITH_API_KEY itself; wrapping is a no-op when off.
    from langsmith.wrappers import wrap_openai

    return wrap_openai(client)
