"""Tests for M01 Brief intake: BriefDraft model validation and run_brief_intake orchestration."""

import json
import re
from decimal import Decimal
from uuid import uuid4

import pytest
import structlog.testing
from pydantic import ValidationError

from signal_core.ai_gateway.gateway import Result
from signal_core.ai_gateway.outputs import BriefDraft
from signal_core.brief_columns import ColumnSpec
from signal_core.modules.m01_brief import BriefIntakeInput, run_brief_intake

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_RAW = {
    "offer": "CAD software",
    "hypothesis": "firms using SolidWorks that are actively hiring drafters",
    "examples": {
        "positive": ["Brightline Engineering", "Northgate Design", "Apex Structural"],
        "negative": ["Globex Consulting", "Initech IT Services"],
    },
    "sources": {"radar_types": ["job_postings"]},
}


def _make_inp() -> BriefIntakeInput:
    return BriefIntakeInput(
        brief_id=uuid4(),
        description="CAD firms that are hiring",
        org_id=uuid4(),
        workspace_id=uuid4(),
    )


# ---------------------------------------------------------------------------
# Mock pool
# ---------------------------------------------------------------------------


class _MockConn:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.args: list[tuple[str, tuple[str, ...]]] = []  # (fn_name, args)
        self.existing: list[tuple[str, str, str]] = []
        self.sqls: list[str] = []

    async def execute(self, sql: str, args: tuple[str, ...] = ()) -> None:
        m = re.search(r"app\.\w+", sql)
        fn = m.group(0) if m else sql
        self.calls.append(fn)
        self.args.append((fn, args))

    def cursor(self) -> "_MockConnCtx":
        return _MockConnCtx(_MockCursor(self))

    @property
    def last_call_fn(self) -> str | None:
        return self.calls[-1] if self.calls else None


class _MockCursor:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    async def execute(self, sql: str, args: object = None) -> None:
        self._conn.calls.append("cursor")
        self._conn.sqls.append(sql)

    async def fetchall(self) -> list[tuple[str, str, str]]:
        return self._conn.existing


class _MockConnCtx:
    def __init__(self, conn: "_MockConn | _MockCursor") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_MockConn | _MockCursor":
        return self._conn

    async def __aexit__(self, *_: object) -> None:
        pass


class MockPool:
    def __init__(self) -> None:
        self.conn = _MockConn()

    @property
    def last_call_fn(self) -> str | None:
        return self.conn.last_call_fn

    def args_for(self, fn: str) -> list[tuple[str, ...]]:
        return [a for f, a in self.conn.args if f == fn]

    def connection(self) -> _MockConnCtx:
        return _MockConnCtx(self.conn)


# ---------------------------------------------------------------------------
# Fake gateways
# ---------------------------------------------------------------------------


class _FakeGateway:
    """Returns a valid BriefDraft for any run() call."""

    async def run(
        self,
        capability: object,
        prompt: object,
        variables: object,
        output: object,
        ctx: object,
        **kwargs: object,
    ) -> Result[BriefDraft]:  # type: ignore[type-arg]
        draft = BriefDraft.model_validate(_VALID_RAW)
        return Result(value=draft, extractor_version_id=uuid4(), cost_usd=Decimal("0.001"))


_COLUMNS_RAW = {
    **_VALID_RAW,
    "name": "  Operators of industrial drone programmes ",
    "predicates": [
        {
            "key": "drone_programme",
            "label": "Runs a drone programme",
            "value_type": "boolean",
            "role": "signal",
        },
        {
            "key": "industry",
            "label": "Industry",
            "value_type": "enum",
            "allowed_values": ["energy", "mining"],
            "role": "fit.vertical",
        },
        {"key": "Bad Key", "label": "Bad", "value_type": "boolean", "role": "info"},
    ],
    "evidence_rules": [
        {"predicate": "drone_programme", "kind": "requires", "op": "is_true"},
        {"predicate": "ghost", "kind": "requires", "op": "present"},
    ],
    "assumptions": ["Energy and mining sites count as industrial."],
}


class _RawGateway:
    def __init__(self, raw: dict[str, object]) -> None:
        self.raw = raw
        self.variables: dict[str, str] = {}

    async def run(
        self,
        capability: object,
        prompt: object,
        variables: dict[str, str],
        output: object,
        ctx: object,
        **kwargs: object,
    ) -> Result[BriefDraft]:  # type: ignore[type-arg]
        self.variables = variables
        draft = BriefDraft.model_validate(self.raw)
        return Result(value=draft, extractor_version_id=uuid4(), cost_usd=Decimal("0.001"))


class _FailingGateway:
    """Always raises RuntimeError from run()."""

    async def run(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated gateway failure")


# ---------------------------------------------------------------------------
# BriefDraft model tests
# ---------------------------------------------------------------------------


def test_brief_draft_validates_with_valid_data() -> None:
    draft = BriefDraft.model_validate(_VALID_RAW)
    assert draft.offer == "CAD software"
    assert len(draft.examples.positive) == 3
    assert len(draft.examples.negative) == 2
    assert draft.sources.radar_types == ["job_postings"]


def test_brief_draft_rejects_fewer_than_3_positive_examples() -> None:
    raw = dict(_VALID_RAW)
    raw["examples"] = {"positive": ["A", "B"], "negative": ["X", "Y"]}
    with pytest.raises(ValidationError):
        BriefDraft.model_validate(raw)


def test_brief_draft_rejects_fewer_than_2_negative_examples() -> None:
    raw = dict(_VALID_RAW)
    raw["examples"] = {"positive": ["A", "B", "C"], "negative": ["X"]}
    with pytest.raises(ValidationError):
        BriefDraft.model_validate(raw)


def test_brief_draft_rejects_empty_radar_types() -> None:
    raw = dict(_VALID_RAW)
    raw["sources"] = {"radar_types": []}
    with pytest.raises(ValidationError):
        BriefDraft.model_validate(raw)


# ---------------------------------------------------------------------------
# run_brief_intake orchestration tests
# ---------------------------------------------------------------------------


async def test_run_brief_intake_calls_save_brief_draft_on_success() -> None:
    pool = MockPool()
    inp = _make_inp()
    await run_brief_intake(inp, _FakeGateway(), pool)  # type: ignore[arg-type]
    assert pool.last_call_fn == "app.save_brief_draft"


async def test_run_brief_intake_calls_fail_and_reraises_on_gateway_error() -> None:
    pool = MockPool()
    inp = _make_inp()
    with pytest.raises(RuntimeError, match="simulated gateway failure"):
        await run_brief_intake(inp, _FailingGateway(), pool)  # type: ignore[arg-type]
    assert pool.last_call_fn == "app.fail_brief_intake"


async def test_intake_saves_cleaned_columns_and_names_the_brief() -> None:
    pool = MockPool()
    pool.conn.existing = [("size", "Size", "number")]
    gw = _RawGateway(_COLUMNS_RAW)
    inp = _make_inp()
    with structlog.testing.capture_logs() as logs:
        await run_brief_intake(inp, gw, pool)  # type: ignore[arg-type]

    ((brief_id, saved),) = pool.args_for("app.save_brief_draft")
    config = json.loads(saved)
    assert brief_id == str(inp.brief_id)
    assert "name" not in config
    assert [p["key"] for p in config["predicates"]] == ["drone_programme", "industry"]
    assert ColumnSpec.model_validate(config["predicates"][0]).role == "signal"
    assert [r["predicate"] for r in config["evidence_rules"]] == ["drone_programme"]
    assert config["assumptions"] == ["Energy and mining sites count as industrial."]
    assert pool.args_for("app.apply_drafted_name") == [
        (str(inp.brief_id), "Operators of industrial drone programmes")
    ]
    assert pool.conn.calls.index("app.apply_drafted_name") > pool.conn.calls.index(
        "app.save_brief_draft"
    )
    dropped = [e for e in logs if e["event"] == "brief_intake.column_dropped"]
    assert len(dropped) == 2
    assert any("Bad Key" in e["reason"] for e in dropped)
    assert json.loads(gw.variables["existing_predicates"]) == [
        {"key": "size", "label": "Size", "value_type": "number"}
    ]


async def test_blank_name_does_not_call_apply_drafted_name() -> None:
    pool = MockPool()
    await run_brief_intake(_make_inp(), _RawGateway({**_COLUMNS_RAW, "name": "  "}), pool)  # type: ignore[arg-type]
    assert pool.args_for("app.apply_drafted_name") == []
    assert pool.last_call_fn == "app.save_brief_draft"


def test_brief_draft_columns_default_empty_and_stay_lenient() -> None:
    draft = BriefDraft.model_validate(_VALID_RAW)
    assert draft.name == ""
    assert draft.predicates == []
    assert draft.evidence_rules == []
    assert draft.assumptions == []
    lenient = BriefDraft.model_validate({**_VALID_RAW, "predicates": [3, "x", {"key": 1}]})
    assert len(lenient.predicates) == 3


async def test_intake_drops_a_column_whose_key_exists_with_another_type() -> None:
    """I1: the workspace holds "industry" as text; the drafted enum and its rules drop."""
    pool = MockPool()
    pool.conn.existing = [("industry", "Industry", "text")]
    raw = {
        **_COLUMNS_RAW,
        "evidence_rules": [
            *_COLUMNS_RAW["evidence_rules"],  # type: ignore[misc]
            {"predicate": "industry", "kind": "disqualifies", "op": "one_of", "value": ["mining"]},
        ],
    }
    with structlog.testing.capture_logs() as logs:
        await run_brief_intake(_make_inp(), _RawGateway(raw), pool)  # type: ignore[arg-type]
    ((_, saved),) = pool.args_for("app.save_brief_draft")
    config = json.loads(saved)
    assert [p["key"] for p in config["predicates"]] == ["drone_programme"]
    assert [r["predicate"] for r in config["evidence_rules"]] == ["drone_programme"]
    reasons = [e["reason"] for e in logs if e["event"] == "brief_intake.column_dropped"]
    assert any(
        'column "industry" already exists in this workspace as text; give it another key' in r
        for r in reasons
    )


async def test_intake_tolerates_a_null_name_and_odd_assumptions() -> None:
    """M2: a null name means no rename; only non-empty string assumptions are kept, at most 5."""
    raw = {
        **_COLUMNS_RAW,
        "name": None,
        "assumptions": ["a", 3, "", "  ", None, "b", "c", "d", "e", "f"],
    }
    pool = MockPool()
    await run_brief_intake(_make_inp(), _RawGateway(raw), pool)  # type: ignore[arg-type]
    ((_, saved),) = pool.args_for("app.save_brief_draft")
    assert json.loads(saved)["assumptions"] == ["a", "b", "c", "d", "e"]
    assert pool.args_for("app.apply_drafted_name") == []


async def test_intake_shows_the_ai_only_workspace_predicates() -> None:
    """M7: global predicates (workspace_id is null) are not offered to the intake AI."""
    pool = MockPool()
    await run_brief_intake(_make_inp(), _RawGateway(_COLUMNS_RAW), pool)  # type: ignore[arg-type]
    (sql,) = [s for s in pool.conn.sqls if "public.predicate" in s]
    assert "IS NULL" not in sql.upper()
    assert "workspace_id = %s" in sql
