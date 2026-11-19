"""Column specs and evidence rules: cleaning AI output, reading configs."""

import copy
import json
from pathlib import Path
from typing import Any

from signal_core.brief_columns import (
    OPS_BY_TYPE,
    ROLES,
    ColumnSpec,
    EvidenceRule,
    clean_columns,
    column_specs,
    evidence_rules,
    predicate_keys,
)

FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "flytbase_columns.json").read_text(encoding="utf-8")
)


def _fx() -> tuple[list[Any], list[Any]]:
    f = copy.deepcopy(FIXTURE)
    return f["predicates"], f["evidence_rules"]


def test_clean_keeps_valid_fixture() -> None:
    preds, rules = _fx()
    specs, kept, dropped = clean_columns(preds, rules)
    assert len(specs) == 3
    assert len(kept) == 2
    assert dropped == []
    assert isinstance(specs[0], ColumnSpec)
    assert isinstance(kept[0], EvidenceRule)


def test_clean_drops_only_bad_column() -> None:
    preds, rules = _fx()
    preds.append({"key": "Bad Key", "label": "Bad", "value_type": "boolean", "role": "signal"})
    specs, kept, dropped = clean_columns(preds, rules)
    assert len(specs) == 3
    assert len(kept) == 2
    assert len(dropped) == 1
    assert "Bad Key" in dropped[0]


def test_enum_with_one_value_dropped() -> None:
    preds, _ = _fx()
    preds.append(
        {
            "key": "tier",
            "label": "Tier",
            "value_type": "enum",
            "allowed_values": ["a"],
            "role": "info",
        }
    )
    specs, _, dropped = clean_columns(preds, [])
    assert len(specs) == 3
    assert "tier" in dropped[0]


def test_rule_on_dropped_column_drops() -> None:
    preds, rules = _fx()
    rules.append({"predicate": "nonexistent", "kind": "requires", "op": "present"})
    _, kept, dropped = clean_columns(preds, rules)
    assert len(kept) == 2
    assert len(dropped) == 1
    assert "nonexistent" in dropped[0]


def test_rule_follows_its_dropped_column() -> None:
    preds, rules = _fx()
    preds[0] = {**preds[0], "key": "Bad Key"}
    _, kept, dropped = clean_columns(preds, rules)
    assert [r.predicate for r in kept] == ["industry"]
    assert len(dropped) == 2


def test_op_must_fit_type() -> None:
    preds, rules = _fx()
    rules.append({"predicate": "drone_programme", "kind": "requires", "op": "at_least", "value": 3})
    _, kept, dropped = clean_columns(preds, rules)
    assert len(kept) == 2
    assert len(dropped) == 1


def test_value_rules() -> None:
    preds, _ = _fx()
    bad = [
        {"predicate": "drone_programme", "kind": "requires", "op": "is_true", "value": True},
        {"predicate": "industry", "kind": "requires", "op": "one_of", "value": ["nope"]},
        {"predicate": "site_count", "kind": "requires", "op": "at_least", "value": "3"},
        {"predicate": "site_count", "kind": "requires", "op": "present", "min_sources": 9},
    ]
    _, kept, dropped = clean_columns(preds, bad)
    assert kept == []
    assert len(dropped) == 4


def test_defaults_and_nulls() -> None:
    preds, _ = _fx()
    rule = {"predicate": "site_count", "kind": "requires", "op": "present", "min_sources": None}
    del preds[0]["extraction_hint"]
    specs, kept, dropped = clean_columns(preds, [rule])
    assert dropped == []
    assert len(specs) == 3
    assert kept[0].min_sources == 1
    assert kept[0].max_age_days is None


def test_duplicates_after_first_dropped() -> None:
    preds, rules = _fx()
    dup = {**preds[0], "label": "Second"}
    specs, kept, dropped = clean_columns([*preds, dup], [*rules, rules[0]])
    assert len(specs) == 3
    assert specs[0].label != "Second"
    assert len(kept) == 2
    assert len(dropped) == 2


def test_caps() -> None:
    preds = [
        {"key": f"col_{i:02d}", "label": f"C{i}", "value_type": "boolean", "role": "info"}
        for i in range(15)
    ]
    rules = [{"predicate": f"col_{i:02d}", "kind": "requires", "op": "is_true"} for i in range(12)]
    specs, kept, dropped = clean_columns(preds, rules)
    assert len(specs) == 12
    assert len(kept) == 10
    assert len(dropped) == 3 + 2


def test_non_dict_items_dropped() -> None:
    specs, kept, dropped = clean_columns(["vertical", 3], ["x"])
    assert specs == []
    assert kept == []
    assert len(dropped) == 3


def test_predicate_keys_accepts_legacy_strings() -> None:
    preds, _ = _fx()
    assert predicate_keys({"predicates": ["vertical", preds[0]]}) == ["vertical", "drone_programme"]
    assert predicate_keys({}) == []


def test_column_specs_ignores_legacy_and_invalid() -> None:
    preds, _ = _fx()
    cfg = {"predicates": ["vertical", {"key": "X"}, preds[0], 5]}
    assert [s.key for s in column_specs(cfg)] == ["drone_programme"]


def test_evidence_rules_reads_config() -> None:
    preds, rules = _fx()
    cfg = {"predicates": preds, "evidence_rules": [*rules, {"predicate": "ghost", "op": "present"}]}
    assert len(evidence_rules(cfg)) == 2
    assert evidence_rules({"predicates": ["a"], "evidence_rules": None}) == []


def test_constants() -> None:
    assert "signal" in ROLES
    assert len(ROLES) == 7
    assert OPS_BY_TYPE["date"] == ("present",)


def test_key_with_trailing_newline_is_refused() -> None:
    """M1: the key must match the whole string; "ab\n" is not a key."""
    preds, _ = _fx()
    preds[0]["key"] = "drone_programme\n"
    specs, _, dropped = clean_columns(preds, [])
    assert "drone_programme" not in [s.key for s in specs]
    assert any("drone_programme" in d for d in dropped)


def test_clean_drops_a_column_whose_key_exists_with_another_type() -> None:
    """I1: a drafted column whose key the workspace holds as another type drops, with its rules."""
    preds, rules = _fx()
    specs, kept, dropped = clean_columns(
        preds, rules, existing_types={"industry": "text", "site_count": "number"}
    )
    assert "industry" not in [s.key for s in specs]
    assert "site_count" in [s.key for s in specs]
    assert all(r.predicate != "industry" for r in kept)
    assert any(
        'column "industry" already exists in this workspace as text; give it another key' in d
        for d in dropped
    )
