"""Edit tiers and cost estimates for the Brief edit preview (M1, S6)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from signal_core.modules.m01_preview import _counts, classify_edit, estimate_cost, over_budget
from signal_core.modules.m03_extraction import BACKFILL_CAP, BACKFILL_SCOPE_SQL

BASE = {
    "offer": "CAD",
    "hypothesis": "Hiring CAD engineers",
    "examples": {"positive": ["a", "b", "c"], "negative": ["d", "e"]},
    "sources": {"radar_types": ["job_postings"]},
}

PRICES = {
    ("claude-sonnet-5-5", "ai_input_tokens"): Decimal("2"),
    ("claude-sonnet-5-5", "ai_output_tokens"): Decimal("10"),
    ("claude-haiku-5-5", "ai_input_tokens"): Decimal("0.1"),
    ("claude-haiku-5-5", "ai_output_tokens"): Decimal("0.5"),
}
MODELS = {
    "judge": "claude-sonnet-5-5",
    "extract": "claude-sonnet-5-5",
    "prejudge": "claude-haiku-5-5",
}


def test_icp_change_is_narrow() -> None:
    assert classify_edit(BASE, {**BASE, "icp": {"geographies": ["DE"]}}) == (
        "Narrow",
        False,
        ["icp"],
    )


def test_examples_change_is_redefine_and_rejudges() -> None:
    new = {**BASE, "examples": {"positive": ["a", "b", "c", "f"], "negative": ["d", "e"]}}
    assert classify_edit(BASE, new) == ("Redefine", True, ["examples"])


def test_new_predicate_is_redefine_without_rejudging() -> None:
    assert classify_edit(BASE, {**BASE, "predicates": ["uses_tool_y"]}) == (
        "Redefine",
        False,
        ["predicates"],
    )


def test_highest_tier_wins() -> None:
    new = {
        **BASE,
        "icp": {"geographies": ["DE"]},
        "sources": {"radar_types": ["job_postings", "events"]},
    }
    assert classify_edit(BASE, new) == ("Expand", False, ["icp", "sources"])


def test_hypothesis_change_is_expand_and_rejudges() -> None:
    assert classify_edit(BASE, {**BASE, "hypothesis": "Opening new plants"}) == (
        "Expand",
        True,
        ["hypothesis"],
    )


def test_no_change() -> None:
    assert classify_edit(BASE, dict(BASE)) == (None, False, [])


def test_rejudging_cost_and_minutes() -> None:
    usd, minutes = estimate_cost(PRICES, MODELS, {"rejudge_verdicts": 100})
    assert usd == Decimal("0.7")
    assert minutes == 4


def test_expand_cost_and_minutes() -> None:
    usd, minutes = estimate_cost(PRICES, MODELS, {"new_radar_types": 1})
    assert usd == Decimal("2.225")
    assert minutes >= 60


def test_unknown_prices_cost_nothing() -> None:
    assert estimate_cost({}, MODELS, {"rejudge_verdicts": 10})[0] == Decimal(0)


def test_no_budget_is_never_over_budget() -> None:
    assert over_budget([], Decimal("1000")) is False


def test_over_budget_when_any_cap_would_be_passed() -> None:
    caps = [(Decimal("100"), Decimal("10")), (Decimal("20"), Decimal("15"))]
    assert over_budget(caps, Decimal("6")) is True
    assert over_budget(caps, Decimal("5")) is False


# ── Re-extraction count: the backfill scope (P2) ─────────────────────────────


class _CountCursor:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    async def execute(self, sql: str, args: Any = None) -> None:
        self._calls.append((sql, args))

    async def fetchone(self) -> tuple[int]:
        return (7,)

    async def __aenter__(self) -> _CountCursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _CountConn:
    def __init__(self, calls: list[tuple[str, Any]]) -> None:
        self._calls = calls

    def cursor(self) -> _CountCursor:
        return _CountCursor(self._calls)

    async def __aenter__(self) -> _CountConn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _CountPool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def connection(self) -> _CountConn:
        return _CountConn(self.calls)


def _spec(key: str) -> dict[str, Any]:
    return {"key": key, "label": key, "value_type": "number", "role": "scale"}


async def test_reextract_counts_the_backfill_scope() -> None:
    pool = _CountPool()
    ws, brief, version = uuid4(), uuid4(), uuid4()
    old = {"predicates": [_spec("site_count"), "vertical"]}
    new = {"predicates": [_spec("site_count"), "vertical", _spec("fleet_size")]}

    counts = await _counts(pool, ws, brief, version, old, new, False, ["predicates"])  # type: ignore[arg-type]

    assert counts["reextract_records"] == 7
    (sql, args) = pool.calls[-1]
    assert f"FROM ({BACKFILL_SCOPE_SQL}" in sql
    assert args == (str(ws), str(brief), BACKFILL_CAP)


async def test_reextract_not_counted_when_no_key_added() -> None:
    pool = _CountPool()
    old = {"predicates": [_spec("site_count")]}
    new = {"predicates": [_spec("site_count") | {"label": "Industrial sites"}]}

    counts = await _counts(pool, uuid4(), uuid4(), uuid4(), old, new, False, ["predicates"])  # type: ignore[arg-type]

    assert counts["reextract_records"] == 0
    assert pool.calls == []
