"""Tests for M9 Activation module.

All DB calls are mocked — no real DB needed.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

from signal_core.metering import CallContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), uuid4(), None, "activation.test", {})


def _make_pool(
    fetchone_sequence: list,
    fetchall_sequence: list | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build a mock pool where all connections share one conn + one cursor.

    fetchone_sequence: values returned by successive cur.fetchone() calls.
    fetchall_sequence: values returned by successive cur.fetchall() calls.

    Returns (pool, conn, cur) so callers can assert on conn.execute calls.
    """
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)

    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)

    cur.execute = AsyncMock()
    conn.execute = AsyncMock()

    cur.fetchone = AsyncMock(side_effect=fetchone_sequence)
    if fetchall_sequence is not None:
        cur.fetchall = AsyncMock(side_effect=fetchall_sequence)
    else:
        cur.fetchall = AsyncMock(return_value=[])

    return pool, conn, cur


# ---------------------------------------------------------------------------
# Tests — create_campaign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_campaign_returns_uuid():
    """create_campaign should return the UUID produced by app.create_campaign RPC."""
    from signal_core.modules.m09_activation import create_campaign

    ctx = _make_ctx()
    brief_id = uuid4()
    new_campaign_id = uuid4()

    pool, conn, cur = _make_pool(fetchone_sequence=[(str(new_campaign_id),)])

    result = await create_campaign(pool, ctx, "Q4 Target", "Vertical", brief_id)

    assert isinstance(result, UUID)
    assert result == new_campaign_id
    cur.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_campaign_with_destination_integration():
    """create_campaign passes destination_integration_id when provided."""
    from signal_core.modules.m09_activation import create_campaign

    ctx = _make_ctx()
    brief_id = uuid4()
    dest_id = uuid4()
    new_campaign_id = uuid4()

    pool, conn, cur = _make_pool(fetchone_sequence=[(str(new_campaign_id),)])

    result = await create_campaign(pool, ctx, "Q4 Target", "Vertical", brief_id, dest_id)

    assert isinstance(result, UUID)
    # Verify destination_integration_id was passed into the SQL call
    call_args = cur.execute.call_args
    assert str(dest_id) in call_args[0][1]


# ---------------------------------------------------------------------------
# Tests — push_campaign_batch kill switch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_kill_switch_raises_error():
    """push_mode='off' must raise KillSwitchError immediately."""
    from signal_core.modules.m09_activation import push_campaign_batch, KillSwitchError

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "off", None)
    pool, conn, cur = _make_pool(fetchone_sequence=[campaign_row])

    with pytest.raises(KillSwitchError):
        await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)


# ---------------------------------------------------------------------------
# Tests — push_campaign_batch dry run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_dry_run_records_no_members(mock_pool=None):
    """In 'dry run' mode: pushed==0 (no campaign_member rows) but external write recorded."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    person_entity_id = uuid4()
    account_id = uuid4()
    cp_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "dry run", None)
    candidates = [(str(account_id), str(person_entity_id), str(cp_id))]

    # fetchone sequence: campaign row, suppression=None, existing_campaign=None
    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row, None, None],
        fetchall_sequence=[candidates],
    )

    result = await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)

    assert result["pushed"] == 0
    assert result["skipped"] == 0
    assert result["paused"] is False

    # dry_run=True must appear in the external write RPC call
    conn.execute.assert_called_once()
    call_args_list = conn.execute.call_args_list
    assert len(call_args_list) == 1
    # The 9th positional arg (index 8) is dry_run; True expected
    rpc_params = call_args_list[0][0][1]
    assert rpc_params[8] is True


# ---------------------------------------------------------------------------
# Tests — push_campaign_batch suppression
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_skips_suppressed_person():
    """A person in the suppression_list must be skipped (skipped >= 1)."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    person_entity_id = uuid4()
    account_id = uuid4()
    cp_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "live", None)
    candidates = [(str(account_id), str(person_entity_id), str(cp_id))]

    # suppression check returns a row → person is suppressed
    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row, (1,)],
        fetchall_sequence=[candidates],
    )

    result = await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)

    assert result["skipped"] >= 1
    assert result["pushed"] == 0
    # No campaign_member rows, no external write records
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_push_skips_person_already_in_active_campaign():
    """A person already in an active campaign in this workspace must be skipped."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    person_entity_id = uuid4()
    account_id = uuid4()
    cp_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "live", None)
    candidates = [(str(account_id), str(person_entity_id), str(cp_id))]

    # suppression=None, existing_campaign returns a row → person already in a campaign
    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row, None, (1,)],
        fetchall_sequence=[candidates],
    )

    result = await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)

    assert result["skipped"] >= 1
    assert result["pushed"] == 0
    conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Tests — push_campaign_batch bounce guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_pauses_at_bounce_threshold():
    """After every _bounce_interval pushes (live mode), check_and_pause_if_bouncing is called.
    If it returns True, paused=True and loop stops."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    person_entity_id = uuid4()
    account_id = uuid4()
    cp_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "live", None)
    candidates = [(str(account_id), str(person_entity_id), str(cp_id))]

    # fetchone sequence:
    #   1. campaign row
    #   2. suppression check → None
    #   3. existing campaign check → None
    #   4. bounce check → (True,)   [triggered because _bounce_interval=1]
    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row, None, None, (True,)],
        fetchall_sequence=[candidates],
    )

    result = await push_campaign_batch(
        pool, MagicMock(), campaign_id, ctx, _bounce_interval=1
    )

    assert result["paused"] is True
    assert result["pushed"] == 1


# ---------------------------------------------------------------------------
# Tests — push_campaign_batch live mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_live_creates_members():
    """In live mode, one eligible candidate produces pushed=1."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    person_entity_id = uuid4()
    account_id = uuid4()
    cp_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "live", None)
    candidates = [(str(account_id), str(person_entity_id), str(cp_id))]

    # fetchone sequence: campaign row, suppression=None, existing_campaign=None
    # bounce check not called (1 push, interval=100 → 1 % 100 != 0)
    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row, None, None],
        fetchall_sequence=[candidates],
    )

    result = await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)

    assert result["pushed"] == 1
    assert result["skipped"] == 0
    assert result["paused"] is False

    # 2 conn.execute calls: add_campaign_member + record_external_write
    assert conn.execute.call_count == 2
    # The record_external_write call must have dry_run=False (index 8)
    rpc_calls = conn.execute.call_args_list
    # Second call is record_external_write
    rpc_params = rpc_calls[1][0][1]
    assert rpc_params[8] is False


@pytest.mark.asyncio
async def test_push_no_candidates_returns_zeros():
    """When there are no eligible candidates, return all zeros."""
    from signal_core.modules.m09_activation import push_campaign_batch

    ctx = _make_ctx()
    campaign_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()

    campaign_row = (str(campaign_id), str(org_id), str(workspace_id), str(uuid4()), "live", None)

    pool, conn, cur = _make_pool(
        fetchone_sequence=[campaign_row],
        fetchall_sequence=[[]],  # empty candidates
    )

    result = await push_campaign_batch(pool, MagicMock(), campaign_id, ctx)

    assert result == {"pushed": 0, "skipped": 0, "paused": False}
    conn.execute.assert_not_called()
