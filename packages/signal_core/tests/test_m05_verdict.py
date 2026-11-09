"""Tests for M5 verdict computation."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from signal_core.modules.m05_verdict import compute_verdict, VerdictOutput
from signal_core.metering import CallContext


@pytest.mark.asyncio
async def test_compute_verdict_no_brief_version_returns_none():
    """When no published brief_version exists, compute_verdict returns None without calling AI."""
    pool = MagicMock()
    conn = MagicMock()
    # pool.connection() used as async context manager
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    # conn.cursor() used as async context manager
    cur = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    # First fetchone: no brief_version row
    cur.fetchone = AsyncMock(return_value=None)

    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "verdict.compute", {})

    result = await compute_verdict(pool, gateway, uuid4(), uuid4(), ctx)

    assert result is None
    gateway.run.assert_not_called()


@pytest.mark.asyncio
async def test_compute_verdict_no_claims_returns_unverified(monkeypatch):
    """When entity has no claims, verdict is Unverified without calling AI."""
    pool = MagicMock()
    conn = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    # First fetchone: brief_version row; second fetchone: entity display_name
    cur.fetchone = AsyncMock(
        side_effect=[(uuid4(), "test hypothesis"), ("ACME Corp",)]
    )
    # fetchall: no claims
    cur.fetchall = AsyncMock(return_value=[])

    gateway = MagicMock()
    upsert = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m05_verdict.upsert_verdict", upsert)

    ctx = CallContext(uuid4(), uuid4(), None, None, "verdict.compute", {})
    result = await compute_verdict(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == "Unverified"
    gateway.run.assert_not_called()
    upsert.assert_called_once()
    # claim_ids must be empty list for Unverified
    assert upsert.call_args[0][4] == []
