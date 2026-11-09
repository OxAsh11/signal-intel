"""Tests for FetchGuard, content_hash.

pytest-localserver is not available in this environment, so the HTTP layer is
mocked with unittest.mock instead of a live httpserver fixture.  The
socket.getaddrinfo call is also patched for robots.txt tests so that the
test helper URL resolves to a public IP rather than loopback.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from signal_core.connectors.base import FetchBlocked, FetchGuard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(status: int, body: str):
    """Return an async-context-manager mock that behaves like aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)

    @asynccontextmanager
    async def _resp_ctx(*_args, **_kwargs):
        yield resp

    return _resp_ctx


def _make_mock_session(status: int, body: str):
    """Return an async-context-manager mock for aiohttp.ClientSession."""
    resp_ctx = _make_mock_response(status, body)
    session = MagicMock()
    session.get = resp_ctx

    @asynccontextmanager
    async def _session_ctx(*_args, **_kwargs):
        yield session

    return _session_ctx


# A fake getaddrinfo result that returns a public IP (1.2.3.4).
_PUBLIC_GETADDRINFO = [(2, 1, 6, "", ("1.2.3.4", 0))]

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_private_ip_blocked():
    g = FetchGuard()
    with pytest.raises(FetchBlocked):
        await g.check("http://192.168.1.1/page")


@pytest.mark.asyncio
async def test_loopback_blocked():
    g = FetchGuard()
    with pytest.raises(FetchBlocked):
        await g.check("http://127.0.0.1/page")


@pytest.mark.asyncio
async def test_public_ip_allowed():
    """Public IP with an empty robots.txt must not raise."""
    session_ctx = _make_mock_session(200, "")
    with (
        patch("socket.getaddrinfo", return_value=_PUBLIC_GETADDRINFO),
        patch("aiohttp.ClientSession", new=session_ctx),
    ):
        g = FetchGuard()
        await g.check("http://example.com/page")  # must not raise


@pytest.mark.asyncio
async def test_robots_disallowed():
    """robots.txt that disallows /page must raise FetchDisallowed."""
    from signal_core.connectors.base import FetchDisallowed

    robots_body = "User-agent: *\nDisallow: /page\n"
    session_ctx = _make_mock_session(200, robots_body)
    with (
        patch("socket.getaddrinfo", return_value=_PUBLIC_GETADDRINFO),
        patch("aiohttp.ClientSession", new=session_ctx),
    ):
        g = FetchGuard()
        with pytest.raises(FetchDisallowed):
            await g.check("http://example.com/page")


@pytest.mark.asyncio
async def test_robots_404_treated_as_no_rules():
    """A 404 robots.txt must be treated as "no rules" — must not raise."""
    session_ctx = _make_mock_session(404, "Not Found")
    with (
        patch("socket.getaddrinfo", return_value=_PUBLIC_GETADDRINFO),
        patch("aiohttp.ClientSession", new=session_ctx),
    ):
        g = FetchGuard()
        await g.check("http://example.com/page")  # must not raise


def test_content_hash_stable():
    from signal_core.connectors.base import content_hash

    assert content_hash(b"hello") == content_hash(b"hello")
    assert content_hash(b"hello") != content_hash(b"world")
    # Pin the exact SHA-256 so algorithm regressions are caught.
    assert content_hash(b"hello") == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
