"""Unit tests for signal_core.derivation.writer — RPC call assertions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from signal_core.derivation.writer import upsert_memory_chunk, upsert_verdict


@pytest.mark.asyncio
async def test_upsert_verdict_calls_rpc():
    conn = AsyncMock()
    cur = AsyncMock()
    conn.execute = AsyncMock(return_value=cur)
    verdict_id = uuid4()
    cur.fetchone = AsyncMock(return_value=(verdict_id,))
    # psycopg3: pool.connection() is a sync call returning an async context manager
    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn_cm)
    bv_id, ent_id = uuid4(), uuid4()
    result = await upsert_verdict(pool, bv_id, ent_id, "Confirmed", [uuid4()], [], {})
    assert result == verdict_id
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "app.upsert_verdict" in sql


@pytest.mark.asyncio
async def test_upsert_memory_chunk_calls_rpc():
    conn = AsyncMock()
    cur = AsyncMock()
    conn.execute = AsyncMock(return_value=cur)
    chunk_id = uuid4()
    cur.fetchone = AsyncMock(return_value=(chunk_id,))
    # psycopg3: pool.connection() is a sync call returning an async context manager
    conn_cm = AsyncMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection = MagicMock(return_value=conn_cm)
    result = await upsert_memory_chunk(
        pool,
        uuid4(),
        uuid4(),
        None,
        None,
        "source_record",
        uuid4(),
        0,
        "hello",
        [0.1] * 1024,
        "voyage-3",
        "tenant",
        None,
    )
    assert result == chunk_id
    sql = conn.execute.call_args[0][0]
    assert "app.upsert_memory_chunk" in sql
