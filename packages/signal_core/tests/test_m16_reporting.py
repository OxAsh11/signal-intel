"""Tests for M16 reporting — compute_metric wrapper and get_funnel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from signal_core.modules.m16_reporting import STAGE_ORDER, compute_metric, get_funnel

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ws_id = uuid4()
brief_id = uuid4()


@pytest.fixture
def mock_pool():
    """Standard mock pool matching the prescribed psycopg3 async pattern."""
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock()
    cur.fetchall = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn, cur


def _make_two_call_pool(first_fetchone, second_fetchone):
    """Pool whose first cursor returns first_fetchone and second returns second_fetchone."""
    call_count = [0]

    pool = MagicMock()

    def _connection_cm():
        cm = MagicMock()
        idx = call_count[0]
        call_count[0] += 1

        conn = MagicMock()
        cur = MagicMock()
        cur.execute = AsyncMock()

        if idx == 0:
            cur.fetchone = AsyncMock(return_value=first_fetchone)
        else:
            cur.fetchone = AsyncMock(return_value=second_fetchone)

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    pool.connection = _connection_cm
    return pool


def _make_funnel_pool(stage_rows, median_rows):
    """Pool for get_funnel: first cursor returns stage_rows, second returns median_rows."""
    call_count = [0]

    pool = MagicMock()

    def _connection_cm():
        cm = MagicMock()
        idx = call_count[0]
        call_count[0] += 1

        conn = MagicMock()
        cur = MagicMock()
        cur.execute = AsyncMock()

        if idx == 0:
            cur.fetchall = AsyncMock(return_value=stage_rows)
        else:
            cur.fetchall = AsyncMock(return_value=median_rows)

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cm.__aenter__ = AsyncMock(return_value=conn)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    pool.connection = _connection_cm
    return pool


# ---------------------------------------------------------------------------
# compute_metric tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_metric_accounts_discovered():
    """compute_metric returns a dict with 'value' for a known key."""
    pool = _make_two_call_pool(
        first_fetchone=(42,),  # app.compute_metric RPC returns 42
        second_fetchone=(1,),  # metric_definition version lookup returns 1
    )
    result = await compute_metric(pool, ws_id, brief_id, "accounts_discovered")
    assert "value" in result
    assert result["value"] == 42
    assert isinstance(result["value"], int)


@pytest.mark.asyncio
async def test_compute_metric_returns_required_keys():
    """Result dict contains value, metric_key, computed_at, definition_version."""
    pool = _make_two_call_pool(
        first_fetchone=(10,),
        second_fetchone=(1,),
    )
    result = await compute_metric(pool, ws_id, brief_id, "messages_sent")
    assert set(result.keys()) >= {"value", "metric_key", "computed_at", "definition_version"}
    assert result["metric_key"] == "messages_sent"
    assert isinstance(result["definition_version"], int)
    assert isinstance(result["computed_at"], str)


@pytest.mark.asyncio
async def test_compute_metric_unknown_key_raises():
    """compute_metric raises ValueError with 'unknown metric' for a NULL RPC response."""
    pool = _make_two_call_pool(
        first_fetchone=(None,),  # RPC returns NULL
        second_fetchone=(1,),
    )
    with pytest.raises(ValueError, match="unknown metric"):
        await compute_metric(pool, ws_id, brief_id, "not_a_metric")


@pytest.mark.asyncio
async def test_compute_metric_row_none_raises():
    """compute_metric raises ValueError when cursor returns no row at all."""
    pool = _make_two_call_pool(
        first_fetchone=None,  # fetchone returns None (no row)
        second_fetchone=(1,),
    )
    with pytest.raises(ValueError, match="unknown metric"):
        await compute_metric(pool, ws_id, brief_id, "not_a_metric")


@pytest.mark.asyncio
async def test_compute_metric_float_value():
    """compute_metric handles float values (e.g. reply_rate)."""
    pool = _make_two_call_pool(
        first_fetchone=(0.0325,),
        second_fetchone=(1,),
    )
    result = await compute_metric(pool, ws_id, brief_id, "reply_rate")
    assert result["value"] == pytest.approx(0.0325)


@pytest.mark.asyncio
async def test_compute_metric_passes_filters():
    """compute_metric passes filters JSON to the RPC."""
    captured_args: list = []

    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    async def capture_execute(sql, args):
        captured_args.append(args)

    cur.execute = capture_execute
    cur.fetchone = AsyncMock(return_value=(5,))
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)

    filters = {"band": "High"}
    await compute_metric(pool, ws_id, brief_id, "accounts_discovered", filters=filters)

    # First call args: (workspace_id, brief_id, metric_key, filters_json)
    assert captured_args[0][3] == '{"band": "High"}'


# ---------------------------------------------------------------------------
# get_funnel tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_funnel_returns_all_stages():
    """get_funnel returns a row for every stage in STAGE_ORDER."""
    pool = _make_funnel_pool(
        stage_rows=[("Untouched", 100), ("Contacted", 30)],
        median_rows=[],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    stage_names = [row["stage"] for row in funnel]
    assert "Untouched" in stage_names
    assert "Contacted" in stage_names
    assert len(funnel) == len(STAGE_ORDER)


@pytest.mark.asyncio
async def test_get_funnel_stage_order():
    """Funnel rows are ordered by STAGE_ORDER."""
    pool = _make_funnel_pool(
        stage_rows=[("Qualified", 5), ("Untouched", 200), ("Contacted", 50)],
        median_rows=[],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    assert [row["stage"] for row in funnel] == STAGE_ORDER


@pytest.mark.asyncio
async def test_get_funnel_counts():
    """Funnel rows include correct stage counts."""
    pool = _make_funnel_pool(
        stage_rows=[("Untouched", 150), ("Queued", 80), ("Contacted", 40)],
        median_rows=[],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    by_stage = {r["stage"]: r for r in funnel}
    assert by_stage["Untouched"]["count"] == 150
    assert by_stage["Queued"]["count"] == 80
    assert by_stage["Contacted"]["count"] == 40
    # Stages with no DB rows default to 0
    assert by_stage["Disqualified"]["count"] == 0


@pytest.mark.asyncio
async def test_get_funnel_conversion_rate():
    """Conversion rate is next_stage_count / current_stage_count."""
    pool = _make_funnel_pool(
        stage_rows=[("Untouched", 100), ("Queued", 40)],
        median_rows=[],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    by_stage = {r["stage"]: r for r in funnel}
    # Queued / Untouched = 40 / 100
    assert by_stage["Queued"]["conversion_rate"] == pytest.approx(0.4)
    # Untouched has no predecessor → None
    assert by_stage["Untouched"]["conversion_rate"] is None


@pytest.mark.asyncio
async def test_get_funnel_zero_predecessor_conversion_none():
    """When a predecessor stage has 0 count, conversion_rate is None."""
    pool = _make_funnel_pool(
        stage_rows=[("Contacted", 10)],
        median_rows=[],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    by_stage = {r["stage"]: r for r in funnel}
    # Untouched=0, Queued's conversion_rate should be None
    assert by_stage["Queued"]["conversion_rate"] is None


@pytest.mark.asyncio
async def test_get_funnel_median_days():
    """Median days in stage is populated from account_stage_event."""
    pool = _make_funnel_pool(
        stage_rows=[("Contacted", 20)],
        median_rows=[("Contacted", 3.5)],
    )
    funnel = await get_funnel(pool, ws_id, brief_id)
    by_stage = {r["stage"]: r for r in funnel}
    assert by_stage["Contacted"]["median_days_in_stage"] == pytest.approx(3.5)
    # Stage without median data should be None
    assert by_stage["Untouched"]["median_days_in_stage"] is None


@pytest.mark.asyncio
async def test_get_funnel_row_keys():
    """Each funnel row has the required keys."""
    pool = _make_funnel_pool(stage_rows=[], median_rows=[])
    funnel = await get_funnel(pool, ws_id, brief_id)
    for row in funnel:
        assert set(row.keys()) >= {"stage", "count", "conversion_rate", "median_days_in_stage"}
