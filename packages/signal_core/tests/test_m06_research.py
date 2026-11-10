"""Tests for M6 research engine."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from signal_core.modules.m06_research import generate_research_doc, ResearchOutput, ResearchSection
from signal_core.metering import CallContext


def _make_pool(*query_results):
    """
    Build a mock pool where each call to pool.connection() returns a fresh
    connection whose cursor's fetchone / fetchall return the next item from
    *query_results in order.

    Each element in query_results can be:
      - a single row (tuple/None) → returned by fetchone
      - a list of rows            → returned by fetchall
    """
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)

    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    cur.execute = AsyncMock()

    fetchone_results = []
    fetchall_results = []

    for r in query_results:
        if isinstance(r, list):
            fetchall_results.append(r)
        else:
            fetchone_results.append(r)

    cur.fetchone = AsyncMock(side_effect=fetchone_results)
    cur.fetchall = AsyncMock(side_effect=fetchall_results)

    return pool


@pytest.mark.asyncio
async def test_generate_research_doc_no_brief_version_returns_none():
    """When no published brief_version exists, generate_research_doc returns None."""
    pool = _make_pool(None)  # brief_version query returns None

    gateway = MagicMock()
    gateway.run = AsyncMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "research.generate", {})

    result = await generate_research_doc(pool, gateway, uuid4(), uuid4(), ctx)

    assert result is None
    gateway.run.assert_not_called()


@pytest.mark.asyncio
async def test_generate_research_doc_existing_doc_returns_early():
    """When an existing research_doc exists with no newer claims, return early without calling AI."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    existing_doc_id = uuid4()

    import datetime
    published_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    # latest_claim_at is before published_at (None simulates no claims)

    pool = _make_pool(
        # brief_version
        (bv_id, org_id, "test hypothesis", [{"title": "Overview"}]),
        # account row
        (entity_id, org_id),
        # entity display_name
        ("ACME Corp",),
        # existing research_doc
        (existing_doc_id, published_at),
        # latest claim created_at — None means no claims after the doc
        (None,),
    )

    gateway = MagicMock()
    gateway.run = AsyncMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "research.generate", {})

    result = await generate_research_doc(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == str(existing_doc_id)
    gateway.run.assert_not_called()


@pytest.mark.asyncio
async def test_generate_research_doc_calls_gateway_and_creates_doc():
    """When no existing doc, gateway is called and app.create_research_doc is called."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    source_id = str(uuid4())
    new_doc_id = uuid4()

    pool = _make_pool(
        # brief_version
        (bv_id, org_id, "test hypothesis", [{"title": "Overview"}]),
        # account row
        (entity_id, org_id),
        # entity display_name
        ("ACME Corp",),
        # no existing research_doc
        None,
        # source records (fetchall — not a fetchone)
        [(source_id, "Some content about the company", None)],
        # app.create_research_doc result (create_claim uses execute only, no fetchone)
        (new_doc_id,),
    )

    # Build a fake gateway result
    section = ResearchSection(
        title="Overview",
        content=f"ACME Corp is growing. [SOURCE:{source_id}]",
    )
    research_output = ResearchOutput(sections=[section])

    gateway_result = MagicMock()
    gateway_result.value = research_output
    gateway_result.extractor_version_id = uuid4()

    gateway = MagicMock()
    gateway.run = AsyncMock(return_value=gateway_result)

    ctx = CallContext(uuid4(), uuid4(), None, None, "research.generate", {})

    result = await generate_research_doc(pool, gateway, uuid4(), uuid4(), ctx)

    gateway.run.assert_called_once()
    assert result == str(new_doc_id)


@pytest.mark.asyncio
async def test_generate_research_doc_writeback_skips_on_exception():
    """When app.create_claim raises, the exception is swallowed and the function completes."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    source_id = str(uuid4())
    new_doc_id = uuid4()

    # We need to simulate that create_claim raises but create_research_doc succeeds.
    # We'll patch the pool to raise on the create_claim execute call.
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    # Track call count so we can raise on create_claim call (call #6)
    call_count = [0]
    fetchone_responses = [
        # brief_version
        (bv_id, org_id, "test hypothesis", [{"title": "Overview"}]),
        # account row
        (entity_id, org_id),
        # entity display_name
        ("ACME Corp",),
        # no existing research_doc
        None,
        # app.create_research_doc (create_claim raises before fetchone, so no slot needed)
        (new_doc_id,),
    ]
    fetchall_responses = [
        # source records
        [(source_id, "ACME content", None)],
    ]

    fetchone_idx = [0]
    fetchall_idx = [0]

    async def fetchone_side_effect():
        idx = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_responses[idx]

    async def fetchall_side_effect():
        idx = fetchall_idx[0]
        fetchall_idx[0] += 1
        return fetchall_responses[idx]

    # On create_claim execute, raise an exception
    execute_count = [0]

    async def execute_side_effect(sql, params=None):
        execute_count[0] += 1
        # The 5th execute call is create_claim (after bv, account, entity, existing_doc, sources)
        if "create_claim" in sql:
            raise Exception("claim already exists")

    cur.execute = AsyncMock(side_effect=execute_side_effect)
    cur.fetchone = AsyncMock(side_effect=fetchone_side_effect)
    cur.fetchall = AsyncMock(side_effect=fetchall_side_effect)

    section = ResearchSection(
        title="Overview",
        content=f"ACME Corp is growing. [SOURCE:{source_id}]",
    )
    research_output = ResearchOutput(sections=[section])

    gateway_result = MagicMock()
    gateway_result.value = research_output
    gateway_result.extractor_version_id = uuid4()

    gateway = MagicMock()
    gateway.run = AsyncMock(return_value=gateway_result)

    ctx = CallContext(uuid4(), uuid4(), None, None, "research.generate", {})

    # Should complete without raising despite create_claim failure
    result = await generate_research_doc(pool, gateway, uuid4(), uuid4(), ctx)

    # Doc should still be created
    assert result == str(new_doc_id)
