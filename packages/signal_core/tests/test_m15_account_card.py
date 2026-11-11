"""Tests for M15 account card generation."""

from __future__ import annotations

import datetime
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from signal_core.metering import CallContext
from signal_core.modules.m15_account_card import (
    AccountCardOutput,
    generate_account_card,
)


def _make_pool(*query_results):
    """
    Build a mock pool where fetchone and fetchall calls return results in
    sequence order, split by type (list → fetchall, other → fetchone).
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
async def test_generate_account_card_no_brief_version_returns_none():
    """When no published brief_version exists, generate_account_card returns None."""
    pool = _make_pool(None)  # brief_version query returns None

    gateway = MagicMock()
    gateway.run = AsyncMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "account_card.generate", {})

    result = await generate_account_card(pool, gateway, uuid4(), uuid4(), ctx)

    assert result is None
    gateway.run.assert_not_called()


@pytest.mark.asyncio
async def test_generate_account_card_existing_current_card_returns_early():
    """When an existing account_card is more recent than the latest verdict, return early."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    card_id = uuid4()

    card_time = datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
    verdict_time = datetime.datetime(2026, 5, 1, 12, 0, 0, tzinfo=datetime.UTC)

    pool = _make_pool(
        # 1. brief_version
        (bv_id, org_id, "test hypothesis"),
        # 2. account entity_id
        (entity_id,),
        # 3. entity display_name
        ("ACME Corp",),
        # 4. existing card (card_time > verdict_time → early return)
        (card_id, card_time),
        # 5. latest verdict computed_at
        (verdict_time,),
    )

    gateway = MagicMock()
    gateway.run = AsyncMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "account_card.generate", {})

    result = await generate_account_card(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == str(card_id)
    gateway.run.assert_not_called()


@pytest.mark.asyncio
async def test_generate_account_card_calls_gateway():
    """When no existing card, gateway is called and app.upsert_account_card is called."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    new_card_id = uuid4()
    claim_id = uuid4()

    pool = _make_pool(
        # 1. brief_version
        (bv_id, org_id, "test hypothesis"),
        # 2. account entity_id
        (entity_id,),
        # 3. entity display_name
        ("ACME Corp",),
        # 4. no existing card
        None,
        # 5. latest verdict
        ("Confirmed", [{"claim_id": str(claim_id), "quote": "strong growth"}]),
        # 6. latest score
        ("A", 90, 85, 80, 75, 70),
        # 7. account stage
        ("prospect",),
        # 8. claims (fetchall)
        [(claim_id, "revenue", "$10M ARR")],
        # 9. contact coverage
        (3,),
        # 10. upsert result
        (new_card_id,),
    )

    card_output = AccountCardOutput(
        bullets=[
            "ACME Corp is a $10M ARR company [CLAIM:some-id]",
            "Strong fit for our ICP [CLAIM:some-id]",
        ],
        next_step="Schedule discovery call with VP Sales",
    )
    gateway_result = MagicMock()
    gateway_result.value = card_output
    gateway_result.extractor_version_id = uuid4()

    gateway = MagicMock()
    gateway.run = AsyncMock(return_value=gateway_result)
    ctx = CallContext(uuid4(), uuid4(), None, None, "account_card.generate", {})

    result = await generate_account_card(pool, gateway, uuid4(), uuid4(), ctx)

    gateway.run.assert_called_once()
    assert result == str(new_card_id)


@pytest.mark.asyncio
async def test_generate_account_card_bullets_format():
    """Verify that the gateway output has bullets and next_step, and content is persisted."""
    bv_id = uuid4()
    org_id = uuid4()
    entity_id = uuid4()
    new_card_id = uuid4()

    # Capture the execute calls to verify content_json structure
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    fetchone_responses = [
        (bv_id, org_id, "hypothesis"),  # brief_version
        (entity_id,),  # account entity_id
        ("Test Corp",),  # display_name
        None,  # no existing card
        ("Likely", []),  # verdict
        ("B", 75, 70, 65, 60, 55),  # score
        ("lead",),  # stage
        (2,),  # contact coverage
        (new_card_id,),  # upsert result
    ]
    fetchall_responses = [
        [],  # no claims
    ]

    fetchone_idx = [0]
    fetchall_idx = [0]

    async def fetchone_side():
        idx = fetchone_idx[0]
        fetchone_idx[0] += 1
        return fetchone_responses[idx]

    async def fetchall_side():
        idx = fetchall_idx[0]
        fetchall_idx[0] += 1
        return fetchall_responses[idx]

    # Capture execute arguments
    captured_executes = []

    async def execute_side(sql, params=None):
        captured_executes.append((sql, params))

    cur.execute = AsyncMock(side_effect=execute_side)
    cur.fetchone = AsyncMock(side_effect=fetchone_side)
    cur.fetchall = AsyncMock(side_effect=fetchall_side)

    expected_bullets = [
        "Test Corp is a growing company [CLAIM:abc]",
        "Strong timing signal [ACTIVITY:xyz]",
    ]
    expected_next_step = "Reach out to CTO within 2 weeks"
    card_output = AccountCardOutput(bullets=expected_bullets, next_step=expected_next_step)

    gateway_result = MagicMock()
    gateway_result.value = card_output
    gateway_result.extractor_version_id = uuid4()

    gateway = MagicMock()
    gateway.run = AsyncMock(return_value=gateway_result)
    ctx = CallContext(uuid4(), uuid4(), None, None, "account_card.generate", {})

    result = await generate_account_card(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == str(new_card_id)

    # Find the upsert_account_card call and verify content_json
    upsert_call = next(
        (sql, params) for sql, params in captured_executes if "upsert_account_card" in sql
    )
    content_json_str = upsert_call[1][2]  # third param is content_json
    content = json.loads(content_json_str)
    assert "bullets" in content
    assert "next_step" in content
    assert content["bullets"] == expected_bullets
    assert content["next_step"] == expected_next_step
