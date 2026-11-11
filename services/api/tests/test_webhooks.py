"""Tests for inbound webhook ingestion — POST /v1/webhooks/{integration_id}.

Uses the same fake-pool pattern as test_api_auth.py so no real database is needed.
The FakeCursor pre-loads rows in call order: each fetchone() pops from the front.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from api.main import build_app

# ---------------------------------------------------------------------------
# Fake pool / connection / cursor infrastructure (identical to test_api_auth)
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

    async def __aenter__(self) -> "FakeCursor":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


@dataclass
class FakeConnection:
    _cursor: FakeCursor

    def cursor(self) -> FakeCursor:
        return self._cursor

    async def __aenter__(self) -> "FakeConnection":
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
# Shared test constants
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
WS_ID = uuid4()
INTEGRATION_ID = uuid4()
ACCOUNT_ID = uuid4()
SECRET = "test-webhook-secret-key"


def _make_integration_row() -> tuple[Any, ...]:
    """Row returned by: SELECT org_id, workspace_id, credentials_vault_key FROM integration."""
    return (ORG_ID, WS_ID, SECRET)


def _make_account_row() -> tuple[Any, ...]:
    """Row returned by: SELECT id FROM account WHERE external_id = %s."""
    return (ACCOUNT_ID,)


def _compute_sig(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _make_payload(
    external_id: str = "evt-001",
    event_type: str = "email_replied",
    account_external_id: str = "acct-001",
) -> bytes:
    return json.dumps(
        {
            "external_id": external_id,
            "type": event_type,
            "account_external_id": account_external_id,
        }
    ).encode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def valid_client():
    """Client whose pool is wired for a fresh (non-duplicate) valid inbound webhook.

    fetchone call order:
      1. integration row
      2. dedup check → None (not seen before)
      3. account lookup → account row
    """
    app = build_app()
    cursor = FakeCursor(
        _rows=[
            _make_integration_row(),  # integration lookup
            None,                      # dedup check: not duplicate
            _make_account_row(),       # account lookup
        ]
    )
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def bad_sig_client():
    """Client whose pool returns a valid integration row (sig check runs after the lookup)."""
    app = build_app()
    cursor = FakeCursor(_rows=[_make_integration_row()])
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def duplicate_client():
    """Client whose pool simulates a previously seen event.

    fetchone call order:
      1. integration row
      2. dedup check → existing row (duplicate)
    """
    app = build_app()
    cursor = FakeCursor(
        _rows=[
            _make_integration_row(),  # integration lookup
            (uuid4(),),               # dedup: existing activity row found
        ]
    )
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def unknown_type_client():
    """Same flow as valid_client but payload uses an unknown event type."""
    app = build_app()
    cursor = FakeCursor(
        _rows=[
            _make_integration_row(),
            None,
            _make_account_row(),
        ]
    )
    app.state.pool = FakePool(cursor)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_valid_inbound_webhook_stores_activity(valid_client: AsyncClient) -> None:
    """Happy path: valid HMAC, fresh event, activity created, returns 200."""
    payload = _make_payload()
    sig = _compute_sig(payload)

    resp = await valid_client.post(
        f"/v1/webhooks/{INTEGRATION_ID}",
        content=payload,
        headers={"X-Signal-Signature": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_invalid_signature_returns_401(bad_sig_client: AsyncClient) -> None:
    """Wrong HMAC → 401 Unauthorized."""
    payload = _make_payload()

    resp = await bad_sig_client.post(
        f"/v1/webhooks/{INTEGRATION_ID}",
        content=payload,
        headers={"X-Signal-Signature": "sha256=deadbeef"},
    )
    assert resp.status_code == 401


async def test_duplicate_event_idempotent(duplicate_client: AsyncClient) -> None:
    """Duplicate external_id → 200 without calling create_activity again."""
    payload = _make_payload()
    sig = _compute_sig(payload)

    resp = await duplicate_client.post(
        f"/v1/webhooks/{INTEGRATION_ID}",
        content=payload,
        headers={"X-Signal-Signature": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify create_activity was NOT called (only 2 executes: integration lookup + dedup)
    executed_sqls = [sql for sql, _ in duplicate_client._transport.app.state.pool._cursor._executed]
    assert not any("create_activity" in sql for sql in executed_sqls)


async def test_unknown_event_type_uses_note_added(unknown_type_client: AsyncClient) -> None:
    """Unknown event type is mapped to 'note_added' and stored successfully."""
    payload = _make_payload(event_type="custom_crm_event")
    sig = _compute_sig(payload)

    resp = await unknown_type_client.post(
        f"/v1/webhooks/{INTEGRATION_ID}",
        content=payload,
        headers={"X-Signal-Signature": f"sha256={sig}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify create_activity was called with kind='note_added'
    cursor = unknown_type_client._transport.app.state.pool._cursor
    create_activity_calls = [
        params for sql, params in cursor._executed if "create_activity" in sql
    ]
    assert len(create_activity_calls) == 1
    # params tuple: (org_id, workspace_id, account_id, kind, occurred_at, integration_id, external_id)
    _org, _ws, _acct, kind, _occ, _int, _ext = create_activity_calls[0]
    assert kind == "note_added"
