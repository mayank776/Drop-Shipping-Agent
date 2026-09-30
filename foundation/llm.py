"""LLM calls at judgment points, with every output validated (D18).

Agents never use raw model output. ``LLMJudge.decide`` asks for JSON matching a Pydantic
schema, validates it against the schema and the caller's business rules, retries once with
the errors fed back, and otherwise raises ``LLMValidationError`` so the agent escalates.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from .audit import AuditLog

T = TypeVar("T", bound=BaseModel)

# A rule inspects a schema-valid object and returns human-readable problems (empty = pass).
Rule = Callable[[Any], list[str]]

AUDIT_OUTPUT_CHARS = 4_000


class LLMValidationError(RuntimeError):
    def __init__(self, task: str, errors: Sequence[str]) -> None:
        super().__init__(f"LLM output for {task!r} failed validation: {'; '.join(errors)}")
        self.task = task
        self.errors = list(errors)


def validate_output(raw: str | None, schema: type[T], rules: Iterable[Rule] = ()) -> tuple[T | None, list[str]]:
    """Return ``(value, [])`` if valid, else ``(None, errors)``."""
    if raw is None or not raw.strip():
        return None, ["empty output"]
    try:
        value = schema.model_validate_json(raw)
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
        ]
    errors: list[str] = []
    for rule in rules:
        try:
            errors.extend(rule(value))
        except Exception as exc:  # a crashing rule must fail closed
            errors.append(f"rule {getattr(rule, '__name__', rule)!s} crashed: {exc}")
    return (None, errors) if errors else (value, [])


# --- Reusable rules ---------------------------------------------------------------------


def max_length(field: str, limit: int) -> Rule:
    def rule(value: Any) -> list[str]:
        text = getattr(value, field)
        return [f"{field}: longer than {limit} characters"] if len(text) > limit else []

    rule.__name__ = f"max_length({field})"
    return rule


def in_range(field: str, low: float, high: float) -> Rule:
    def rule(value: Any) -> list[str]:
        number = getattr(value, field)
        return [] if low <= number <= high else [f"{field}: {number} outside [{low}, {high}]"]

    rule.__name__ = f"in_range({field})"
    return rule


def forbid_patterns(field: str, patterns: Iterable[str], reason: str) -> Rule:
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]

    def rule(value: Any) -> list[str]:
        text = getattr(value, field)
        return [f"{field}: {reason}"] if any(p.search(text) for p in compiled) else []

    rule.__name__ = f"forbid_patterns({field})"
    return rule


# --- Client -----------------------------------------------------------------------------


class LLMJudge:
    def __init__(self, client: Any, deployment: str, audit: AuditLog, max_attempts: int = 2) -> None:
        """``client`` is an ``openai.AzureOpenAI`` (or anything with the same chat API)."""
        self._client = client
        self._deployment = deployment
        self._audit = audit
        self._max_attempts = max_attempts

    def decide(
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
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
        }
        errors: list[str] = []
        for attempt in range(1, self._max_attempts + 1):
            completion = self._client.chat.completions.create(
                model=self._deployment, messages=messages, response_format=response_format
            )
            choice = completion.choices[0]
            raw = choice.message.content
            if choice.finish_reason == "length":
                value, errors = None, ["output truncated"]
            elif getattr(choice.message, "refusal", None):
                value, errors = None, [f"model refused: {choice.message.refusal}"]
            else:
                value, errors = validate_output(raw, schema, rules)

            details: dict[str, Any] = {"attempt": attempt, "ok": value is not None}
            if value is not None:
                details["output"] = value.model_dump_json()[:AUDIT_OUTPUT_CHARS]
            else:
                details["errors"] = errors
            self._audit.record(agent, "llm.decide", task, details, correlation_id)

            if value is not None:
                return value
            messages.append({"role": "assistant", "content": raw or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "Your answer was rejected:\n- "
                    + "\n- ".join(errors)
                    + "\nReply again with JSON that fixes every problem.",
                }
            )
        raise LLMValidationError(task, errors)
