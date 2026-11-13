"""Tests for M3 extraction pipeline: relevance judge and structured extraction."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import signal_core.modules.m03_extraction as _m03_extraction
from signal_core.ai_gateway.errors import AiOutputInvalid
from signal_core.ai_gateway.gateway import AiGateway, MemoryExtractorVersions
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.metering import CallContext, MemoryBudgetGuard, MemoryPriceBook
from signal_core.modules.m03_extraction import (
    ExtractionResult,
    extract_backfill,
    extract_structured,
    judge_relevance,
    passes_lexicon,
)

# ---------------------------------------------------------------------------
# Shared IDs
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
WORKSPACE_ID = uuid4()
SOURCE_RECORD_ID = uuid4()
PREDICATE_ID = uuid4()
PREDICATE_KEY = "uses_hiring"


# ---------------------------------------------------------------------------
# Fake pool infrastructure
# ---------------------------------------------------------------------------


class FakeCursor:
    """Cursor-like object returned by FakeConn.cursor()."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, args: Any = None) -> None:
        self._rows = self._conn._route(sql, args)

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConn:
    """Connection-like object that routes SQL to canned responses."""

    def __init__(self, source_record: dict, predicates: list[tuple]) -> None:
        self._source_record = source_record
        self._predicates = predicates
        self.calls: list[tuple[str, Any]] = []
        self._entity_id = uuid4()
        self._mention_id = uuid4()
        self._claim_id = uuid4()

    def _route(self, sql: str, args: Any) -> list[tuple[Any, ...]]:
        """Return canned rows based on the SQL pattern."""
        self.calls.append((sql, args))
        sql_lower = sql.lower()
        if "lexicon" in sql_lower:
            lexicon = self._source_record.get("lexicon")
            return [(lexicon,)] if lexicon is not None else []
        if "public.source_record" in sql_lower:
            sr = self._source_record
            if "extraction_status" in sql_lower:
                return [(sr.get("extraction_status", "pending"),)]
            return [
                (
                    str(sr["id"]),
                    str(sr["org_id"]),
                    str(sr["workspace_id"]),
                    sr.get("url", "https://example.com/article"),
                    sr.get("payload_ref"),
                )
            ]
        if "public.predicate" in sql_lower:
            return self._predicates
        if "app.create_entity" in sql_lower:
            return [(str(self._entity_id),)]
        if "app.create_mention" in sql_lower:
            return [(str(self._mention_id),)]
        if "app.create_claim" in sql_lower:
            return [(str(self._claim_id),)]
        # app.update_extraction_status and others return nothing meaningful
        return []

    async def execute(self, sql: str, args: Any = None) -> FakeCursor:
        self._route(sql, args)
        return FakeCursor(self)  # cursor with empty rows for non-select execute calls

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    async def __aenter__(self) -> FakeConn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConnCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakePool:
    """Pool-like object backed by a single FakeConn."""

    def __init__(self, source_record: dict, predicates: list[tuple] | None = None) -> None:
        self.conn = FakeConn(source_record, predicates or [])

    def connection(self) -> FakeConnCtx:
        return FakeConnCtx(self.conn)


# ---------------------------------------------------------------------------
# Gateway factory
# ---------------------------------------------------------------------------

_HAIKU = "claude-haiku-5-5"
_SONNET = "claude-sonnet-5-5"


def _prices() -> MemoryPriceBook:
    return MemoryPriceBook(
        {
            (_HAIKU, UsageUnit.AI_INPUT_TOKENS): Decimal("0.10"),
            (_HAIKU, UsageUnit.AI_OUTPUT_TOKENS): Decimal("0.50"),
            (_SONNET, UsageUnit.AI_INPUT_TOKENS): Decimal("2.00"),
            (_SONNET, UsageUnit.AI_OUTPUT_TOKENS): Decimal("10.00"),
        }
    )


def _make_gateway(response: str) -> AiGateway:
    return AiGateway(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        chat=FakeProvider({"*": response}),
        embedder=None,
        prices=_prices(),
        budget=MemoryBudgetGuard(),
        versions=MemoryExtractorVersions(),
    )


def _ctx() -> CallContext:
    return CallContext(ORG_ID, WORKSPACE_ID, None, None, "test.job", {})


def _fake_source_record() -> dict:
    return {
        "id": SOURCE_RECORD_ID,
        "org_id": ORG_ID,
        "workspace_id": WORKSPACE_ID,
        "url": "https://example.com/acme-hiring-engineers",
        "payload_ref": None,
    }


def _fake_predicates() -> list[tuple]:
    return [(str(PREDICATE_ID), PREDICATE_KEY, "Whether the company is actively hiring engineers")]


# ---------------------------------------------------------------------------
# Tests: judge_relevance
# ---------------------------------------------------------------------------


async def test_judge_relevance_relevant() -> None:
    """Returns True when the AI says relevant=true."""
    response = json.dumps({"relevant": True, "reason": "contains hiring signals"})
    gw = _make_gateway(response)
    pool = FakePool(_fake_source_record())

    result = await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert result is True
    # update_extraction_status should NOT have been called on a relevant result
    status_calls = [c for c, _ in pool.conn.calls if "update_extraction_status" in c]
    assert status_calls == []


async def test_judge_relevance_irrelevant_updates_status() -> None:
    """Returns False and persists irrelevant status when AI says relevant=false."""
    reason = "generic navigation page"
    response = json.dumps({"relevant": False, "reason": reason})
    gw = _make_gateway(response)
    pool = FakePool(_fake_source_record())

    result = await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert result is False
    status_calls = [args for sql, args in pool.conn.calls if "update_extraction_status" in sql]
    assert len(status_calls) == 1
    assert "irrelevant" in status_calls[0]


async def test_judge_relevance_ai_error_updates_status_and_reraises() -> None:
    """On AiOutputInvalid, persists failed status and re-raises."""
    # Return malformed JSON to trigger AiOutputInvalid (missing required field)
    gw = _make_gateway('{"relevant": true}')  # missing "reason" field
    pool = FakePool(_fake_source_record())

    with pytest.raises(AiOutputInvalid):
        await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx())

    status_calls = [args for sql, args in pool.conn.calls if "update_extraction_status" in sql]
    assert len(status_calls) == 1
    assert "failed" in status_calls[0]


def test_passes_lexicon_casefold() -> None:
    assert passes_lexicon("AUSSCHREIBUNG für Hotels", ["ausschreibung"])
    assert passes_lexicon("Société Générale", ["société"])
    fullwidth = "".join(chr(0xFEE0 + ord(c)) for c in "Tender")  # NFKC folds these
    assert passes_lexicon(f"{fullwidth} notice", ["tender"])
    assert not passes_lexicon("nothing here", ["tender"])
    assert passes_lexicon("x", [])
    # Accents are ignored on both sides.
    assert passes_lexicon("Hotel renovation planned", ["rénovation"])
    assert passes_lexicon("Rénovation de l'hôtel", ["renovation"])
    assert passes_lexicon("Straße", ["strasse"])


async def test_judge_relevance_prefilter_skips_ai() -> None:
    """A record with none of its radar's lexicon terms is pre-filtered without an AI call."""
    gw = _make_gateway(json.dumps({"relevant": True, "reason": "unused"}))
    record = _fake_source_record() | {"lexicon": ["tender", "procurement"]}
    pool = FakePool(record)

    result = await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert result is False
    status_calls = [args for sql, args in pool.conn.calls if "update_extraction_status" in sql]
    assert status_calls == [(str(SOURCE_RECORD_ID), "irrelevant", "prefilter")]
    assert gw._budget.lines == []  # type: ignore[attr-defined]


async def test_judge_relevance_lexicon_hit_goes_to_ai() -> None:
    gw = _make_gateway(json.dumps({"relevant": True, "reason": "ok"}))
    record = _fake_source_record() | {"lexicon": ["hiring"]}
    pool = FakePool(record)

    assert await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx()) is True


# ---------------------------------------------------------------------------
# Tests: extract_structured
# ---------------------------------------------------------------------------


async def test_extract_structured_creates_mentions_and_claims() -> None:
    """Returns ExtractionResult with AI-extracted mentions and claims."""
    extraction_resp = json.dumps(
        {
            "mentions": [
                {
                    "entity_type": "company",
                    "raw_name": "Acme Corp",
                    "raw_domain": "acme.com",
                    "raw_country": "US",
                    "raw_attributes": {},
                }
            ],
            "claims": [
                {
                    "mention_index": 0,
                    "predicate_key": PREDICATE_KEY,
                    "value": '"true"',
                    "quote": "Acme is hiring engineers",
                    "quote_original": None,
                    "quote_language": None,
                    "confidence": 90,
                }
            ],
        }
    )
    gw = _make_gateway(extraction_resp)
    pool = FakePool(_fake_source_record(), _fake_predicates())

    result = await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert isinstance(result, ExtractionResult)
    assert len(result.mentions) == 1
    assert result.mentions[0].raw_name == "Acme Corp"
    assert len(result.claims) == 1
    assert result.claims[0].predicate_key == PREDICATE_KEY


async def test_extract_structured_persists_extracted_status() -> None:
    """Calls update_extraction_status('extracted') on success."""
    extraction_resp = json.dumps({"mentions": [], "claims": []})
    gw = _make_gateway(extraction_resp)
    pool = FakePool(_fake_source_record(), [])

    await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    status_calls = [args for sql, args in pool.conn.calls if "update_extraction_status" in sql]
    assert len(status_calls) == 1
    assert "extracted" in status_calls[0]


async def test_extract_structured_ai_error_updates_status_and_reraises() -> None:
    """On AiOutputInvalid, persists failed status and re-raises."""
    # Return malformed JSON that cannot be parsed as ExtractionResult
    gw = _make_gateway('{"bad": "schema"}')
    pool = FakePool(_fake_source_record(), _fake_predicates())

    with pytest.raises(AiOutputInvalid):
        await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    status_calls = [args for sql, args in pool.conn.calls if "update_extraction_status" in sql]
    assert len(status_calls) == 1
    assert "failed" in status_calls[0]


async def test_extract_structured_skips_unknown_predicates() -> None:
    """Claims referencing unknown predicate keys are silently skipped."""
    extraction_resp = json.dumps(
        {
            "mentions": [{"entity_type": "company", "raw_name": "Acme Corp", "raw_attributes": {}}],
            "claims": [
                {
                    "mention_index": 0,
                    "predicate_key": "nonexistent_key",
                    "value": '"x"',
                    "confidence": 50,
                }
            ],
        }
    )
    gw = _make_gateway(extraction_resp)
    pool = FakePool(_fake_source_record(), _fake_predicates())

    result = await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert len(result.claims) == 1  # claim is in AI output
    # But app.create_claim should NOT have been called
    claim_calls = [sql for sql, _ in pool.conn.calls if "create_claim" in sql]
    assert claim_calls == []


async def test_extract_structured_skips_oob_mention_index() -> None:
    """Claims with mention_index out of bounds are silently skipped."""
    extraction_resp = json.dumps(
        {
            "mentions": [{"entity_type": "company", "raw_name": "Acme Corp", "raw_attributes": {}}],
            "claims": [
                {
                    "mention_index": 99,  # out of bounds
                    "predicate_key": PREDICATE_KEY,
                    "value": '"true"',
                    "confidence": 70,
                }
            ],
        }
    )
    gw = _make_gateway(extraction_resp)
    pool = FakePool(_fake_source_record(), _fake_predicates())

    result = await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert len(result.mentions) == 1
    claim_calls = [sql for sql, _ in pool.conn.calls if "create_claim" in sql]
    assert claim_calls == []


# ---------------------------------------------------------------------------
# Tests: extract_backfill
# ---------------------------------------------------------------------------


class _FakeBackfillCursor:
    """Cursor for backfill pool — returns pending source_record ids."""

    def __init__(self, ids: list[tuple]) -> None:
        self._ids = ids
        self._rows: list[tuple] = []

    async def execute(self, sql: str, args: Any = None) -> None:
        if "source_record" in sql.lower():
            self._rows = self._ids

    async def fetchall(self) -> list[tuple]:
        return self._rows

    async def __aenter__(self) -> _FakeBackfillCursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakeBackfillConn:
    def __init__(self, ids: list[tuple]) -> None:
        self._ids = ids

    def cursor(self) -> _FakeBackfillCursor:
        return _FakeBackfillCursor(self._ids)

    async def __aenter__(self) -> _FakeBackfillConn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class _FakeBackfillConnCtx:
    def __init__(self, ids: list[tuple]) -> None:
        self._ids = ids

    async def __aenter__(self) -> _FakeBackfillConn:
        return _FakeBackfillConn(self._ids)

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeBackfillPool:
    """Pool that returns N pending source_record ids for the backfill query."""

    def __init__(self, pending_ids: list[UUID]) -> None:
        self._rows = [(str(id_),) for id_ in pending_ids]

    def connection(self) -> _FakeBackfillConnCtx:
        return _FakeBackfillConnCtx(self._rows)


class _FakeJob:
    """Fake procrastinate task handle that records defer_async calls."""

    def __init__(self) -> None:
        self.deferred: list[dict] = []

    async def defer_async(self, **kwargs: Any) -> None:
        self.deferred.append(kwargs)


async def test_extract_backfill_queues_pending_records() -> None:
    """extract_backfill returns count=N and defers one job per pending record."""
    pending_ids = [uuid4(), uuid4(), uuid4()]
    pool = FakeBackfillPool(pending_ids)
    fake_job = _FakeJob()

    # Patch the module-level handle so defer_async works without a real Procrastinate app.
    original = _m03_extraction.extract_relevance_judge_job
    _m03_extraction.extract_relevance_judge_job = fake_job
    try:
        count = await extract_backfill(pool, uuid4(), WORKSPACE_ID)
    finally:
        _m03_extraction.extract_relevance_judge_job = original

    assert count == 3
    assert len(fake_job.deferred) == 3
    deferred_ids = {call["source_record_id"] for call in fake_job.deferred}
    assert deferred_ids == {str(id_) for id_ in pending_ids}
