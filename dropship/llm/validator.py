"""Shared LLM output validator: schema first, then business rules. Fails closed."""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# A rule inspects a schema-valid object and returns human-readable problems (empty = pass).
Rule = Callable[[Any], list[str]]


def validate_output(raw: str | None, schema: type[T], rules: Iterable[Rule] = ()) -> tuple[T | None, list[str]]:
    """Return ``(value, [])`` if valid, else ``(None, errors)``."""
    if raw is None or not raw.strip():
        return None, ["empty output"]
    try:
        value = schema.model_validate_json(raw)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()]
    errors: list[str] = []
    for rule in rules:
        try:
            errors.extend(rule(value))
        except Exception as exc:  # a crashing rule must not let output through
            errors.append(f"rule {getattr(rule, '__name__', rule)!s} crashed: {exc}")
    return (None, errors) if errors else (value, [])


def max_length(field: str, limit: int) -> Rule:
    def rule(value: Any) -> list[str]:
        return [f"{field}: longer than {limit} characters"] if len(getattr(value, field)) > limit else []

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
