"""The deterministic rubric (P2): evidence rules decide a verdict; the AI judges only open cases."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from signal_core.brief_columns import EvidenceRule, evidence_rules
from signal_core.rubric import RubricClaim, evaluate_rubric, source_of

NOW = datetime(2026, 11, 19, tzinfo=UTC)
FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flytbase_columns.json").read_text(encoding="utf-8")
)
RULES = evidence_rules(FIXTURE)


def _claim(
    cid: str,
    key: str,
    value: Any,
    *,
    quote: str | None = "a quote",
    days_old: int = 10,
    source: str = "news.example.com",
) -> RubricClaim:
    return RubricClaim(
        id=cid,
        key=key,
        value=value,
        quote=quote,
        confidence=90,
        observed_at=NOW - timedelta(days=days_old),
        source=source,
    )


def test_fixture_has_both_rules() -> None:
    assert [(r.predicate, r.kind) for r in RULES] == [
        ("drone_programme", "requires"),
        ("industry", "disqualifies"),
    ]


def test_required_met_confirms() -> None:
    out = evaluate_rubric(RULES, [_claim("a", "drone_programme", True)], NOW)
    assert out.value == "Confirmed"
    assert out.claim_ids == ["a"]
    required = out.rules[0]
    assert (required.met, required.touched, required.sources, required.claim_ids) == (
        True,
        True,
        1,
        ["a"],
    )
    assert required.as_dict() == {
        "predicate": "drone_programme",
        "kind": "requires",
        "op": "is_true",
        "met": True,
        "touched": True,
        "sources": 1,
        "claim_ids": ["a"],
    }
    assert out.rules[1].touched is False and out.rules[1].met is False


def test_disqualifier_wins_over_required() -> None:
    claims = [
        _claim("a", "drone_programme", True),
        _claim("b", "industry", "drone vendor", source="vendor.example"),
    ]
    out = evaluate_rubric(RULES, claims, NOW)
    assert out.value == "Contradicted"
    assert out.claim_ids == ["b"]


def test_no_rule_touched_unverified() -> None:
    out = evaluate_rubric(RULES, [_claim("a", "site_count", 40)], NOW)
    assert out.value == "Unverified"
    assert out.claim_ids == []
    assert not any(r.touched for r in out.rules)


def test_two_claims_same_host_count_once() -> None:
    rule = RULES[0].model_copy(update={"min_sources": 2})
    claims = [
        # Two records from one origin: the URL hosts differ only in case.
        _claim("a", "drone_programme", True, source=source_of("https://news.example.com/a", "r1")),
        _claim("b", "drone_programme", True, source=source_of("https://NEWS.example.com/b", "r2")),
    ]
    out = evaluate_rubric([rule], claims, NOW)
    assert out.value is None
    assert (out.rules[0].sources, out.rules[0].met, out.rules[0].claim_ids) == (
        1,
        False,
        ["a", "b"],
    )

    independent = [*claims[:1], _claim("c", "drone_programme", True, source="other.example")]
    assert evaluate_rubric([rule], independent, NOW).value == "Confirmed"


def test_source_is_the_lowercased_host_or_the_record_id() -> None:
    assert source_of("https://NEWS.Example.com:8443/a?b=1", "r1") == "news.example.com"
    assert source_of(None, "r1") == "r1"
    assert source_of("not a url", "r2") == "r2"


def test_claim_without_quote_does_not_satisfy() -> None:
    for quote in (None, "", "   "):
        out = evaluate_rubric(RULES, [_claim("a", "drone_programme", True, quote=quote)], NOW)
        assert out.value is None
        assert out.rules[0].touched and not out.rules[0].met


def test_too_old_does_not_satisfy() -> None:
    out = evaluate_rubric(RULES, [_claim("a", "drone_programme", True, days_old=800)], NOW)
    assert out.value is None
    assert evaluate_rubric(
        RULES, [_claim("a", "drone_programme", True, days_old=730)], NOW
    ).value == ("Confirmed")


def test_false_answer_is_open_not_confirmed() -> None:
    out = evaluate_rubric(RULES, [_claim("a", "drone_programme", False)], NOW)
    assert out.value is None


def test_one_of_casefold() -> None:
    claims = [_claim("a", "drone_programme", True), _claim("b", "industry", "Drone Vendor")]
    out = evaluate_rubric(RULES, claims, NOW)
    assert out.value == "Contradicted"
    assert out.claim_ids == ["b"]


def test_contains_and_at_least() -> None:
    contains = EvidenceRule(
        predicate="tech_stack", kind="requires", op="contains", value=["revit", "BIM"]
    )
    at_least = EvidenceRule(predicate="site_count", kind="requires", op="at_least", value=10)
    rules = [contains, at_least]

    good = [
        _claim("a", "tech_stack", "Uses Autodesk REVIT daily"),
        _claim("b", "site_count", 10),
    ]
    out = evaluate_rubric(rules, good, NOW)
    assert out.value == "Confirmed"
    assert out.claim_ids == ["a", "b"]

    too_few = [good[0], _claim("b", "site_count", 9)]
    assert evaluate_rubric(rules, too_few, NOW).value is None
    no_match = [_claim("a", "tech_stack", "uses SketchUp"), good[1]]
    assert evaluate_rubric(rules, no_match, NOW).value is None
    # A boolean is not a number, and a number is not text.
    assert evaluate_rubric([at_least], [_claim("b", "site_count", True)], NOW).value is None
    assert evaluate_rubric([contains], [_claim("a", "tech_stack", 5)], NOW).value is None


def test_present_and_is_false() -> None:
    present = EvidenceRule(predicate="hq_city", kind="requires", op="present")
    is_false = EvidenceRule(predicate="in_house_drones", kind="disqualifies", op="is_false")
    assert evaluate_rubric([present], [_claim("a", "hq_city", "Delft")], NOW).value == "Confirmed"
    out = evaluate_rubric([present, is_false], [_claim("b", "in_house_drones", False)], NOW)
    assert (out.value, out.claim_ids) == ("Contradicted", ["b"])


def test_no_requires_rule_never_confirms() -> None:
    disqualifies = [r for r in RULES if r.kind == "disqualifies"]
    out = evaluate_rubric(disqualifies, [_claim("a", "industry", "energy")], NOW)
    assert out.value is None  # touched, not disqualified, nothing required: open
