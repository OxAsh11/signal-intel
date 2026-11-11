"""Tests for M01 Brief intake: BriefDraft model validation and run_brief_intake orchestration."""

import re
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from signal_core.ai_gateway.gateway import Result
from signal_core.ai_gateway.outputs import BriefDraft
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
        self.calls: list[tuple[str, str]] = []  # (fn_name, sql)

    async def execute(self, sql: str, args: object = None) -> None:
        m = re.search(r"app\.\w+", sql)
        fn = m.group(0) if m else sql
        self.calls.append(fn)

    @property
    def last_call_fn(self) -> str | None:
        return self.calls[-1] if self.calls else None


class _MockConnCtx:
    def __init__(self, conn: _MockConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _MockConn:
        return self._conn

    async def __aexit__(self, *_: object) -> None:
        pass


class MockPool:
    def __init__(self) -> None:
        self.conn = _MockConn()

    @property
    def last_call_fn(self) -> str | None:
        return self.conn.last_call_fn

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
