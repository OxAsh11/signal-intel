"""Edit tiers and cost estimates for the Brief edit preview (M1, S6)."""

from __future__ import annotations

from decimal import Decimal

from signal_core.modules.m01_preview import classify_edit, estimate_cost, over_budget

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
