"""Tests for M3 extraction pipeline: relevance judge and structured extraction."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
import structlog.testing

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
    parse_source_date,
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

    def __init__(
        self,
        source_record: dict,
        predicates: list[tuple],
        calls: list[tuple[str, Any]] | None = None,
        log: list[tuple[int, str, Any]] | None = None,
        conn_id: int = 0,
        fail_on: str | None = None,
        tx_events: list[tuple[str, int, BaseException | None]] | None = None,
    ) -> None:
        self._source_record = source_record
        self._predicates = predicates
        self.calls: list[tuple[str, Any]] = calls if calls is not None else []
        self.log: list[tuple[int, str, Any]] = log if log is not None else []
        self.conn_id = conn_id
        self.fail_on = fail_on
        self.tx_events: list[tuple[str, int, BaseException | None]] = (
            tx_events if tx_events is not None else []
        )
        self._entity_id = uuid4()
        self._mention_id = uuid4()
        self._claim_id = uuid4()

    def _route(self, sql: str, args: Any) -> list[tuple[Any, ...]]:
        """Return canned rows based on the SQL pattern."""
        self.calls.append((sql, args))
        self.log.append((self.conn_id, sql, args))
        sql_lower = sql.lower()
        if self.fail_on and self.fail_on in sql_lower:
            raise RuntimeError("simulated database error")
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
        cur = FakeCursor(self)
        cur._rows = self._route(sql, args)
        return cur

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def __aenter__(self) -> FakeConn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeTransaction:
    """Records entry and the exception (if any) the block exited with."""

    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeTransaction:
        self._conn.tx_events.append(("enter", self._conn.conn_id, None))
        return self

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        self._conn.tx_events.append(("exit", self._conn.conn_id, exc))


class FakeConnCtx:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakePool:
    """Pool-like object; every connection() is a new FakeConn sharing one call log."""

    def __init__(
        self,
        source_record: dict,
        predicates: list[tuple] | None = None,
        fail_on: str | None = None,
    ) -> None:
        self._source_record = source_record
        self._predicates = predicates or []
        self._fail_on = fail_on
        self._n = 0
        self.log: list[tuple[int, str, Any]] = []
        self.tx_events: list[tuple[str, int, BaseException | None]] = []
        self.conn = self._new_conn()  # first connection; .calls is the shared call list
        self.calls = self.conn.calls

    def _new_conn(self) -> FakeConn:
        calls = self.conn.calls if self._n > 0 else None
        self._n += 1
        return FakeConn(
            self._source_record,
            self._predicates,
            calls,
            self.log,
            self._n,
            self._fail_on,
            self.tx_events,
        )

    def connection(self) -> FakeConnCtx:
        return FakeConnCtx(self._new_conn())


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


NUMBER_ID = uuid4()


def _fake_predicates() -> list[tuple]:
    # id, key, label, value_type, allowed_values, extraction_hint
    return [
        (
            str(PREDICATE_ID),
            PREDICATE_KEY,
            "Hiring",
            "boolean",
            None,
            "Whether the company is actively hiring engineers",
        ),
        (str(NUMBER_ID), "sites", "Sites", "number", None, "Number of sites"),
    ]


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
                    "value": True,
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
                    "value": "x",
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
                    "value": True,
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
# Tests: typed values, validation, one transaction, source dates
# ---------------------------------------------------------------------------


def _resp(claims: list[dict], source_date: str | None = None) -> str:
    return json.dumps(
        {
            "mentions": [{"entity_type": "company", "raw_name": "Acme Corp", "raw_attributes": {}}],
            "claims": claims,
            "source_date": source_date,
        }
    )


def _claim(key: str, value: Any) -> dict:
    return {"mention_index": 0, "predicate_key": key, "value": value, "confidence": 80}


async def test_extract_sends_predicate_types() -> None:
    seen: dict[str, str] = {}
    gw = _make_gateway(_resp([]))
    original = gw.run

    async def spy(cap: Any, prompt: Any, variables: dict[str, str], *a: Any, **k: Any) -> Any:
        if "predicates" in variables:
            seen.update(variables)
        return await original(cap, prompt, variables, *a, **k)

    gw.run = spy  # type: ignore[method-assign]
    pool = FakePool(_fake_source_record(), _fake_predicates())

    await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    predicates = json.loads(seen["predicates"])
    hiring = next(p for p in predicates if p["key"] == PREDICATE_KEY)
    assert hiring["value_type"] == "boolean"
    assert '"value_type": "boolean"' in seen["predicates"]
    assert set(hiring) == {"key", "label", "value_type", "allowed_values", "hint"}


async def test_extract_typed_boolean_written_as_json_true() -> None:
    gw = _make_gateway(_resp([_claim(PREDICATE_KEY, True)]))
    pool = FakePool(_fake_source_record(), _fake_predicates())

    await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    claim_args = [a for sql, a in pool.calls if "create_claim" in sql]
    assert len(claim_args) == 1
    assert claim_args[0][3] == "true"


async def test_extract_invalid_claim_dropped_record_still_extracted() -> None:
    gw = _make_gateway(_resp([_claim("sites", "100+"), _claim(PREDICATE_KEY, True)]))
    pool = FakePool(_fake_source_record(), _fake_predicates())

    with structlog.testing.capture_logs() as logs:
        await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    claim_calls = [a for sql, a in pool.calls if "create_claim" in sql]
    assert len(claim_calls) == 1
    status_calls = [a for sql, a in pool.calls if "update_extraction_status" in sql]
    assert status_calls[-1][1] == "extracted"
    invalid = [e for e in logs if e["event"] == "extraction.claim_invalid"]
    assert len(invalid) == 1
    assert invalid[0]["predicate_key"] == "sites"


async def test_extract_writes_in_one_transaction() -> None:
    gw = _make_gateway(_resp([_claim(PREDICATE_KEY, True)]))
    pool = FakePool(_fake_source_record(), _fake_predicates())

    await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    assert [e[0] for e in pool.tx_events] == ["enter", "exit"]
    assert pool.tx_events[1][2] is None
    tx_conn = pool.tx_events[0][1]
    writes = ("create_entity", "create_mention", "resolve_mention", "create_claim")
    write_conns = {cid for cid, sql, _ in pool.log if any(w in sql for w in writes)}
    assert write_conns == {tx_conn}


async def test_extract_failure_rolls_back() -> None:
    gw = _make_gateway(_resp([_claim(PREDICATE_KEY, True)]))
    pool = FakePool(_fake_source_record(), _fake_predicates(), fail_on="app.create_claim")

    with pytest.raises(RuntimeError):
        await extract_structured(pool, gw, SOURCE_RECORD_ID, _ctx())

    exits = [e for e in pool.tx_events if e[0] == "exit"]
    assert len(exits) == 1
    assert isinstance(exits[0][2], RuntimeError)  # the transaction saw the error: rollback
    tx_conn = exits[0][1]
    failed = [
        (cid, args)
        for cid, sql, args in pool.log
        if "update_extraction_status" in sql and args[1] == "failed"
    ]
    assert len(failed) == 1
    assert failed[0][0] != tx_conn  # written on a fresh connection, after the rollback


def test_parse_source_date() -> None:
    fetched = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    assert parse_source_date("2026-11-02", fetched) == datetime(2026, 11, 2, tzinfo=UTC)
    assert parse_source_date(None, fetched) == fetched
    assert parse_source_date("2027-01-01", fetched) == fetched
    assert parse_source_date("Nov 2026", fetched) == fetched


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
