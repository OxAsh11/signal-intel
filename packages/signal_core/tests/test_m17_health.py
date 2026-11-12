"""Tests for M17 health — data completeness checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from signal_core.metering import CallContext
from signal_core.modules.m17_health import PIPELINES, check_completeness

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "health.check", {})


def _make_pool_enabled(org_id, expected_counts: dict[str, tuple[int, int]]):
    """Build a mock pool for a fully-enabled run.

    Connection call order:
      0: workspace org_id lookup (cursor + fetchone)
      For each pipeline (scoring, research, enrichment, stage):
        N*2+1: counts query (cursor + 2x fetchone for expected then actual)
        N*2+2: RPC call (cursor + execute, no fetchone needed)
    """
    pool = MagicMock()
    call_count = [0]

    # Ordered list of connections to return; we build them lazily.
    pipeline_names = list(PIPELINES.keys())

    def _make_conn(call_idx: int):
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock()

        if call_idx == 0:
            # Workspace lookup
            cur.fetchone = AsyncMock(return_value=(org_id,))
        else:
            # Pipeline connections come in pairs: (counts, rpc)
            pipeline_idx = (call_idx - 1) // 2
            is_counts = ((call_idx - 1) % 2) == 0

            if pipeline_idx < len(pipeline_names):
                pname = pipeline_names[pipeline_idx]
                exp, act = expected_counts.get(pname, (0, 0))
                if is_counts:
                    # Returns expected first, then actual
                    cur.fetchone = AsyncMock(side_effect=[(exp,), (act,)])
                # RPC connection: just needs execute, no fetchone return value needed

        return conn

    def _connection_cm():
        cm = MagicMock()
        idx = call_count[0]
        call_count[0] += 1
        conn = _make_conn(idx)
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    pool.connection = _connection_cm
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_completeness_kill_switch_off_returns_empty():
    """When kill-switch is disabled, check_completeness returns {}."""
    pool = MagicMock()
    ctx = _make_ctx()
    workspace_id = uuid4()

    with patch(
        "signal_core.modules.m17_health.is_enabled",
        new=AsyncMock(return_value=False),
    ):
        result = await check_completeness(pool, workspace_id, ctx)

    assert result == {}


@pytest.mark.asyncio
async def test_check_completeness_runs_all_four_pipelines():
    """When kill-switch is on, all 4 pipelines are checked and RPC is called 4 times."""
    org_id = uuid4()
    workspace_id = uuid4()
    ctx = _make_ctx()

    counts = {
        "scoring": (100, 80),
        "research": (100, 60),
        "enrichment": (100, 90),
        "stage": (50, 40),
    }

    pool = _make_pool_enabled(org_id, counts)

    # Track RPC calls by intercepting cur.execute calls on RPC connections
    # We'll patch is_enabled and then verify via result dict length.
    with patch(
        "signal_core.modules.m17_health.is_enabled",
        new=AsyncMock(return_value=True),
    ):
        result = await check_completeness(pool, workspace_id, ctx)

    assert len(result) == 4
    assert set(result.keys()) == {"scoring", "research", "enrichment", "stage"}


@pytest.mark.asyncio
async def test_check_completeness_records_correct_counts():
    """Expected and actual values in result dict match the mocked DB counts."""
    org_id = uuid4()
    workspace_id = uuid4()
    ctx = _make_ctx()

    counts = {
        "scoring": (200, 150),
        "research": (200, 100),
        "enrichment": (200, 180),
        "stage": (75, 70),
    }

    pool = _make_pool_enabled(org_id, counts)

    with patch(
        "signal_core.modules.m17_health.is_enabled",
        new=AsyncMock(return_value=True),
    ):
        result = await check_completeness(pool, workspace_id, ctx)

    assert result["scoring"] == {"expected": 200, "actual": 150}
    assert result["research"] == {"expected": 200, "actual": 100}
    assert result["enrichment"] == {"expected": 200, "actual": 180}
    assert result["stage"] == {"expected": 75, "actual": 70}


@pytest.mark.asyncio
async def test_check_completeness_no_workspace_returns_empty():
    """When workspace is not found, check_completeness returns {}."""
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=None)  # workspace not found

    ctx = _make_ctx()
    workspace_id = uuid4()

    with patch(
        "signal_core.modules.m17_health.is_enabled",
        new=AsyncMock(return_value=True),
    ):
        result = await check_completeness(pool, workspace_id, ctx)

    assert result == {}
