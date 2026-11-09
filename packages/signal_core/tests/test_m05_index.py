"""Tests for m05_index — S9 source-record chunker/embedder."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from signal_core.metering import CallContext
from signal_core.modules.m05_index import chunk_text, index_source_record


# ---------------------------------------------------------------------------
# chunk_text unit tests
# ---------------------------------------------------------------------------


def test_chunk_text_single_chunk():
    """Short text fits in one chunk."""
    text = "hello world"
    chunks = chunk_text(text)
    assert chunks == ["hello world"]


def test_chunk_text_empty_string():
    """Empty string returns empty list."""
    assert chunk_text("") == []


def test_chunk_text_whitespace_only():
    """Whitespace-only string returns empty list."""
    assert chunk_text("   ") == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(fetchone_return):
    """Build a psycopg3-style async pool mock."""
    conn = MagicMock()
    cur = AsyncMock()
    cur.fetchone = AsyncMock(return_value=fetchone_return)
    cur.fetchall = AsyncMock(return_value=[])

    @asynccontextmanager
    async def mock_cursor(*_args, **_kwargs):
        yield cur

    conn.cursor = MagicMock(side_effect=mock_cursor)

    @asynccontextmanager
    async def mock_connection():
        yield conn

    pool = MagicMock()
    pool.connection = MagicMock(side_effect=lambda: mock_connection())
    return pool


# ---------------------------------------------------------------------------
# index_source_record tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_index_source_record_no_row_returns_zero():
    """Returns 0 and does not call embed when source_record is not found."""
    pool = _make_pool(fetchone_return=None)

    gateway = MagicMock()
    gateway.embed = AsyncMock()
    gateway.model_for = MagicMock(return_value="test-embed-model")

    ctx = CallContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        brief_id=None,
        job_id=None,
        job_type="index.chunk_embed",
        job_args={},
    )

    result = await index_source_record(pool, gateway, uuid4(), uuid4(), ctx)

    assert result == 0
    gateway.embed.assert_not_called()


@pytest.mark.asyncio
async def test_index_source_record_chunks_and_embeds(monkeypatch):
    """Chunks content, calls embed, and upserts each chunk."""
    org_id = uuid4()
    ws_id = uuid4()
    ent_id = uuid4()
    source_id = uuid4()

    # Build row: (org_id, workspace_id, entity_id, brief_id, content, scope, created_at)
    row = (org_id, ws_id, ent_id, None, "Hello world " * 200, "org", datetime.now())

    # Count chunks to match embed return value
    from signal_core.modules.m05_index import chunk_text

    num_chunks = len(chunk_text(row[4]))
    assert num_chunks >= 1

    pool = _make_pool(fetchone_return=row)

    gateway = MagicMock()
    gateway.model_for = MagicMock(return_value="test-embed-model")
    gateway.embed = AsyncMock(return_value=[[0.1] * 10] * num_chunks)

    upsert_mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m05_index.upsert_memory_chunk", upsert_mock)

    ctx = CallContext(
        org_id=org_id,
        workspace_id=ws_id,
        brief_id=None,
        job_id=None,
        job_type="index.chunk_embed",
        job_args={},
    )

    result = await index_source_record(pool, gateway, source_id, ws_id, ctx)

    assert result > 0
    upsert_mock.assert_called()
    assert upsert_mock.call_count == num_chunks
