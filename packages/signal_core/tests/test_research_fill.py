"""AI research columns against a fake gateway and fake web edge: no AI key, no HTTP."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from signal_core.ai_gateway.gateway import Result
from signal_core.ai_gateway.prompts import Capability, render
from signal_core.fill import research_fill
from signal_core.fill.base import CLAIM_SQL, PRICE_SQL, SOURCE_SQL
from signal_core.metering import CallContext
from signal_core.providers.base import DEFAULT_PRICES

RUN_ID = uuid4()
ORG = uuid4()
WS = uuid4()
ENTITY = uuid4()
PREDICATE = uuid4()
SOURCE = uuid4()
CLAIM = uuid4()
VERSION = uuid4()
PAGE = research_fill.SourcePage(
    url="https://acme.example/engineering",
    title="acme.example",
    content="Acme runs a drone programme across forty sites.",
)


def _config(**over: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "question": "Does the company run a drone programme?",
        "inputs": ["drone_programme"],
        "web": True,
        "max_pages": 2,
    }
    config.update(over)
    return config


def _run(**over: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "org_id": str(ORG),
        "workspace_id": str(WS),
        "entity_id": str(ENTITY),
        "predicate_id": str(PREDICATE),
        "brief_id": None,
        "key": "drone_summary",
        "fill_method": "research",
        "fill_config": _config(),
        "value_type": "text",
        "allowed_values": None,
        "run_condition": None,
        "inputs": ["drone_programme"],
        "name": "Acme Engineering",
        "domain": "acme.example",
    }
    run.update(over)
    return run


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakePool]:
        yield self

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.calls.append((sql, params))
        if sql == research_fill.RUN_INPUTS_SQL:
            return FakeCursor([("drone_programme", uuid4(), True)])
        if sql == SOURCE_SQL:
            return FakeCursor([(SOURCE,)])
        if sql == CLAIM_SQL:
            return FakeCursor([(CLAIM,)])
        if sql == PRICE_SQL:
            return FakeCursor([(Decimal("5000"),)])  # $5 per 1,000 fetches = $0.005 each
        raise AssertionError(f"unexpected SQL {sql}")

    def sent(self, sql: str) -> list[tuple[Any, ...]]:
        return [p for s, p in self.calls if s == sql]


class FakeGateway:
    """Renders the frozen prompt before answering, proving every variable was supplied."""

    def __init__(self, answer: research_fill.ResearchAnswer) -> None:
        self.answer = answer
        self.calls: list[dict[str, str]] = []
        self.ctx: CallContext | None = None

    async def run(
        self,
        capability: Capability,
        prompt: Any,
        variables: dict[str, str],
        output: Any,
        ctx: CallContext,
        *,
        max_tokens: int = 2048,
    ) -> Result[research_fill.ResearchAnswer]:
        assert capability is Capability.WRITE
        render(prompt, variables)
        self.calls.append(variables)
        self.ctx = ctx
        return Result(
            value=output.model_validate(self.answer.model_dump()),
            extractor_version_id=VERSION,
            cost_usd=Decimal(0),
        )


class FakeWeb:
    def __init__(self, pages: list[research_fill.SourcePage] | None = None) -> None:
        self.pages = [PAGE] if pages is None else pages
        self.calls: list[tuple[list[str], int, CallContext]] = []

    async def search(
        self, queries: list[str], max_pages: int, ctx: CallContext
    ) -> list[research_fill.SourcePage]:
        self.calls.append((queries, max_pages, ctx))
        return self.pages[:max_pages]


class FakeBudget:
    def __init__(self) -> None:
        self.reserves: list[Decimal] = []
        self.settles: list[dict[str, Any]] = []
        self.releases: list[UUID] = []

    async def reserve(self, ctx: CallContext, estimate: Decimal) -> UUID:
        self.reserves.append(estimate)
        return uuid4()

    async def settle(
        self,
        reservation_id: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[Any],
        latency_ms: int,
        status: str,
    ) -> None:
        self.settles.append({"capability": capability, "lines": lines, "ctx": ctx})

    async def release(self, reservation_id: UUID) -> None:
        self.releases.append(reservation_id)


def _answer(**over: Any) -> research_fill.ResearchAnswer:
    data: dict[str, Any] = {
        "supported": True,
        "answer": "Yes, across forty sites",
        "supporting_sentence": "Acme runs a drone programme across forty sites.",
        "reasoning": "The fetched page states the programme directly.",
    }
    data.update(over)
    return research_fill.ResearchAnswer.model_validate(data)


async def test_an_answered_research_column_writes_a_typed_claim_and_receipt() -> None:
    gateway, web, budget, pool = FakeGateway(_answer()), FakeWeb(), FakeBudget(), FakePool()
    result = await research_fill.fill_research(pool, gateway, budget, _run(), RUN_ID, web=web)

    assert result.status == "succeeded"
    assert result.claim_id == CLAIM
    receipt = result.inputs
    assert receipt["queries"] == web.calls[0][0]
    assert receipt["pages"] == [{"url": PAGE.url, "title": PAGE.title}]
    assert receipt["answer"] == "Yes, across forty sites"
    assert len(receipt["cited_claim_ids"]) == 1
    assert receipt["reasoning"].startswith("The fetched page")
    assert "api_key" not in json.dumps(receipt).lower()
    assert web.calls[0][1] == 2
    assert gateway.ctx is not None and gateway.ctx.predicate_run_id == RUN_ID
    claim = pool.sent(CLAIM_SQL)[0]
    assert json.loads(claim[3]) == "Yes, across forty sites"
    assert claim[4] == "Acme runs a drone programme across forty sites."
    assert claim[9] == VERSION  # provenance: the frozen prompt version
    source = pool.sent(SOURCE_SQL)[0]
    assert source[2] == "research"  # evidence, not derived:formula
    assert source[4] == PAGE.url
    assert budget.reserves and budget.settles[0]["capability"] == "search"


async def test_unsupported_answers_are_no_data_without_a_claim() -> None:
    pool = FakePool()
    result = await research_fill.fill_research(
        pool,
        FakeGateway(_answer(supported=False, answer="", supporting_sentence="")),
        FakeBudget(),
        _run(),
        RUN_ID,
        web=FakeWeb([]),
    )
    assert result.status == "no_data"
    assert result.inputs["answer"] == ""
    assert pool.sent(CLAIM_SQL) == []
    assert pool.sent(SOURCE_SQL) == []


async def test_a_wrong_type_uses_the_p3_message() -> None:
    result = await research_fill.fill_research(
        FakePool(),
        FakeGateway(_answer()),
        FakeBudget(),
        _run(value_type="boolean"),
        RUN_ID,
        web=FakeWeb([]),
    )
    assert result.status == "error"
    assert result.error == "formula returned text, column is boolean"


async def test_research_without_web_uses_input_values_and_never_reserves_search() -> None:
    gateway, budget, pool = FakeGateway(_answer()), FakeBudget(), FakePool()
    result = await research_fill.fill_research(
        pool,
        gateway,
        budget,
        _run(fill_config=_config(web=False)),
        RUN_ID,
    )
    assert result.status == "succeeded"
    assert result.inputs["queries"] == []
    assert result.inputs["pages"] == []
    assert "drone_programme" in gateway.calls[0]["inputs"]
    assert budget.reserves == []
    assert pool.sent(SOURCE_SQL)[0][2] == "research:model"


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (_config(question="too short"), "10-500 characters"),
        (_config(max_pages=0), "max_pages must be 1-5"),
        (_config(inputs="not a list"), "list of column keys"),
    ],
)
async def test_an_invalid_research_config_is_an_error_cell(
    config: dict[str, Any], message: str
) -> None:
    result = await research_fill.fill_research(
        FakePool(), FakeGateway(_answer()), FakeBudget(), _run(fill_config=config), RUN_ID
    )
    assert result.status == "error"
    assert message in str(result.error)


async def test_the_frozen_prompt_is_the_one_the_gateway_renders() -> None:
    from signal_core.ai_gateway.prompts import PROMPTS_DIR, file_sha256, load_prompt

    prompt = load_prompt("research_column", 1)
    assert prompt.name == "research_column"
    assert prompt.sha256 == file_sha256((PROMPTS_DIR / "research_column" / "v1.md").read_bytes())
    variables = {
        "question": "Question long enough",
        "entity_name": "Acme",
        "domain": "acme.example",
        "inputs": "{}",
        "sources": "[]",
        "max_pages": "2",
    }
    rendered = render(prompt, variables)
    assert "Question long enough" in rendered
    assert DEFAULT_PRICES  # the price book, not a hard-coded cost, prices provider calls
