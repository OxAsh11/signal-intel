"""AI research columns (P6): one grounded answer, one typed claim, one receipt.

The prompt is frozen in LOCK.json. The gateway owns the AI reservation and settles it against
the run; Brave fetches, when enabled, have their own reservation for at most ``max_pages``
pages. The receipt stored in ``predicate_run.inputs`` contains no key and no raw page body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway, Result
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.config import get_settings
from signal_core.connectors import SourcePayload
from signal_core.connectors.base import FetchGuard
from signal_core.connectors.brave import (
    PRICE_MODEL as BRAVE_PRICE_MODEL,
)
from signal_core.connectors.brave import (
    TARGETED_KIND,
    BraveSearchConnector,
)
from signal_core.contracts.enums import UsageUnit
from signal_core.fill.base import (
    FillResult,
    as_json,
    context_for_run,
    create_source,
    typed,
    unit_price,
    write_claim,
)
from signal_core.logging import get_logger
from signal_core.metering import BudgetGuard, CallContext, UsageLine

log = get_logger(__name__)

RUN_INPUTS_SQL = "select key, claim_id, value from app.run_input_values(%s)"
MIN_QUESTION = 10
MAX_QUESTION = 500
MAX_PAGES = 5


class ResearchAnswer(BaseModel):
    supported: bool
    answer: str = ""
    supporting_sentence: str = ""
    reasoning: str = ""


@dataclass(frozen=True)
class SourcePage:
    url: str
    title: str
    content: str


class ResearchWeb(Protocol):
    async def search(
        self, queries: list[str], max_pages: int, ctx: CallContext
    ) -> list[SourcePage]: ...


class BraveResearchWeb:
    """The production web edge: one search, then a guarded fetch of each result."""

    async def search(
        self, queries: list[str], max_pages: int, ctx: CallContext
    ) -> list[SourcePage]:
        key = get_settings().brave_search_api_key
        connector = BraveSearchConnector(
            TARGETED_KIND,
            api_key=key.get_secret_value() if key else None,
            meter=None,
        )
        payloads, _ = await connector.fetch({}, queries, {}, FetchGuard(), limit=max_pages)
        pages: list[SourcePage] = []
        for payload in payloads[:max_pages]:
            page = _page(payload)
            if page is not None:
                pages.append(page)
        return pages


def _page(payload: SourcePayload) -> SourcePage | None:
    if not payload.url:
        return None
    host = payload.url.split("/")[2] if "//" in payload.url else payload.url
    return SourcePage(
        url=payload.url,
        title=host,
        content=payload.raw.decode("utf-8", errors="replace")[:20_000],
    )


def _config(run: dict[str, Any]) -> tuple[str, list[str], bool, int] | str:
    """Validate the column's fill_config. An invalid config is an error cell, not an exception."""
    config = run.get("fill_config") or {}
    question = str(config.get("question") or "").strip()
    if not MIN_QUESTION <= len(question) <= MAX_QUESTION:
        return f"a research question must be {MIN_QUESTION}-{MAX_QUESTION} characters"
    inputs = config.get("inputs") or []
    if not isinstance(inputs, list) or not all(isinstance(k, str) for k in inputs):
        return "research inputs must be a list of column keys"
    web = bool(config.get("web"))
    max_pages = int(config.get("max_pages", 1))
    if not 1 <= max_pages <= MAX_PAGES:
        return f"max_pages must be 1-{MAX_PAGES}"
    return question, [str(k) for k in inputs], web, max_pages


def _queries(question: str, name: str | None, domain: str | None) -> list[str]:
    subject = " ".join(p for p in (name, domain) if p)
    return [f"{subject} {question}".strip()]


async def fill_research(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    budget: BudgetGuard,
    run: dict[str, Any],
    run_id: UUID,
    *,
    web: ResearchWeb | None = None,
    ctx: Any = None,
) -> FillResult:
    """Answer one research column, with evidence and a machine-readable receipt."""
    ctx = ctx or context_for_run(run, run_id)
    config = _config(run)
    if isinstance(config, str):
        return FillResult(
            "error", error=config, inputs={"queries": [], "pages": [], "attempts": []}
        )
    question, inputs, use_web, max_pages = config

    async with pool.connection() as conn:
        cur = await conn.execute(RUN_INPUTS_SQL, (run_id,))
        rows = await cur.fetchall()
    known = {str(key): value for key, _, value in rows if str(key) in inputs}
    claim_ids = [str(claim_id) for key, claim_id, _ in rows if str(key) in inputs]

    queries: list[str] = []
    pages: list[SourcePage] = []
    if use_web:
        queries = _queries(question, run.get("name"), run.get("domain"))
        web = web or BraveResearchWeb()
        price = await unit_price(pool, BRAVE_PRICE_MODEL, UsageUnit.FETCH)
        reservation = await budget.reserve(ctx, price * max_pages)
        try:
            pages = await web.search(queries, max_pages, ctx)
        except BaseException:
            await budget.release(reservation)
            raise
        await budget.settle(
            reservation,
            ctx,
            "search",
            "brave",
            BRAVE_PRICE_MODEL,
            [UsageLine(UsageUnit.FETCH, len(pages), price * len(pages))],
            0,
            "ok",
        )

    def receipt(answer: ResearchAnswer) -> dict[str, Any]:
        return {
            "queries": queries,
            "pages": [{"url": p.url, "title": p.title} for p in pages],
            "answer": answer.answer,
            "cited_claim_ids": claim_ids,
            "reasoning": answer.reasoning,
        }

    prompt = load_prompt("research_column", 1)
    sources = [{"url": p.url, "title": p.title, "content": p.content} for p in pages] or [
        {"value": value} for value in known.values()
    ]
    result: Result[ResearchAnswer] = await gateway.run(
        Capability.WRITE,
        prompt,
        {
            "question": question,
            "entity_name": str(run.get("name") or ""),
            "domain": str(run.get("domain") or ""),
            "inputs": as_json(known),
            "sources": as_json(sources),
            "max_pages": str(max_pages),
        },
        ResearchAnswer,
        ctx,
    )
    answer = result.value
    if not answer.supported or not answer.answer.strip():
        return FillResult("no_data", inputs=receipt(answer))

    problem = typed(answer.answer, run)
    if problem is not None:
        return FillResult("error", error=problem, inputs=receipt(answer))

    first_page = pages[0] if pages else None
    source_id = await create_source(
        pool,
        run,
        connector="research" if first_page else "research:model",
        source_type="web" if first_page else "answer",
        url=first_page.url if first_page else None,
        payload_ref=first_page.title if first_page else None,
        content=answer.supporting_sentence or answer.answer,
    )
    claim_id = await write_claim(
        pool,
        run,
        answer.answer,
        quote=answer.supporting_sentence,
        source_id=source_id,
        confidence=80,
        extractor_version_id=result.extractor_version_id,
    )
    return FillResult("succeeded", claim_id=claim_id, inputs=receipt(answer))
