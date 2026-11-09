"""Tests for M3 extraction pipeline: relevance judge and structured extraction."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway, MemoryExtractorVersions
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.metering import CallContext, MemoryBudgetGuard, MemoryPriceBook
from signal_core.modules.m03_extraction import (
    ExtractionResult,
    judge_relevance,
    extract_structured,
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

    def __init__(self, conn: "FakeConn") -> None:
        self._conn = conn
        self._rows: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, args: Any = None) -> None:
        self._rows = self._conn._route(sql, args)

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    async def __aenter__(self) -> "FakeCursor":
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
        if "public.source_record" in sql_lower:
            sr = self._source_record
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

    async def execute(self, sql: str, args: Any = None) -> "FakeCursor":
        self._route(sql, args)
        return FakeCursor(self)  # cursor with empty rows for non-select execute calls

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self)

    async def __aenter__(self) -> "FakeConn":
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
    status_calls = [
        args for sql, args in pool.conn.calls if "update_extraction_status" in sql
    ]
    assert len(status_calls) == 1
    assert "irrelevant" in status_calls[0]


async def test_judge_relevance_ai_error_updates_status_and_reraises() -> None:
    """On AiOutputInvalid, persists failed status and re-raises."""
    # Return malformed JSON to trigger AiOutputInvalid (missing required field)
    gw = _make_gateway('{"relevant": true}')  # missing "reason" field
    pool = FakePool(_fake_source_record())

    with pytest.raises(AiOutputInvalid):
        await judge_relevance(pool, gw, SOURCE_RECORD_ID, _ctx())

    status_calls = [
        args for sql, args in pool.conn.calls if "update_extraction_status" in sql
    ]
    assert len(status_calls) == 1
    assert "failed" in status_calls[0]


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

    status_calls = [
        args for sql, args in pool.conn.calls if "update_extraction_status" in sql
    ]
    assert len(status_calls) == 1
    assert "extracted" in status_calls[0]


async def test_extract_structured_ai_error_updates_status_and_reraises() -> None:
    """On AiOutputInvalid, persists failed status and re-raises."""
    # Return malformed JSON that cannot be parsed as ExtractionResult
    gw = _make_gateway('{"bad": "schema"}')
    pool = FakePool(_fake_source_record(), _fake_predicates())

    with pytest.raises(AiOutputInvalid):
        await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    status_calls = [
        args for sql, args in pool.conn.calls if "update_extraction_status" in sql
    ]
    assert len(status_calls) == 1
    assert "failed" in status_calls[0]


async def test_extract_structured_skips_unknown_predicates() -> None:
    """Claims referencing unknown predicate keys are silently skipped."""
    extraction_resp = json.dumps(
        {
            "mentions": [
                {"entity_type": "company", "raw_name": "Acme Corp", "raw_attributes": {}}
            ],
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
            "mentions": [
                {"entity_type": "company", "raw_name": "Acme Corp", "raw_attributes": {}}
            ],
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
