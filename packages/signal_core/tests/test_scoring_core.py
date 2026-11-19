"""Pure scoring (M8) shared by live scoring and the edit preview (M1)."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from signal_core.scoring_core import (
    LEGACY_ROLES,
    ScoreInputs,
    predicate_roles,
    score_account,
    value_matches_icp,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flytbase_columns.json").read_text(encoding="utf8")
)

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def _claim(key: str, value: object, confidence: int = 80) -> tuple[object, ...]:
    # id, predicate key, value, confidence, observed_at, half-life days, connector
    return (f"c-{key}-{value}", key, value, confidence, NOW, None, "radar:r1")


def test_confirmed_fit_claim_scores_medium() -> None:
    result = score_account(
        {}, {}, ScoreInputs([_claim("vertical", "Manufacturing")], "Confirmed", 0, False), NOW
    )
    # no ICP list: fit is unknown (50). evidence 90, scale 0, timing 10 → 0.25 x 150 = 37.5 → Low
    assert (result.fit, result.evidence, result.scale, result.timing) == (50, 90, 0, 10)
    assert result.priority == 37.5
    assert result.band == "Low"
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


def _cfg(**icp: list[str]) -> dict[str, Any]:
    return {**FIXTURE, "icp": icp}


def _score(config: dict[str, Any], claims: list[tuple[object, ...]]) -> Any:
    return score_account(config, {}, ScoreInputs(claims, "Confirmed", 0, False), NOW)


def test_fit_matches_icp_list() -> None:
    config = _cfg(verticals=["energy"])
    assert _score(config, [_claim("industry", "Energy")]).fit == 100
    result = _score(config, [_claim("industry", "renewable energy")])
    assert result.fit == 100
    assert result.explanation["fit"]["matched"] == ["industry"]
    assert result.explanation["fit"]["missed"] == []


def test_fit_mismatch_scores_zero_and_gates() -> None:
    result = _score(_cfg(verticals=["energy"]), [_claim("industry", "drone vendor")])
    assert result.fit == 0
    assert result.explanation["fit"]["missed"] == ["industry"]
    assert result.gates == [{"reason": "outside_icp", "predicate": "industry"}]
    config = {
        "predicates": [
            {
                "key": "hq",
                "label": "HQ",
                "value_type": "text",
                "allowed_values": None,
                "extraction_hint": "",
                "decay_half_life_days": None,
                "role": "fit.geography",
            }
        ],
        "icp": {"geographies": ["Germany"]},
    }
    geo = _score(config, [_claim("hq", "France")])
    assert geo.band == "Excluded"
    assert geo.gates == [{"reason": "outside_icp", "predicate": "hq"}]


def test_size_band_numeric() -> None:
    assert value_matches_icp("fit.size", 450, ["201-1000"]) is True
    assert value_matches_icp("fit.size", 450, ["1000+"]) is False
    assert value_matches_icp("fit.size", 1500, ["1000+"]) is True
    assert value_matches_icp("fit.size", 450, ["mid-size"]) is None
    assert value_matches_icp("fit.size", "120 employees", ["51-200"]) is None
    config = {
        "predicates": [
            {
                "key": "headcount",
                "label": "Headcount",
                "value_type": "number",
                "allowed_values": None,
                "extraction_hint": "",
                "decay_half_life_days": None,
                "role": "fit.size",
            }
        ],
        "icp": {"size_bands": ["mid-size"]},
    }
    result = _score(config, [_claim("headcount", 450)])
    assert result.fit == 50
    assert result.explanation["fit"]["matched"] == []
    assert result.explanation["fit"]["missed"] == []


def test_scale_reads_role() -> None:
    result = _score(FIXTURE, [_claim("site_count", 30)])
    assert result.scale == int(math.log1p(30) * 10)
    assert _score(FIXTURE, [_claim("site_count", "many")]).scale == 0


def test_timing_counts_signal_only() -> None:
    old = NOW - timedelta(days=400)
    claims = [
        ("c-old", "industry", "energy", 80, NOW, None, "radar:r1"),
        ("c-recent", "drone_programme", True, 80, NOW, None, "radar:r1"),
        ("c-old2", "drone_programme", True, 80, old, None, "radar:r1"),
    ]
    assert _score(FIXTURE, claims).timing == 10


def test_legacy_config_unchanged_keys() -> None:
    assert predicate_roles({"predicates": ["vertical", "sites"]}) == LEGACY_ROLES
    assert LEGACY_ROLES["sites"] == "scale"
    config = {"predicates": ["vertical"], "icp": {"verticals": ["manufacturing"]}}
    result = _score(config, [_claim("vertical", "Manufacturing"), _claim("sites", 4)])
    assert result.fit == 100
    assert result.scale == int(math.log1p(4) * 10)
    assert result.timing == 20
    assert ("vertical", "Manufacturing") in result.labels
