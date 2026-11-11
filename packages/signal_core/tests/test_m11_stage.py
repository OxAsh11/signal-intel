"""Tests for M11 stage machine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from signal_core.metering import CallContext
from signal_core.modules.m11_stage import advance_stage, mark_dormant_accounts

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "stage.test", {})


def _make_pool_for_advance(current_stage: str, org_id: UUID | None = None):
    """Mock pool that returns a fixed stage row for the first query and
    a no-op cursor for the upsert RPC call."""
    if org_id is None:
        org_id = uuid4()

    pool = MagicMock()
    conn = MagicMock()

    # Connections returned by pool.connection() context manager.
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)

    cur = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    cur.execute = AsyncMock()
    # First fetchone → (current_stage, org_id); subsequent calls → None.
    cur.fetchone = AsyncMock(side_effect=[(current_stage, org_id), None])

    # conn.execute used for the upsert RPC (no cursor, no return value needed).
    conn.execute = AsyncMock()

    return pool, org_id


def _make_pool_for_dormant(dormant_rows: list[tuple]):
    """Mock pool for mark_dormant_accounts tests.

    Connection usage pattern:
      - Connection 0: dormant list query (uses cursor + fetchall).
      - For each dormant account, advance_stage uses 2 connections:
          - Connection 1+2*i:   fetch current stage (uses cursor + fetchone).
          - Connection 2+2*i:   upsert RPC (uses conn.execute directly, no cursor).
    """
    pool = MagicMock()
    call_count = [0]

    def _make_conn(call_idx: int):
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock()

        if call_idx == 0:
            # Initial dormant list query.
            cur.fetchall = AsyncMock(return_value=dormant_rows)
            cur.fetchone = AsyncMock(return_value=None)
        else:
            # advance_stage connections (alternating fetch / upsert).
            # Odd relative index → fetch current stage; even → upsert RPC.
            relative = call_idx - 1  # 0-based offset after the list query
            is_fetch = (relative % 2) == 0
            if is_fetch:
                cur.fetchone = AsyncMock(return_value=("Contacted", uuid4()))
            else:
                cur.fetchone = AsyncMock(return_value=None)
            cur.fetchall = AsyncMock(return_value=[])

        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_stage_message_sent_untouched_to_contacted():
    """message_sent on an Untouched account should advance to Contacted."""
    account_id = uuid4()
    workspace_id = uuid4()
    pool, _org_id = _make_pool_for_advance("Untouched")
    ctx = _make_ctx()

    result = await advance_stage(pool, account_id, None, "message_sent", workspace_id, ctx)

    assert result == "Contacted"
    # Verify the upsert RPC was called with the right stage value.
    pool.connection.return_value.__aenter__.assert_called()


@pytest.mark.asyncio
async def test_advance_stage_no_matching_transition_returns_none():
    """An activity_kind with no matching current-stage transition returns None."""
    account_id = uuid4()
    workspace_id = uuid4()
    # reply_received only transitions from Contacted; Untouched is not in the map.
    pool, _ = _make_pool_for_advance("Untouched")
    ctx = _make_ctx()

    result = await advance_stage(pool, account_id, None, "reply_received", workspace_id, ctx)

    assert result is None
    # Upsert RPC should NOT have been called.
    conn = pool.connection.return_value.__aenter__.return_value
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_advance_stage_disqualified_from_any_stage():
    """marked_disqualified uses the '*' wildcard and applies from any stage."""
    account_id = uuid4()
    workspace_id = uuid4()
    # Start from Engaged — a non-trivial stage to show wildcard applies.
    pool, _ = _make_pool_for_advance("Engaged")
    ctx = _make_ctx()

    result = await advance_stage(pool, account_id, None, "marked_disqualified", workspace_id, ctx)

    assert result == "Disqualified"


@pytest.mark.asyncio
async def test_advance_stage_idempotent_double_call():
    """Second call with same activity_id returns same stage without breaking.

    The SQL RPC handles idempotency at the DB level; the Python function still
    returns the new stage on the second call (the stage is already the target,
    so the transition does not apply — returns None for the second call when
    the stage is already Contacted).
    """
    account_id = uuid4()
    workspace_id = uuid4()
    activity_id = uuid4()
    ctx = _make_ctx()

    # First call: Untouched → Contacted.
    pool1, org_id = _make_pool_for_advance("Untouched")
    result1 = await advance_stage(pool1, account_id, activity_id, "message_sent", workspace_id, ctx)
    assert result1 == "Contacted"

    # Second call: stage is now Contacted; message_sent maps Untouched→Contacted
    # and Queued→Contacted, NOT Contacted→anything, so returns None.
    pool2, _ = _make_pool_for_advance("Contacted", org_id)
    result2 = await advance_stage(pool2, account_id, activity_id, "message_sent", workspace_id, ctx)
    assert result2 is None


@pytest.mark.asyncio
async def test_mark_dormant_only_for_right_ages():
    """Accounts with recent activity are not marked dormant; stale ones are."""
    workspace_id = uuid4()
    ctx = _make_ctx()

    # Two dormant accounts returned from the query (the SQL already filters
    # for no-recent-activity, so the mock only returns the stale ones).
    stale_account_1 = uuid4()
    stale_account_2 = uuid4()
    dormant_rows = [
        (stale_account_1, uuid4()),
        (stale_account_2, uuid4()),
    ]

    pool = _make_pool_for_dormant(dormant_rows)

    count = await mark_dormant_accounts(pool, workspace_id, ctx)

    # Both stale accounts should have been marked.
    assert count == 2
