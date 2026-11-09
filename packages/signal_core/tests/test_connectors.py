"""Tests for connector implementations.

Tasks 4 and 5 will ADD further tests to this file.
"""

import io
import csv
import pytest
from signal_core.connectors.csv_upload import CsvConnector
from signal_core.connectors.base import FetchGuard


@pytest.mark.asyncio
async def test_csv_produces_one_payload_per_data_row(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"], ["Beta Inc", "beta.io"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, cursor = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert len(payloads) == 2
    assert payloads[0].url.startswith("csv:")
    assert payloads[0].content_hash != payloads[1].content_hash


@pytest.mark.asyncio
async def test_csv_header_only_returns_zero_payloads(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("name,domain\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    assert payloads == []


@pytest.mark.asyncio
async def test_csv_deduplicates_identical_rows(tmp_path):
    path = tmp_path / "dupe.csv"
    path.write_text("name,domain\nAcme,acme.com\nAcme,acme.com\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    hashes = [p.content_hash for p in payloads]
    assert len(set(hashes)) == len(hashes)  # all unique hashes (deduped at upload layer)


@pytest.mark.asyncio
async def test_csv_cursor_is_always_empty_dict(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    _, cursor = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert cursor == {}


@pytest.mark.asyncio
async def test_csv_source_type():
    conn = CsvConnector()
    assert conn.source_type == "csv_upload"


# ============================================================
# Task 4 tests: website, news, and job posts connectors
# ============================================================


@pytest.mark.asyncio
async def test_website_strips_html():
    """WebsiteConnector returns plain text with HTML tags removed."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock, patch

    from signal_core.connectors.website import WebsiteConnector

    html_content = b"<html><body><h1>Hello</h1><p>World</p></body></html>"

    resp = MagicMock()
    resp.status = 200
    resp.read = AsyncMock(return_value=html_content)

    @asynccontextmanager
    async def _resp_ctx(*_args, **_kwargs):
        yield resp

    session = MagicMock()
    session.get = _resp_ctx

    @asynccontextmanager
    async def _session_ctx(*_args, **_kwargs):
        yield session

    guard = FetchGuard()
    # Skip SSRF + robots checks so we control only the page-fetch mock.
    guard.check = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with patch("aiohttp.ClientSession", new=_session_ctx):
        conn = WebsiteConnector()
        payloads, next_cursor = await conn.fetch(
            config={"urls": ["http://example.com/page"]},
            queries=[],
            cursor={},
            guard=guard,
            limit=10,
        )

    assert len(payloads) == 1
    assert b"<h1>" not in payloads[0].raw
    assert b"Hello" in payloads[0].raw
    assert payloads[0].content_hash != ""
    assert conn.source_type == "website"


@pytest.mark.asyncio
async def test_news_fake_returns_two_payloads():
    """NewsConnector fake mode returns exactly 2 deterministic payloads."""
    from signal_core.connectors.news import NewsConnector

    guard = FetchGuard()
    conn = NewsConnector()
    payloads, _ = await conn.fetch(
        config={"fake": True},
        queries=["CAD software hiring"],
        cursor={},
        guard=guard,
        limit=10,
    )
    assert len(payloads) == 2
    # Deterministic: same call → same content hashes
    payloads2, _ = await conn.fetch(
        config={"fake": True},
        queries=["other query"],
        cursor={},
        guard=guard,
        limit=10,
    )
    assert payloads[0].content_hash == payloads2[0].content_hash
    assert conn.source_type == "news"


@pytest.mark.asyncio
async def test_jobs_fake_returns_two_payloads():
    """JobPostConnector fake mode returns exactly 2 deterministic payloads."""
    from signal_core.connectors.job_posts import JobPostConnector

    guard = FetchGuard()
    conn = JobPostConnector()
    payloads, _ = await conn.fetch(
        config={"fake": True},
        queries=[],
        cursor={},
        guard=guard,
        limit=10,
    )
    assert len(payloads) == 2
    assert conn.source_type == "job_post"
    # Deterministic: same call → same content hashes
    payloads2, _ = await conn.fetch(
        config={"fake": True},
        queries=[],
        cursor={},
        guard=guard,
        limit=10,
    )
    assert payloads[0].content_hash == payloads2[0].content_hash
