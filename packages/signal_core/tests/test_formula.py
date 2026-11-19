"""The formula language (D5): every parity case in packages/formula/cases.json, plus the Python API."""

import json
import math
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from signal_core.formula import (
    Binary,
    FormulaSyntaxError,
    FormulaTypeError,
    Num,
    Ref,
    check_result,
    evaluate,
    parse,
    references,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES: list[dict[str, Any]] = json.loads(
    (REPO_ROOT / "packages" / "formula" / "cases.json").read_text(encoding="utf-8")
)["cases"]


def _same(actual: object, expected: object) -> bool:
    """Numbers compare by value (2 == 2.0); a boolean never equals a number."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and actual == expected
    return type(actual) is type(expected) and actual == expected


def _run(case: dict[str, Any]) -> object:
    return evaluate(
        parse(case["expr"]),
        case["values"],
        case["system"],
        date.fromisoformat(case["today"]),
    )


def test_case_file_is_rich() -> None:
    assert len(CASES) >= 60
    assert len({c["name"] for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_parity_case(case: dict[str, Any]) -> None:
    expect = case["expect"]
    if isinstance(expect, dict):
        error = {"syntax": FormulaSyntaxError, "type": FormulaTypeError}[expect["error"]]
        with pytest.raises(error):
            _run(case)
        return
    actual = _run(case)
    assert _same(actual, expect), f"{case['expr']!r}: got {actual!r}, want {expect!r}"


def test_integers_stay_integers() -> None:
    v = evaluate(parse("1 + 2 * 3"), {}, {}, date(2026, 11, 19))
    assert v == 7 and isinstance(v, int)


def test_parse_returns_an_immutable_tree() -> None:
    node = parse("site_count + 1")
    assert node == Binary("+", Ref("site_count"), Num(1))
    with pytest.raises(AttributeError):
        node.op = "-"  # type: ignore[misc]


def test_references_sorted_distinct() -> None:
    node = parse('concat(zeta, alpha, if(alpha > 1, beta, zeta), @name, "gamma")')
    assert references(node) == ["alpha", "beta", "zeta"]
    assert references(parse("1 + 2")) == []


@pytest.mark.parametrize(
    ("expr", "position"),
    [
        ("1 + @x", 4),
        ("foo(1)", 0),
        ('"abc', 0),
        ("1 2", 2),
        ("", 0),
        ("(1 + 2", 6),
        ("a" * 501, 500),
        ("(" * 33 + "1" + ")" * 33, 32),
        ("1" + " + 1" * 100, 400),
        (r'"a\nb"', 2),
    ],
)
def test_syntax_error_position(expr: str, position: int) -> None:
    with pytest.raises(FormulaSyntaxError) as info:
        parse(expr)
    assert info.value.position == position
    assert str(info.value)


def test_errors_are_value_errors() -> None:
    assert issubclass(FormulaSyntaxError, ValueError)
    assert issubclass(FormulaTypeError, ValueError)


@pytest.mark.parametrize(
    ("value", "value_type", "allowed", "problem"),
    [
        (True, "boolean", None, None),
        (1, "boolean", None, "formula returned number, column is boolean"),
        (3, "number", None, None),
        (2.5, "number", None, None),
        (math.inf, "number", None, "formula returned non-finite number, column is number"),
        (math.nan, "number", None, "formula returned non-finite number, column is number"),
        (True, "number", None, "formula returned boolean, column is number"),
        ("x", "text", None, None),
        (4, "text", None, "formula returned number, column is text"),
        ("energy", "enum", ["energy", "mining"], None),
        (
            "farming",
            "enum",
            ["energy", "mining"],
            "formula returned text outside the allowed values, column is enum",
        ),
        (False, "enum", ["energy", "mining"], "formula returned boolean, column is enum"),
        ("2026-11-19", "date", None, None),
        ("2026-02-30", "date", None, "formula returned text, column is date"),
        ("19/11/2026", "date", None, "formula returned text, column is date"),
        (20261119, "date", None, "formula returned number, column is date"),
        (None, "number", None, None),
    ],
)
def test_check_result(
    value: Any, value_type: str, allowed: list[str] | None, problem: str | None
) -> None:
    assert check_result(value, value_type, allowed) == problem
