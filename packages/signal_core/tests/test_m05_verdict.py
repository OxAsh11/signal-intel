"""M5 verdicts under a Brief's evidence rules (P2): the rubric decides, the AI judges open cases.

What these paths do against the real schema is proven in services/worker/tests/test_pipeline_e2e.py.
"""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import structlog.testing
from pydantic import BaseModel, ValidationError

import signal_core.modules.m05_verdict as m05
from signal_core.ai_gateway.errors import AiOutputInvalid
from signal_core.ai_gateway.gateway import Result
from signal_core.ai_gateway.prompts import Prompt, render
from signal_core.briefs import ActiveBrief
from signal_core.metering import CallContext

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "flytbase_columns.json").read_text(encoding="utf-8")
)
RUBRIC_CONFIG: dict[str, Any] = {
    "hypothesis": "Utilities that run drone programmes",
    "examples": {
        "positive": ["Volt Grid, a utility flying drone inspections"],
        "negative": ["SkyMakers, a drone vendor"],
    },
    **FIXTURE,
}
LEGACY_CONFIG: dict[str, Any] = {
    "hypothesis": "Engineering firms that design in Revit",
    "predicates": ["uses_revit"],
}


class RecordingGateway:
    """Validates each answer against the requested output, as the real gateway does."""

    def __init__(self, answers: dict[str, dict[str, Any]] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[Prompt, str]] = []

    async def run(
        self,
        capability: Any,
        prompt: Prompt,
        variables: dict[str, str],
        output: type[BaseModel],
        ctx: CallContext,
        **_: Any,
    ) -> Result[Any]:
        self.calls.append((prompt, render(prompt, variables)))
        try:
            value = output.model_validate(self.answers[f"{prompt.name}/v{prompt.version}"])
        except ValidationError as exc:
            raise AiOutputInvalid([str(e["msg"]) for e in exc.errors()]) from None
        return Result(value=value, extractor_version_id=uuid4(), cost_usd=Decimal(0))

    @property
    def names(self) -> list[str]:
        return [f"{p.name}/v{p.version}" for p, _ in self.calls]


def _brief(config: dict[str, Any]) -> ActiveBrief:
    return ActiveBrief(uuid4(), uuid4(), uuid4(), config, {})


def _ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "test", {})


def _claim(
    key: str,
    value: Any,
    quote: str | None = "announced a drone programme",
    url: str | None = "https://news.example.com/a",
    days_old: int = 10,
) -> tuple[Any, ...]:
    observed = datetime.now(UTC) - timedelta(days=days_old)
    return (uuid4(), key, value, quote, 90, observed, url, uuid4())


def _pool(claims: list[tuple[Any, ...]], existing: tuple[Any, ...] | None = None) -> MagicMock:
    cur = MagicMock()
    cur.execute = AsyncMock()
    # First the entity's name; after that the stored verdict for each Brief (none by default).
    cur.fetchone = AsyncMock(side_effect=[("Volt Grid",), *([existing] * 8)])
    cur.fetchall = AsyncMock(return_value=claims)
    conn = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.cur = cur
    return pool


@pytest.fixture
def upsert(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(m05, "upsert_verdict", mock)
    return mock


def _use(monkeypatch: pytest.MonkeyPatch, *briefs: ActiveBrief) -> None:
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=list(briefs)))


def _queued_reviews(pool: MagicMock) -> list[Any]:
    return [c for c in pool.cur.execute.call_args_list if "queue_verdict_review" in c.args[0]]


def test_live_claims_sql_selects_the_source() -> None:
    sql = " ".join(m05.LIVE_CLAIMS_SQL.split())
    assert "c.observed_at, sr.url, sr.id" in sql
    assert "JOIN public.source_record sr ON sr.id = c.source_record_id" in sql


async def test_rubric_decides_without_the_ai(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    gateway = RecordingGateway()
    drone = _claim("drone_programme", True)
    sites = _claim("site_count", 40, quote=None)
    pool = _pool([drone, sites])

    assert await m05.compute_verdict(pool, gateway, uuid4(), uuid4(), _ctx()) == "Confirmed"  # type: ignore[arg-type]
    assert gateway.calls == []
    _, _, _, value, claim_ids, quotes, inputs = upsert.call_args.args
    assert value == "Confirmed"
    assert claim_ids == [drone[0]]
    assert quotes == [{"claim_id": str(drone[0]), "quote": "announced a drone programme"}]
    assert inputs["method"] == "rubric"
    assert [(r["predicate"], r["kind"], r["met"]) for r in inputs["rubric"]] == [
        ("drone_programme", "requires", True),
        ("industry", "disqualifies", False),
    ]
    assert inputs["rubric"][0]["claim_ids"] == [str(drone[0])]
    assert _queued_reviews(pool) == []


async def test_rubric_contradicts_and_leaves_unverified_without_the_ai(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    gateway = RecordingGateway()
    vendor = _claim("industry", "Drone Vendor", quote="we build drones")
    pool = _pool([_claim("drone_programme", True), vendor])
    assert await m05.compute_verdict(pool, gateway, uuid4(), uuid4(), _ctx()) == "Contradicted"  # type: ignore[arg-type]
    assert upsert.call_args.args[4] == [vendor[0]]

    untouched = _pool([_claim("site_count", 40)])
    assert await m05.compute_verdict(untouched, gateway, uuid4(), uuid4(), _ctx()) == "Unverified"  # type: ignore[arg-type]
    assert upsert.call_args.args[4] == []
    assert upsert.call_args.args[6]["method"] == "rubric"
    assert gateway.calls == []


async def test_open_case_calls_verdict_v3_with_the_rules(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    answer = {
        "value": "Likely",
        "reasoning": "one unquoted mention",
        "supporting_claim_indices": [0],
    }
    gateway = RecordingGateway({"verdict/v3": answer})
    unquoted = _claim("drone_programme", True, quote=None)
    pool = _pool([unquoted])

    assert await m05.compute_verdict(pool, gateway, uuid4(), uuid4(), _ctx()) == "Likely"  # type: ignore[arg-type]
    assert gateway.names == ["verdict/v3"]
    prompt = gateway.calls[0][1]
    for text in (
        "Utilities that run drone programmes",
        "Volt Grid, a utility flying drone inspections",
        "SkyMakers, a drone vendor",
        "Volt Grid",
        # The plain rules and what the evidence showed for each.
        "requires drone_programme is true",
        "within 730 days",
        "disqualifies industry is one of: drone vendor",
        "not met",
        "news.example.com",
    ):
        assert text in prompt, text
    _, _, _, value, claim_ids, _, inputs = upsert.call_args.args
    assert (value, claim_ids) == ("Likely", [unquoted[0]])
    assert inputs["method"] == "rubric_ai"
    assert inputs["reasoning"] == "one unquoted mention"
    assert (inputs["prompt_name"], inputs["prompt_version"]) == ("verdict", 3)
    assert inputs["rubric"][0]["touched"] is True and inputs["rubric"][0]["met"] is False


async def test_open_verdict_can_never_be_confirmed(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    """R4: verdict v3 answering Confirmed is demoted to Likely in code, never stored as Confirmed
    and never a failed job (no paid retries, later Briefs still judged)."""
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    confirmed = {"value": "Confirmed", "reasoning": "r", "supporting_claim_indices": [0]}
    gateway = RecordingGateway({"verdict/v3": confirmed})
    with structlog.testing.capture_logs() as logs:
        value = await m05.compute_verdict(  # type: ignore[arg-type]
            _pool([_claim("drone_programme", False)]), gateway, uuid4(), uuid4(), _ctx()
        )
    assert value == "Likely"
    assert gateway.names == ["verdict/v3"]
    assert upsert.call_args.args[3] == "Likely"
    assert [e["event"] for e in logs].count("verdict.open_confirmed_demoted") == 1


def test_live_claims_order_has_a_tiebreak() -> None:
    """M5: claims observed at the same moment come back in a stable order."""
    sql = " ".join(m05.LIVE_CLAIMS_SQL.split())
    assert "ORDER BY c.observed_at DESC, c.id" in sql


async def test_legacy_brief_keeps_verdict_v2_and_stores_the_reasoning(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(LEGACY_CONFIG))
    answer = {"value": "Confirmed", "reasoning": "quoted", "supporting_claim_indices": [0]}
    gateway = RecordingGateway({"verdict/v2": answer})
    claim = _claim("uses_revit", True, quote="designs in Revit")
    assert await m05.compute_verdict(_pool([claim]), gateway, uuid4(), uuid4(), _ctx()) == (  # type: ignore[arg-type]
        "Confirmed"
    )
    assert gateway.names == ["verdict/v2"]
    inputs = upsert.call_args.args[6]
    assert inputs["method"] == "ai"
    assert inputs["reasoning"] == "quoted"
    assert (inputs["prompt_name"], inputs["prompt_version"]) == ("verdict", 2)
    assert "rubric" not in inputs


async def test_brief_edited_between_no_rules_and_rules(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    """Review focus 4: each path gives a valid verdict; a version with rules never calls v2."""
    legacy, rubric = _brief(LEGACY_CONFIG), _brief(RUBRIC_CONFIG)
    _use(monkeypatch, rubric, legacy)
    answer = {"value": "Likely", "reasoning": "r", "supporting_claim_indices": [0]}
    gateway = RecordingGateway({"verdict/v2": answer, "verdict/v3": answer})
    claims = [_claim("drone_programme", True)]

    assert await m05.compute_verdict(_pool(claims), gateway, uuid4(), uuid4(), _ctx()) == (  # type: ignore[arg-type]
        "Confirmed"
    )
    assert gateway.names == ["verdict/v2"]  # the legacy version only
    by_version = {c.args[1]: (c.args[3], c.args[6]["method"]) for c in upsert.call_args_list}
    assert by_version == {
        rubric.brief_version_id: ("Confirmed", "rubric"),
        legacy.brief_version_id: ("Likely", "ai"),
    }

    # Rules removed again (only the legacy version judges): v2 again, never v3.
    _use(monkeypatch, legacy)
    gateway = RecordingGateway({"verdict/v2": answer})
    await m05.compute_verdict(_pool(claims), gateway, uuid4(), uuid4(), _ctx())  # type: ignore[arg-type]
    assert gateway.names == ["verdict/v2"]


async def test_rubric_verdict_still_queues_conflicts(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    yes = _claim("drone_programme", True)
    no = _claim("drone_programme", False, url="https://other.example/b")
    pool = _pool([yes, no])
    assert await m05.compute_verdict(pool, RecordingGateway(), uuid4(), uuid4(), _ctx()) == (  # type: ignore[arg-type]
        "Confirmed"
    )
    (review,) = _queued_reviews(pool)
    assert sorted(review.args[1][1]) == sorted([str(yes[0]), str(no[0])])


async def test_human_mark_holds_on_the_rubric_path(
    monkeypatch: pytest.MonkeyPatch, upsert: AsyncMock
) -> None:
    _use(monkeypatch, _brief(RUBRIC_CONFIG))
    claims = [_claim("drone_programme", True)]
    held = ("Likely", {"human_override": True, "live_claim_ids": [str(claims[0][0])]})
    gateway = RecordingGateway()
    pool = _pool(claims, existing=held)
    assert await m05.compute_verdict(pool, gateway, uuid4(), uuid4(), _ctx()) == "Likely"  # type: ignore[arg-type]
    upsert.assert_not_called()
    assert gateway.calls == []


def test_claim_source_falls_back_to_the_record_id() -> None:
    record = UUID(int=7)
    claim = (uuid4(), "k", True, "q", 90, datetime.now(UTC), None, record)
    assert m05.rubric_claim(claim).source == str(record)
    assert m05.rubric_claim(_claim("k", True, url="https://A.Example/x")).source == "a.example"
