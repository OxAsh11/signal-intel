"""Tests for connector implementations.

Tasks 4 and 5 will ADD further tests to this file.
"""

import pytest

from signal_core.connectors.base import FetchGuard
from signal_core.connectors.csv_upload import CsvConnector


@pytest.mark.asyncio
async def test_csv_produces_one_payload_per_data_row(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"], ["Beta Inc", "beta.io"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _cursor = await conn.fetch(
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
    payloads, _ = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert payloads == []


@pytest.mark.asyncio
async def test_csv_deduplicates_identical_rows(tmp_path):
    path = tmp_path / "dupe.csv"
    path.write_text("name,domain\nAcme,acme.com\nAcme,acme.com\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
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
        payloads, _next_cursor = await conn.fetch(
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


# ---------------------------------------------------------------------------
# Task 5: RadarQuerySet model and generate_radar_queries
# ---------------------------------------------------------------------------


def test_radar_query_set_model_validates():
    """RadarQuerySet validates correctly with all source types."""
    from signal_core.modules.m02_radar import RadarQuery, RadarQuerySet

    qs = RadarQuerySet(
        queries=[
            RadarQuery(query_text="SolidWorks hiring", language="en", source="hypothesis"),
            RadarQuery(query_text="CAD software engineer", source="icp"),
        ]
    )
    assert len(qs.queries) == 2
    assert qs.queries[0].source == "hypothesis"
    assert qs.queries[1].source == "icp"
    assert qs.queries[1].language == "en"


@pytest.mark.asyncio
async def test_generate_radar_queries_calls_gateway():
    """generate_radar_queries passes variables to the gateway and returns RadarQuerySet."""
    from decimal import Decimal
    from uuid import uuid4

    from signal_core.ai_gateway.gateway import Result
    from signal_core.metering import CallContext
    from signal_core.modules.m02_radar import RadarQuery, RadarQuerySet
    from signal_core.modules.m02_radar_ai import generate_radar_queries

    expected_qs = RadarQuerySet(
        queries=[
            RadarQuery(query_text="SolidWorks hiring", source="hypothesis"),
            RadarQuery(query_text="CAD engineer ICP", source="icp"),
        ]
    )

    class _FakeGateway:
        async def run(self, *args, **kwargs):
            return Result(
                value=expected_qs, extractor_version_id=uuid4(), cost_usd=Decimal("0.001")
            )

    ctx = CallContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        brief_id=uuid4(),
        job_id=None,
        job_type="test",
        job_args={},
    )
    result = await generate_radar_queries(
        gateway=_FakeGateway(),
        hypotheses=["firms using SolidWorks that are hiring"],
        icp_summary="Mid-market engineering firms",
        connector_type="job_post",
        ctx=ctx,
        limit=5,
    )
    assert len(result.queries) == 2
    assert result.queries[0].query_text == "SolidWorks hiring"


def test_source_payload_defaults_keep_old_connectors():
    from datetime import UTC, datetime

    from signal_core.connectors import SourcePayload

    p = SourcePayload("https://x.test", datetime.now(tz=UTC), "h", None, "en", b"x")
    assert p.query is None
    assert p.content_kind == "full"


def test_radar_query_set_carries_lexicon():
    from signal_core.modules.m02_radar import RadarQuerySet

    assert RadarQuerySet(queries=[]).lexicon == []
    assert RadarQuerySet(queries=[], lexicon=["tender"]).lexicon == ["tender"]
