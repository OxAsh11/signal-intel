"""Tests for M6 research engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from signal_core.metering import CallContext
from signal_core.modules.m06_research import generate_research_doc


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

    published_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
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
