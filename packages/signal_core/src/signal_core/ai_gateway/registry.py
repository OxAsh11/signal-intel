"""Predicate registry validation. Mirrors the database trigger `app.validate_claim`."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class Predicate:
    key: str
    value_type: str
    allowed_values: list[str] | None = None
    unit: str | None = None


def validate_claim_value(p: Predicate, value: object) -> str | None:
    """Return an error message when `value` does not fit the predicate, else None."""
    t = p.value_type
    if t == "boolean":
        return None if isinstance(value, bool) else _mismatch(p)
    if t == "number":
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        return None if ok else _mismatch(p)
    if t in {"text", "date", "enum", "entity_ref"} and not isinstance(value, str):
        return _mismatch(p)
    if not isinstance(value, str):
        return f"unknown value type {t} for predicate {p.key}"
    if t == "text":
        return None
    if t == "enum":
        if value in (p.allowed_values or []):
            return None
        return f"value {value!r} is not allowed for predicate {p.key}"
    if t == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return f"value {value!r} is not an ISO date (predicate {p.key})"
        return None
    if t == "entity_ref":
        try:
            UUID(value)
        except ValueError:
            return f"value {value!r} is not an entity id (predicate {p.key})"
        return None
    return f"unknown value type {t} for predicate {p.key}"


def _mismatch(p: Predicate) -> str:
    return f"claim value does not match predicate {p.key} ({p.value_type})"
