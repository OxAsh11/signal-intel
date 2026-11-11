"""Tests for API token authentication and scope enforcement.

Uses an in-memory fake pool so no real database is needed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.main import build_app

# ---------------------------------------------------------------------------
# Fake pool / connection / cursor infrastructure
# ---------------------------------------------------------------------------


@dataclass
class FakeCursor:
    _rows: list[Any] = field(default_factory=list)
    _executed: list[tuple[str, Any]] = field(default_factory=list)

    async def execute(self, sql: str, params: Any = None) -> None:
        self._executed.append((sql, params))

    async def fetchone(self) -> Any | None:
        if self._rows:
            return self._rows.pop(0)
        return None

    async def fetchall(self) -> list[Any]:
        rows = list(self._rows)
        self._rows.clear()
        return rows

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


@dataclass
class FakeConnection:
    _cursor: FakeCursor

    def cursor(self) -> FakeCursor:
        return self._cursor

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeConnectionContext:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    async def __aenter__(self) -> FakeConnection:
        return FakeConnection(self._cursor)

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakePool:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def connection(self) -> FakeConnectionContext:
        return FakeConnectionContext(self._cursor)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
WS_ID = uuid4()


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _valid_token_row(raw: str = "test-token") -> tuple[Any, ...]:
    """Row matching the SELECT in auth.get_token for a read-scoped token."""
    return (uuid4(), ORG_ID, WS_ID, "Read")


def _write_token_row(raw: str = "write-token") -> tuple[Any, ...]:
    return (uuid4(), ORG_ID, WS_ID, "Write")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def valid_client():
    """Client whose pool returns a valid read-scoped token for 'test-token'."""
    app = build_app()
    cursor = FakeCursor(_rows=[_valid_token_row()])
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def empty_client():
    """Client whose pool returns no token rows (missing / revoked / expired)."""
    app = build_app()
    cursor = FakeCursor(_rows=[])  # no rows → 401
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def read_only_client():
    """Client whose pool returns a read-only token."""
    app = build_app()
    cursor = FakeCursor(_rows=[_valid_token_row()])  # only ["read"] scope
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def write_client():
    """Client whose pool returns a write-scoped token."""
    app = build_app()
    cursor = FakeCursor(_rows=[_write_token_row()])
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_health_no_auth_required(valid_client: AsyncClient) -> None:
    resp = await valid_client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_valid_token_resolves(valid_client: AsyncClient) -> None:
    resp = await valid_client.get(
        "/v1/entities",
        headers={"Authorization": "Bearer test-token"},
        params={"workspace_id": str(WS_ID)},
    )
    # The fake cursor returns no entity rows, so items will be empty — but auth passes (200).
    assert resp.status_code == 200


async def test_missing_token_returns_401(empty_client: AsyncClient) -> None:
    resp = await empty_client.get(
        "/v1/entities",
        params={"workspace_id": str(WS_ID)},
    )
    assert resp.status_code == 401


async def test_revoked_token_returns_401(empty_client: AsyncClient) -> None:
    """Pool returning no rows simulates a revoked or expired token."""
    resp = await empty_client.get(
        "/v1/entities",
        headers={"Authorization": "Bearer revoked-token"},
        params={"workspace_id": str(WS_ID)},
    )
    assert resp.status_code == 401


async def test_read_token_cannot_post_campaign(read_only_client: AsyncClient) -> None:
    brief_id = uuid4()
    resp = await read_only_client.post(
        "/v1/campaigns",
        headers={"Authorization": "Bearer read-token"},
        json={
            "name": "x",
            "motion": "Vertical",
            "brief_id": str(brief_id),
            "workspace_id": str(WS_ID),
        },
    )
    assert resp.status_code == 403


async def test_write_token_can_post_campaign(write_client: AsyncClient) -> None:
    brief_id = uuid4()
    resp = await write_client.post(
        "/v1/campaigns",
        headers={"Authorization": "Bearer write-token"},
        json={
            "name": "x",
            "motion": "Vertical",
            "brief_id": str(brief_id),
            "workspace_id": str(WS_ID),
        },
    )
    # Fake pool returns no rows from the RPC call; we just check auth passed (not 401/403).
    assert resp.status_code not in (401, 403)


async def test_invalid_bearer_scheme_returns_401(empty_client: AsyncClient) -> None:
    resp = await empty_client.get(
        "/v1/entities",
        headers={"Authorization": "Token bad-token"},
        params={"workspace_id": str(WS_ID)},
    )
    assert resp.status_code == 401
