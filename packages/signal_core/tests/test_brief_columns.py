"""Column specs and evidence rules: cleaning AI output, reading configs."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from signal_core.brief_columns import (
    OPS_BY_TYPE,
    ROLES,
    ColumnSpec,
    EvidenceRule,
    clean_columns,
    column_specs,
    evidence_rules,
    extract_keys,
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


# ---------------------------------------------------------------------------
# P3: formula columns (mirror app.validate_brief_columns, migration 0039)
# ---------------------------------------------------------------------------


def _formula(
    key: str, formula: str | None, inputs: list[str] | None, **extra: Any
) -> dict[str, Any]:
    return {
        "key": key,
        "label": key.replace("_", " ").title(),
        "value_type": "number",
        "role": "info",
        "fill_method": "formula",
        "formula": formula,
        "inputs": inputs,
        **extra,
    }


def _keys(specs: list[ColumnSpec]) -> list[str]:
    return [s.key for s in specs]


def test_valid_formula_spec_is_kept() -> None:
    preds, rules = _fx()
    preds.append(_formula("sites_doubled", "site_count * 2", ["site_count"]))
    specs, kept, dropped = clean_columns(preds, rules)
    assert dropped == []
    spec = specs[-1]
    assert (spec.fill_method, spec.formula, spec.inputs) == (
        "formula",
        "site_count * 2",
        ["site_count"],
    )
    assert len(kept) == 2


def test_formula_reading_a_formula_is_kept() -> None:
    preds, _ = _fx()
    preds += [
        _formula("big_label", 'if(big_site, "big", "small")', ["big_site"], value_type="text"),
        _formula("big_site", "site_count >= 10", ["site_count"], value_type="boolean"),
    ]
    specs, _, dropped = clean_columns(preds, [])
    assert dropped == []
    assert _keys(specs)[-2:] == ["big_label", "big_site"]


def test_extract_is_the_default_fill_method() -> None:
    preds, _ = _fx()
    preds[0]["fill_method"] = None
    specs, _, dropped = clean_columns(preds, [])
    assert dropped == []
    assert {s.fill_method for s in specs} == {"extract"}
    assert all(s.formula is None for s in specs)


def test_extract_column_may_carry_empty_inputs() -> None:
    preds, _ = _fx()
    preds[0]["inputs"] = []
    preds[1]["inputs"] = None
    preds[2]["formula"] = None
    _, _, dropped = clean_columns(preds, [])
    assert dropped == []


def _dropped_for(extra: list[dict[str, Any]], rules: list[Any] | None = None) -> list[str]:
    preds, _ = _fx()
    _, _, dropped = clean_columns([*preds, *extra], rules or [])
    return dropped


ELEVEN = [f"c_{i:02d}" for i in range(11)]


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (
            {**_formula("x_col", "site_count", ["site_count"]), "fill_method": "manual"},
            "fill_method must be extract or formula",
        ),
        (_formula("x_col", "", ["site_count"]), "a formula column needs a formula of 1-500"),
        (_formula("x_col", "   ", ["site_count"]), "a formula column needs a formula of 1-500"),
        (
            _formula("x_col", "site_count + " + "1" * 490, ["site_count"]),
            "a formula column needs a formula of 1-500",
        ),
        (_formula("x_col", None, ["site_count"]), "a formula column needs a formula of 1-500"),
        (_formula("x_col", "site_count", []), "a formula reads 1-10 other columns"),
        (_formula("x_col", "site_count", None), "a formula reads 1-10 other columns"),
        (
            _formula("x_col", "site_count", ["site_count", "site_count"]),
            "a formula reads 1-10 other columns",
        ),
        (
            _formula("x_col", "concat(" + ", ".join(ELEVEN) + ")", ELEVEN),
            "a formula reads 1-10 other columns",
        ),
        (_formula("x_col", "x_col + 1", ["x_col"]), "a formula cannot read its own column"),
        (_formula("x_col", "site_count +", ["site_count"]), "formula: "),
        (_formula("x_col", "foo(site_count)", ["site_count"]), 'unknown function "foo"'),
        (
            _formula("x_col", "site_count + industry_size", ["site_count"]),
            "inputs must be exactly the columns the formula reads",
        ),
        (
            _formula("x_col", "site_count * 2", ["site_count", "industry"]),
            "inputs must be exactly the columns the formula reads",
        ),
        (
            _formula("x_col", "site_count", None, fill_method="extract"),
            "only a formula column has a formula",
        ),
        (
            _formula("x_col", None, ["site_count"], fill_method="extract"),
            "only a formula column has inputs",
        ),
    ],
)
def test_bad_formula_spec_dropped_with_reason(spec: dict[str, Any], reason: str) -> None:
    dropped = _dropped_for([spec])
    assert len(dropped) == 1, dropped
    assert dropped[0].startswith('column "x_col"'), dropped
    assert reason in dropped[0], dropped


def test_input_that_is_not_a_column_drops() -> None:
    dropped = _dropped_for([_formula("x_col", "ghost_col + 1", ["ghost_col"])])
    assert dropped == ['column "x_col": input "ghost_col" is not a column of this Brief']


def test_input_that_is_a_legacy_key_drops() -> None:
    preds, _ = _fx()
    specs, _, dropped = clean_columns(
        [*preds, "vertical", _formula("x_col", "lower(vertical)", ["vertical"], value_type="text")],
        [],
    )
    assert "x_col" not in _keys(specs)
    assert dropped == [
        'column "vertical": not a column object',
        'column "x_col": formula input "vertical" has no column spec; give it one first',
    ]


def test_formula_on_a_dropped_column_drops_too() -> None:
    preds, _ = _fx()
    preds[2]["key"] = "Bad Key"  # site_count drops
    preds.append(_formula("x_col", "site_count * 2", ["site_count"]))
    specs, _, dropped = clean_columns(preds, [])
    assert "x_col" not in _keys(specs)
    assert dropped[-1] == 'column "x_col": input "site_count" is not a column of this Brief'


def test_formula_reading_a_dropped_formula_drops_too() -> None:
    preds, _ = _fx()
    preds += [
        _formula("col_a", "foo(site_count)", ["site_count"]),  # fails to parse
        _formula("col_b", "col_a + 1", ["col_a"]),
    ]
    specs, _, dropped = clean_columns(preds, [])
    assert "col_b" not in _keys(specs)
    assert dropped[-1] == 'column "col_b": input "col_a" was dropped'


def test_cycle_dropped_with_named_cycle() -> None:
    preds, _ = _fx()
    preds += [
        _formula("col_a", "col_b + 1", ["col_b"]),
        _formula("col_b", "col_a + 1", ["col_a"]),
        _formula("col_c", "col_a * 2", ["col_a"]),  # reads the cycle: drops after it
        _formula("col_d", "site_count * 3", ["site_count"]),  # unrelated: kept
    ]
    specs, _, dropped = clean_columns(preds, [])
    assert _keys(specs) == ["drone_programme", "industry", "site_count", "col_d"]
    assert dropped == [
        "formula columns form a cycle: col_a → col_b → col_a",
        'column "col_c": input "col_a" was dropped',
    ]


def test_longer_cycle_is_named() -> None:
    dropped = _dropped_for(
        [
            _formula("col_a", "col_b", ["col_b"]),
            _formula("col_b", "col_c", ["col_c"]),
            _formula("col_c", "col_a", ["col_a"]),
        ]
    )
    assert dropped == ["formula columns form a cycle: col_a → col_b → col_c → col_a"]


def test_rule_on_formula_column_dropped() -> None:
    preds, rules = _fx()
    preds.append(_formula("big_site", "site_count >= 10", ["site_count"], value_type="boolean"))
    rule = {"predicate": "big_site", "kind": "requires", "op": "is_true"}
    specs, kept, dropped = clean_columns(preds, [*rules, rule])
    assert "big_site" in _keys(specs)
    assert len(kept) == 2
    assert dropped == ['rule on "big_site": an evidence rule cannot point at a formula column']
    assert len(evidence_rules({"predicates": preds, "evidence_rules": [*rules, rule]})) == 2


def test_extract_keys_skips_formula_columns() -> None:
    preds, _ = _fx()
    preds.append(_formula("sites_doubled", "site_count * 2", ["site_count"]))
    cfg = {"predicates": ["vertical", *preds]}
    assert extract_keys(cfg) == ["vertical", "drone_programme", "industry", "site_count"]
    assert "sites_doubled" in predicate_keys(cfg)
    assert extract_keys({}) == []
