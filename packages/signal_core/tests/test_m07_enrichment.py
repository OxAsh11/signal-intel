"""Tests for M7 enrichment engine."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from signal_core.modules.m07_enrichment import enrich_account, StubProvider, PROVIDERS
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
async def test_enrich_account_never_pay_twice():
    """When a valid contact_point exists within 30 days, provider.lookup is not called."""
    org_id = uuid4()
    entity_id = uuid4()
    existing_cp_id = uuid4()

    pool = _make_pool(
        # account row: org_id + entity_id
        (org_id, entity_id),
        # entity display_name
        ("ACME Corp",),
        # entity domain
        ("acme.com",),
        # existing valid contact_point (never-pay-twice check)
        (existing_cp_id,),
    )

    # Create a provider that tracks calls
    spy_provider = StubProvider()
    spy_provider.lookup = AsyncMock(return_value=None)

    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "enrichment.run", {})

    with patch("signal_core.modules.m07_enrichment.PROVIDERS", [spy_provider]):
        result = await enrich_account(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == 0
    spy_provider.lookup.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_account_suppression_skip():
    """When an invalid contact_point exists for the same type, provider.lookup is not called."""
    org_id = uuid4()
    entity_id = uuid4()
    existing_invalid_cp_id = uuid4()

    pool = _make_pool(
        # account row
        (org_id, entity_id),
        # entity display_name
        ("ACME Corp",),
        # entity domain
        ("acme.com",),
        # existing valid contact_point: None (not found)
        None,
        # existing invalid contact_point: found
        (existing_invalid_cp_id,),
    )

    spy_provider = StubProvider()
    spy_provider.lookup = AsyncMock(return_value=None)

    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "enrichment.run", {})

    with patch("signal_core.modules.m07_enrichment.PROVIDERS", [spy_provider]):
        result = await enrich_account(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == 0
    spy_provider.lookup.assert_not_called()


@pytest.mark.asyncio
async def test_enrich_account_waterfall_stops_at_first_valid():
    """When StubProvider returns a valid result, create_contact_point is called once and count is 1."""
    org_id = uuid4()
    entity_id = uuid4()
    new_cp_id = uuid4()

    pool = _make_pool(
        # account row
        (org_id, entity_id),
        # entity display_name
        ("ACME Corp",),
        # entity domain
        ("acme.com",),
        # existing valid contact_point: None
        None,
        # existing invalid contact_point: None
        None,
        # app.create_contact_point result
        (new_cp_id,),
    )

    # Provider that returns a valid result
    valid_provider = StubProvider()
    valid_provider.lookup = AsyncMock(
        return_value={
            "type": "email",
            "value_encrypted": "stub@example.com",
            "status": "valid",
        }
    )

    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "enrichment.run", {})

    with patch("signal_core.modules.m07_enrichment.PROVIDERS", [valid_provider]):
        result = await enrich_account(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == 1
    valid_provider.lookup.assert_called_once()


@pytest.mark.asyncio
async def test_enrich_account_no_account_returns_zero():
    """When account is not found in DB, enrich_account returns 0."""
    # account query returns None
    pool = _make_pool(None)

    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "enrichment.run", {})

    spy_provider = StubProvider()
    spy_provider.lookup = AsyncMock(return_value=None)

    with patch("signal_core.modules.m07_enrichment.PROVIDERS", [spy_provider]):
        result = await enrich_account(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == 0
    spy_provider.lookup.assert_not_called()
