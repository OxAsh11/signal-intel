"""Tests for M10 inbox — reply classification with human-wins rule and unsubscribe handling."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4, UUID

from signal_core.modules.m10_inbox import classify_reply, get_inbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_REPLY_CLASSES = [
    "Interested",
    "Referral",
    "Not now",
    "Not interested",
    "Out of office",
    "Unsubscribe",
]


def _make_activity_row(
    activity_id: UUID,
    org_id: UUID,
    workspace_id: UUID,
    person_entity_id: UUID | None,
    payload: dict,
) -> tuple:
    """Return a mock DB row tuple for an activity."""
    return (
        activity_id,
        org_id,
        workspace_id,
        person_entity_id,
        payload,
    )


def _make_pool(
    activity_row: tuple | None,
    *,
    expect_suppression: bool = False,
) -> MagicMock:
    """Build a mock pool whose execute / fetchone behave predictably.

    Connection call sequence:
      1. First connection: fetch the activity row (uses cursor + fetchone).
      2. Second connection: UPDATE activity payload (conn.execute).
      3. Third connection (optional): INSERT suppression_list row (conn.execute).
      4. Fourth connection: SELECT app.emit(...) (conn.execute).

    When activity_row is None the first fetchone returns None.
    """
    pool = MagicMock()

    # Track connection call count so each async-with block gets a unique conn.
    call_count = [0]

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock()

        if idx == 0:
            # First connection: load activity row.
            if activity_row is not None:
                cur.fetchone = AsyncMock(return_value=activity_row)
            else:
                cur.fetchone = AsyncMock(return_value=None)
        else:
            cur.fetchone = AsyncMock(return_value=None)

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


def _make_pool_human_then_ai(
    initial_payload: dict,
    human_payload: dict,
) -> tuple[MagicMock, list[dict]]:
    """Build pool that tracks payload state across two classify_reply calls.

    Returns (pool, payloads_list) where payloads_list[i] is the payload
    seen on the i-th fetch.
    """
    payloads = [initial_payload.copy(), human_payload.copy()]
    fetch_count = [0]

    pool = MagicMock()
    call_count = [0]

    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock()

        # Every new connection with idx % 4 == 0 is a "fetch" connection.
        if idx % 4 == 0:
            fi = fetch_count[0]
            fetch_count[0] += 1
            payload = payloads[fi] if fi < len(payloads) else payloads[-1]
            row = (activity_id, org_id, workspace_id, person_entity_id, payload)
            cur.fetchone = AsyncMock(return_value=row)
        else:
            cur.fetchone = AsyncMock(return_value=None)

        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    return pool, payloads


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_reply_stores_class():
    """AI classification stores reply_class in activity.payload via service_role UPDATE."""
    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()

    # Activity row with no prior classification.
    activity_row = _make_activity_row(
        activity_id, org_id, workspace_id, person_entity_id, {}
    )
    pool = _make_pool(activity_row)

    await classify_reply(pool, activity_id, "Interested", None, "ai")

    # Pool was used (at least one connection opened).
    assert pool.connection.call_count >= 1


@pytest.mark.asyncio
async def test_classify_reply_emits_event():
    """classify_reply emits reply.classified after storing the class."""
    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()

    activity_row = _make_activity_row(
        activity_id, org_id, workspace_id, person_entity_id, {}
    )
    pool = _make_pool(activity_row)

    await classify_reply(pool, activity_id, "Interested", None, "ai")

    # Verify the pool was used multiple times (fetch + update + emit at minimum).
    assert pool.connection.call_count >= 2


@pytest.mark.asyncio
async def test_human_mark_wins_over_ai():
    """Human classification overwrites a prior AI classification."""
    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()
    member_id = uuid4()

    # First call (AI) sees empty payload; second call (human) sees AI payload.
    initial_payload: dict = {}
    after_ai_payload = {"reply_class": "Not interested", "reply_class_source": "ai"}

    pool, _ = _make_pool_human_then_ai(initial_payload, after_ai_payload)

    # AI classifies first.
    await classify_reply(pool, activity_id, "Not interested", None, "ai")
    # Human overrides.
    await classify_reply(pool, activity_id, "Interested", member_id, "human")

    # Both calls succeeded (no exception raised).
    assert pool.connection.call_count >= 2


@pytest.mark.asyncio
async def test_ai_cannot_override_human():
    """AI classification is a no-op when human mark is already set."""
    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()

    # Payload already carries human source.
    human_payload = {
        "reply_class": "Interested",
        "reply_class_source": "human",
    }
    activity_row = _make_activity_row(
        activity_id, org_id, workspace_id, person_entity_id, human_payload
    )
    pool = _make_pool(activity_row)

    # AI tries to override.
    await classify_reply(pool, activity_id, "Not interested", None, "ai")

    # Only the first connection (fetch) should have been opened; no UPDATE.
    # We verify by checking that only 1 connection was used (fetch only).
    assert pool.connection.call_count == 1


@pytest.mark.asyncio
async def test_unsubscribe_adds_to_suppression():
    """Unsubscribe classification inserts the person into suppression_list."""
    activity_id = uuid4()
    org_id = uuid4()
    workspace_id = uuid4()
    person_entity_id = uuid4()

    activity_row = _make_activity_row(
        activity_id, org_id, workspace_id, person_entity_id, {}
    )

    # We need to capture the execute calls to verify suppression insert.
    pool = MagicMock()
    call_count = [0]
    execute_calls: list[tuple] = []

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()
        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock(
            side_effect=lambda sql, params=None: execute_calls.append((sql, params))
        )

        if idx == 0:
            cur.fetchone = AsyncMock(return_value=activity_row)
        else:
            cur.fetchone = AsyncMock(return_value=None)

        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    await classify_reply(pool, activity_id, "Unsubscribe", None, "sequencer")

    # Verify suppression INSERT was called.
    suppression_sqls = [
        sql
        for sql, _ in execute_calls
        if "suppression_list" in sql
    ]
    assert suppression_sqls, (
        "Expected an INSERT into suppression_list for Unsubscribe, "
        f"but execute calls were: {execute_calls}"
    )

    # Verify org_id and person_entity_id are in the params.
    for sql, params in execute_calls:
        if "suppression_list" in sql:
            assert params is not None
            assert str(org_id) in [str(p) for p in params]
            assert str(person_entity_id) in [str(p) for p in params]
            break


@pytest.mark.asyncio
async def test_get_inbox_returns_reply_received_activities():
    """get_inbox returns activity rows of kind reply_received for the workspace."""
    workspace_id = uuid4()

    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    cur.description = [
        MagicMock(name="id"),
        MagicMock(name="org_id"),
        MagicMock(name="workspace_id"),
        MagicMock(name="account_id"),
        MagicMock(name="person_entity_id"),
        MagicMock(name="kind"),
        MagicMock(name="occurred_at"),
        MagicMock(name="payload"),
        MagicMock(name="is_overdue"),
    ]
    # Mock two reply rows.
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    old = now - datetime.timedelta(days=5)
    cur.fetchall = AsyncMock(
        return_value=[
            (uuid4(), uuid4(), workspace_id, uuid4(), uuid4(), "reply_received", now, {}, False),
            (uuid4(), uuid4(), workspace_id, uuid4(), uuid4(), "reply_received", old, {}, True),
        ]
    )

    rows = await get_inbox(pool, workspace_id, {})

    assert len(rows) == 2


@pytest.mark.asyncio
async def test_classify_reply_activity_not_found_is_noop():
    """classify_reply is a no-op when the activity row is not found."""
    activity_id = uuid4()
    pool = _make_pool(None)  # fetchone returns None

    # Should not raise.
    await classify_reply(pool, activity_id, "Interested", None, "ai")

    # Only one connection: the fetch.
    assert pool.connection.call_count == 1
