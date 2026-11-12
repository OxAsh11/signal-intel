"""Pure scoring (M8) shared by live scoring and the edit preview (M1)."""

from __future__ import annotations

from datetime import UTC, datetime

from signal_core.scoring_core import ScoreInputs, score_account

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def _claim(key: str, value: object, confidence: int = 80) -> tuple[object, ...]:
    # id, predicate key, value, confidence, observed_at, half-life days, connector
    return (f"c-{key}-{value}", key, value, confidence, NOW, None, "radar:r1")


def test_confirmed_fit_claim_scores_medium() -> None:
    result = score_account(
        {}, {}, ScoreInputs([_claim("vertical", "Manufacturing")], "Confirmed", 0, False), NOW
    )
    # fit 80, evidence 90, scale 0, timing 10 → 0.25 x 180 = 45 → Medium
    assert (result.fit, result.evidence, result.scale, result.timing) == (80, 90, 0, 10)
    assert result.priority == 45.0
    assert result.band == "Medium"
    assert result.gates == []
    assert ("radar", "r1") in result.labels
    assert ("vertical", "Manufacturing") in result.labels


def test_geography_outside_icp_is_excluded() -> None:
    config = {"icp": {"geographies": ["Germany"]}}
    result = score_account(
        config, {}, ScoreInputs([_claim("geography", "France")], "Confirmed", 0, False), NOW
    )
    assert result.band == "Excluded"
    assert result.gates == [{"reason": "outside_icp", "predicate": "geography"}]


def test_icp_match_is_case_insensitive() -> None:
    config = {"icp": {"geographies": ["germany"]}}
    result = score_account(
        config, {}, ScoreInputs([_claim("geography", "Germany")], "Confirmed", 0, False), NOW
    )
    assert result.band != "Excluded"


def test_empty_icp_never_excludes() -> None:
    config = {"icp": {"geographies": []}}
    result = score_account(
        config, {}, ScoreInputs([_claim("geography", "France")], "Confirmed", 0, False), NOW
    )
    assert result.gates == []


def test_unknown_geography_never_excludes() -> None:
    config = {"icp": {"geographies": ["Germany"]}}
    result = score_account(
        config, {}, ScoreInputs([_claim("vertical", "Retail")], "Confirmed", 0, False), NOW
    )
    assert all(g["reason"] != "outside_icp" for g in result.gates)


def test_contradicted_gate_wins_over_icp() -> None:
    config = {"icp": {"geographies": ["Germany"]}}
    result = score_account(
        config, {}, ScoreInputs([_claim("geography", "France")], "Contradicted", 0, False), NOW
    )
    assert result.gates[0] == {"reason": "verdict_contradicted"}
    assert result.band == "Excluded"


def test_size_is_never_matched_as_text() -> None:
    # Size claims are free text ("120 employees") and bands are ranges ("51-200"): no exact match.
    config = {"icp": {"size_bands": ["51-200"]}}
    result = score_account(
        config, {}, ScoreInputs([_claim("size", "120 employees")], "Confirmed", 0, False), NOW
    )
    assert all(g["reason"] != "outside_icp" for g in result.gates)
