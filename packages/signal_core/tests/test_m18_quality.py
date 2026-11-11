"""Tests for M18 compliance — submit_dsr, record_lawful_basis,
add_evaluation_example, is_suppressed."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from signal_core.modules.m18_quality import (
    add_evaluation_example,
    is_suppressed,
    record_lawful_basis,
    submit_dsr,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

org_id = uuid4()
person_id = uuid4()
ws_id = uuid4()
brief_ver_id = uuid4()

_DSR_ID = uuid4()
_BASIS_ID = uuid4()


def _make_pool(fetchone_return=None, fetchall_return=None):
    """Standard mock pool matching the prescribed psycopg3 async pattern."""
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=fetchone_return)
    cur.fetchall = AsyncMock(return_value=fetchall_return or [])
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn, cur


# ---------------------------------------------------------------------------
# submit_dsr
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_dsr_deletion_calls_rpc():
    """submit_dsr with type_='deletion' calls the RPC and returns a UUID."""
    pool, conn, cur = _make_pool(fetchone_return=(str(_DSR_ID),))

    result = await submit_dsr(pool, org_id, person_id, "deletion", "test@example.com")

    assert isinstance(result, UUID)
    assert result == _DSR_ID
    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "app.submit_dsr" in call_args[0]
    assert "deletion" in call_args[1]


@pytest.mark.asyncio
async def test_submit_dsr_access_calls_rpc():
    """submit_dsr with type_='access' calls the RPC and returns a UUID."""
    pool, conn, cur = _make_pool(fetchone_return=(str(_DSR_ID),))

    result = await submit_dsr(pool, org_id, person_id, "access", "requester@example.com")

    assert isinstance(result, UUID)
    assert result == _DSR_ID
    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "app.submit_dsr" in call_args[0]
    assert "access" in call_args[1]


# ---------------------------------------------------------------------------
# record_lawful_basis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_lawful_basis_returns_uuid():
    """record_lawful_basis calls the RPC and returns a UUID."""
    pool, conn, cur = _make_pool(fetchone_return=(str(_BASIS_ID),))

    result = await record_lawful_basis(pool, org_id, "email_outreach", "legitimate_interest")

    assert isinstance(result, UUID)
    assert result == _BASIS_ID
    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "app.record_lawful_basis" in call_args[0]


# ---------------------------------------------------------------------------
# add_evaluation_example
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_evaluation_example_calls_rpc():
    """add_evaluation_example calls the RPC with correct params."""
    pool, conn, cur = _make_pool(fetchone_return=None)

    example = {"input": "Who is the CEO?", "expected": "Jane Doe"}
    await add_evaluation_example(pool, org_id, ws_id, brief_ver_id, example)

    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "app.add_evaluation_example" in call_args[0]
    # The example JSON must appear in the params
    import json
    assert json.dumps(example) in call_args[1]


# ---------------------------------------------------------------------------
# is_suppressed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_suppressed_true():
    """is_suppressed returns True when a matching row exists."""
    pool, conn, cur = _make_pool(fetchone_return=(True,))

    result = await is_suppressed(pool, org_id, person_id)

    assert result is True
    cur.execute.assert_called_once()
    call_args = cur.execute.call_args[0]
    assert "suppression_list" in call_args[0]


@pytest.mark.asyncio
async def test_is_suppressed_false():
    """is_suppressed returns False when no matching row exists."""
    pool, conn, cur = _make_pool(fetchone_return=(False,))

    result = await is_suppressed(pool, org_id, person_id)

    assert result is False
