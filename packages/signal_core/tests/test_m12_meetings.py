"""Tests for M12 Meetings + Tasks — meeting proof rule, attribution, task creation,
and next-step suggestion."""

from __future__ import annotations

import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, UUID

from signal_core.modules.m12_meetings import (
    confirm_meeting,
    create_task,
    suggest_next_step,
    sync_meeting,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
WS_ID = uuid4()
INTEG_ID = uuid4()
ACCT_ID = uuid4()
MEETING_ID = uuid4()
CAMPAIGN_ID = uuid4()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Pool factory helpers
# ---------------------------------------------------------------------------

def _make_simple_pool(
    *,
    fetchone_sequence: list,
) -> MagicMock:
    """Build a mock pool where each connection() call returns a conn whose cursor
    fetchone() returns the next item from fetchone_sequence (cycling when exhausted).

    conn.execute is also an AsyncMock (for direct execute calls).
    """
    pool = MagicMock()
    call_count = [0]
    seq = list(fetchone_sequence)

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock()

        fetch_val = seq[idx] if idx < len(seq) else None
        cur.fetchone = AsyncMock(return_value=fetch_val)
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


def _make_tracking_pool() -> tuple[MagicMock, list]:
    """Pool that records every (sql, params) pair passed to conn.execute."""
    pool = MagicMock()
    call_count = [0]
    execute_calls: list[tuple] = []

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=None)
        cur.fetchall = AsyncMock(return_value=[])

        async def _exec(sql, params=None):
            execute_calls.append((sql, params))

        conn.execute = AsyncMock(side_effect=_exec)
        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    return pool, execute_calls


# ---------------------------------------------------------------------------
# sync_meeting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_meeting_matches_domain_to_account():
    """sync_meeting returns a UUID after matching a domain to an account."""
    # First fetchone: entity_alias domain match returns account_id
    # Second fetchone: app.create_meeting RPC returns meeting_id
    pool = _make_simple_pool(
        fetchone_sequence=[
            (str(ACCT_ID),),         # entity_alias → account
            (str(MEETING_ID),),      # create_meeting → meeting UUID
        ]
    )

    meeting_id = await sync_meeting(
        pool, ORG_ID, WS_ID, INTEG_ID, "ext1", _now(), ["acme.com"]
    )
    assert isinstance(meeting_id, UUID)
    assert meeting_id == MEETING_ID


@pytest.mark.asyncio
async def test_sync_meeting_no_domains_uses_null_account():
    """sync_meeting with empty attendee_domains passes NULL account_id to RPC."""
    pool = _make_simple_pool(
        fetchone_sequence=[
            (str(MEETING_ID),),  # create_meeting returns meeting UUID
        ]
    )

    meeting_id = await sync_meeting(
        pool, ORG_ID, WS_ID, INTEG_ID, "ext2", _now(), []
    )
    assert isinstance(meeting_id, UUID)
    # Only one connection opened (no domain lookup)
    assert pool.connection.call_count == 1


@pytest.mark.asyncio
async def test_sync_meeting_unmatched_domain_still_creates_meeting():
    """sync_meeting with no domain match still creates the meeting (account_id=NULL)."""
    pool = _make_simple_pool(
        fetchone_sequence=[
            None,                   # entity_alias: no match
            (str(MEETING_ID),),     # create_meeting returns meeting UUID
        ]
    )

    meeting_id = await sync_meeting(
        pool, ORG_ID, WS_ID, INTEG_ID, "ext3", _now(), ["unknown.io"]
    )
    assert isinstance(meeting_id, UUID)


# ---------------------------------------------------------------------------
# confirm_meeting tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_confirm_meeting_emits_held():
    """confirm_meeting with proof_status='confirmed' calls app.confirm_meeting."""
    pool, execute_calls = _make_tracking_pool()

    # Attribution fetch returns None (no campaign), confirm_meeting call logged.
    await confirm_meeting(pool, MEETING_ID, _now(), "confirmed")

    confirm_sqls = [sql for sql, _ in execute_calls if "app.confirm_meeting" in sql]
    assert confirm_sqls, (
        f"Expected app.confirm_meeting to be called but execute_calls were: {execute_calls}"
    )


@pytest.mark.asyncio
async def test_unconfirmed_meeting_does_not_emit_held():
    """confirm_meeting with proof_status='unconfirmed' is a no-op."""
    pool, execute_calls = _make_tracking_pool()

    await confirm_meeting(pool, MEETING_ID, _now(), "unconfirmed")

    # No DB connections should have been opened.
    assert pool.connection.call_count == 0
    confirm_sqls = [sql for sql, _ in execute_calls if "app.confirm_meeting" in sql]
    assert not confirm_sqls


@pytest.mark.asyncio
async def test_confirm_meeting_has_transcript_accepted():
    """proof_status='has_transcript' is also valid and calls app.confirm_meeting."""
    pool, execute_calls = _make_tracking_pool()

    await confirm_meeting(pool, MEETING_ID, _now(), "has_transcript")

    confirm_sqls = [sql for sql, _ in execute_calls if "app.confirm_meeting" in sql]
    assert confirm_sqls


@pytest.mark.asyncio
async def test_confirm_meeting_attribution_window():
    """confirm_meeting passes campaign_id from attribution window query."""
    pool = MagicMock()
    call_count = [0]
    execute_calls: list[tuple] = []
    # Sequence: first connection is attribution query (cursor.fetchone → campaign_id)
    # Second connection is confirm_meeting RPC (conn.execute)

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()

        if idx == 0:
            # Attribution window query
            cur.fetchone = AsyncMock(return_value=(str(CAMPAIGN_ID),))
        else:
            cur.fetchone = AsyncMock(return_value=None)

        async def _exec(sql, params=None):
            execute_calls.append((sql, params))

        conn.execute = AsyncMock(side_effect=_exec)
        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    await confirm_meeting(pool, MEETING_ID, _now(), "confirmed")

    confirm_sqls = [sql for sql, _ in execute_calls if "app.confirm_meeting" in sql]
    assert confirm_sqls
    # Campaign id should be in the params of the confirm call
    for sql, params in execute_calls:
        if "app.confirm_meeting" in sql:
            assert params is not None
            str_params = [str(p) for p in params if p is not None]
            assert str(CAMPAIGN_ID) in str_params, (
                f"Expected campaign_id {CAMPAIGN_ID} in params {params}"
            )
            break


@pytest.mark.asyncio
async def test_confirm_meeting_explicit_attribution_bypasses_query():
    """When attribution_campaign_id is supplied explicitly, skip the window query."""
    pool, execute_calls = _make_tracking_pool()

    await confirm_meeting(
        pool, MEETING_ID, _now(), "confirmed", attribution_campaign_id=CAMPAIGN_ID
    )

    # Only one connection: the confirm call (no attribution lookup).
    assert pool.connection.call_count == 1
    confirm_sqls = [sql for sql, _ in execute_calls if "app.confirm_meeting" in sql]
    assert confirm_sqls


# ---------------------------------------------------------------------------
# create_task tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_returns_uuid():
    """create_task returns a UUID of the new task."""
    task_id = uuid4()
    pool = _make_simple_pool(fetchone_sequence=[(str(task_id),)])

    result = await create_task(pool, ORG_ID, WS_ID, ACCT_ID, "Follow up", None, "reply")

    assert isinstance(result, UUID)
    assert result == task_id


@pytest.mark.asyncio
async def test_create_task_with_owner_and_due_date():
    """create_task accepts optional owner_member_id and due_date."""
    task_id = uuid4()
    owner_id = uuid4()
    due = datetime.date(2026, 12, 1)
    pool = _make_simple_pool(fetchone_sequence=[(str(task_id),)])

    result = await create_task(
        pool, ORG_ID, WS_ID, ACCT_ID, "Send proposal", due, "manual", owner_id
    )
    assert isinstance(result, UUID)
    assert result == task_id


# ---------------------------------------------------------------------------
# suggest_next_step tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggest_next_step_calls_gateway_and_persists():
    """suggest_next_step calls gateway.write and persists via app.upsert_next_step."""
    pool, execute_calls = _make_tracking_pool()

    # Also mock the account stage fetch (cursor.fetchone on first connection)
    # We need to intercept the cursor path separately.
    stage_pool = MagicMock()
    stage_call_count = [0]

    def _make_stage_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        if idx == 0:
            # Stage fetch
            cur.fetchone = AsyncMock(return_value=("Engaged",))
        else:
            cur.fetchone = AsyncMock(return_value=None)

        async def _exec(sql, params=None):
            execute_calls.append((sql, params))

        conn.execute = AsyncMock(side_effect=_exec)
        return conn

    async def _stage_aenter(_):
        idx = stage_call_count[0]
        stage_call_count[0] += 1
        return _make_stage_conn(idx)

    stage_cm = MagicMock()
    stage_cm.__aenter__ = _stage_aenter
    stage_cm.__aexit__ = AsyncMock(return_value=False)
    stage_pool.connection.return_value = stage_cm

    gateway = MagicMock()
    gateway.write = AsyncMock(return_value="Schedule a demo call next week.")

    suggestion = await suggest_next_step(stage_pool, gateway, ACCT_ID, WS_ID)

    assert suggestion == "Schedule a demo call next week."
    gateway.write.assert_awaited_once()
    # Verify gateway was called with the right prompt path
    args = gateway.write.call_args
    assert args[0][0] == "prompts/next_step/v1.md"

    # Verify app.upsert_next_step was called
    upsert_sqls = [sql for sql, _ in execute_calls if "app.upsert_next_step" in sql]
    assert upsert_sqls, (
        f"Expected app.upsert_next_step call but execute_calls were: {execute_calls}"
    )


@pytest.mark.asyncio
async def test_suggest_next_step_strips_whitespace():
    """suggest_next_step strips leading/trailing whitespace from AI output."""
    pool = MagicMock()
    call_count = [0]
    execute_calls: list = []

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=("Contacted",) if idx == 0 else None)

        async def _exec(sql, params=None):
            execute_calls.append((sql, params))

        conn.execute = AsyncMock(side_effect=_exec)
        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    gateway = MagicMock()
    gateway.write = AsyncMock(return_value="  Send a follow-up email.  \n")

    suggestion = await suggest_next_step(pool, gateway, ACCT_ID, WS_ID)

    assert suggestion == "Send a follow-up email."
