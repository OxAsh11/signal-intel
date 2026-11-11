"""Tests for M13 Integrations Hub — integration CRUD, webhook delivery with retry/replay."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

from signal_core.modules.m13_integrations import (
    IntegrationCtx,
    create_integration,
    deliver_webhook,
    replay_webhook_delivery,
)


# ---------------------------------------------------------------------------
# Pool helpers
# ---------------------------------------------------------------------------


def _make_simple_pool(rows_by_index: dict[int, object]) -> MagicMock:
    """Build a mock pool where each connection (by call order) returns a fixed fetchone row.

    ``rows_by_index[i]`` is what ``cur.fetchone()`` returns for the i-th
    ``pool.connection()`` call that uses a cursor.  Missing keys default to
    returning a fresh delivery-UUID row.
    """
    pool = MagicMock()
    call_count = [0]

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock()

        row = rows_by_index.get(idx, (str(uuid4()),))
        cur.fetchone = AsyncMock(return_value=row)
        cur.fetchall = AsyncMock(return_value=[])

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id() -> UUID:
    return uuid4()


@pytest.fixture
def workspace_id() -> UUID:
    return uuid4()


@pytest.fixture
def ctx(org_id: UUID, workspace_id: UUID) -> IntegrationCtx:
    return IntegrationCtx(org_id=org_id, workspace_id=workspace_id)


@pytest.fixture
def sub_id() -> UUID:
    return uuid4()


@pytest.fixture
def event_id() -> UUID:
    return uuid4()


@pytest.fixture
def delivery_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# create_integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_integration_returns_uuid(ctx: IntegrationCtx) -> None:
    """create_integration calls the RPC and returns a UUID."""
    new_id = uuid4()
    pool = _make_simple_pool({0: (str(new_id),)})

    int_id = await create_integration(pool, ctx, "HubSpot", "CRMs", "Read")

    assert isinstance(int_id, UUID)
    assert int_id == new_id


@pytest.mark.asyncio
async def test_create_integration_passes_name_category(ctx: IntegrationCtx) -> None:
    """create_integration passes name and category through to the RPC."""
    new_id = uuid4()
    pool = _make_simple_pool({0: (str(new_id),)})

    conn_calls: list[tuple] = []

    orig_connection = pool.connection

    # Capture the execute call to verify parameters.
    execute_calls: list[tuple] = []

    async def _aenter(_):
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock(
            side_effect=lambda sql, params=None: execute_calls.append((sql, params))
        )
        conn.execute = AsyncMock()
        cur.fetchone = AsyncMock(return_value=(str(new_id),))
        return conn

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    await create_integration(pool, ctx, "HubSpot", "CRMs", "Read")

    assert execute_calls, "No execute calls made"
    _sql, params = execute_calls[0]
    assert params is not None
    assert "HubSpot" in params
    assert "CRMs" in params
    assert "Read" in params


# ---------------------------------------------------------------------------
# deliver_webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deliver_webhook_calls_url(
    sub_id: UUID,
    event_id: UUID,
) -> None:
    """deliver_webhook POSTs to the subscription URL and returns 'delivered' on 200."""
    target_url = "https://example.com/hook"
    secret = "mysecret"
    sub_row = (str(sub_id), str(uuid4()), target_url, secret, True)
    delivery_row = (str(uuid4()),)

    # Connection 0: load subscription; connection 1: record_webhook_delivery.
    pool = _make_simple_pool({0: sub_row, 1: delivery_row})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "ok"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client):
        status = await deliver_webhook(pool, sub_id, event_id, "score.band_changed", {})

    assert status == "delivered"
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args.args[0] == target_url or call_args.kwargs.get("url") == target_url or call_args[0][0] == target_url


@pytest.mark.asyncio
async def test_deliver_webhook_includes_signature_header(
    sub_id: UUID,
    event_id: UUID,
) -> None:
    """deliver_webhook includes X-Signal-Signature: sha256=<hex> in request headers."""
    target_url = "https://example.com/hook"
    secret = "mysecret"
    sub_row = (str(sub_id), str(uuid4()), target_url, secret, True)
    delivery_row = (str(uuid4()),)

    pool = _make_simple_pool({0: sub_row, 1: delivery_row})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "ok"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client):
        await deliver_webhook(pool, sub_id, event_id, "score.band_changed", {"foo": "bar"})

    _args, kwargs = mock_client.post.call_args
    headers = kwargs.get("headers", {})
    assert "X-Signal-Signature" in headers
    assert headers["X-Signal-Signature"].startswith("sha256=")


@pytest.mark.asyncio
async def test_deliver_webhook_subscription_not_found(
    sub_id: UUID,
    event_id: UUID,
) -> None:
    """deliver_webhook returns 'failed' when subscription row is not found."""
    pool = _make_simple_pool({0: None})

    status = await deliver_webhook(pool, sub_id, event_id, "score.band_changed", {})

    assert status == "failed"


@pytest.mark.asyncio
async def test_deliver_webhook_disabled_subscription(
    sub_id: UUID,
    event_id: UUID,
) -> None:
    """deliver_webhook returns 'failed' immediately when subscription.enabled is False."""
    target_url = "https://example.com/hook"
    secret = "mysecret"
    sub_row = (str(sub_id), str(uuid4()), target_url, secret, False)  # enabled=False

    pool = _make_simple_pool({0: sub_row})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client):
        status = await deliver_webhook(pool, sub_id, event_id, "score.band_changed", {})

    assert status == "failed"
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_webhook_disables_after_10_failures(
    sub_id: UUID,
    event_id: UUID,
) -> None:
    """deliver_webhook disables the subscription after 10 consecutive failures."""
    target_url = "https://example.com/hook"
    secret = "mysecret"
    sub_row = (str(sub_id), str(uuid4()), target_url, secret, True)
    delivery_row = (str(uuid4()),)

    # Many connections needed: 1 load + 10 record_delivery + 1 disable UPDATE.
    rows_by_index: dict[int, object] = {0: sub_row}
    for i in range(1, 12):
        rows_by_index[i] = delivery_row

    pool = _make_simple_pool(rows_by_index)

    # Capture execute calls to verify the disable UPDATE.
    execute_calls: list[tuple] = []
    orig_connection = pool.connection
    call_count = [0]

    def _make_conn(idx: int) -> MagicMock:
        conn = MagicMock()
        cur = MagicMock()

        conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
        conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
        cur.execute = AsyncMock()
        conn.execute = AsyncMock(
            side_effect=lambda sql, params=None: execute_calls.append((sql, params))
        )

        row = rows_by_index.get(idx, delivery_row)
        cur.fetchone = AsyncMock(return_value=row)
        return conn

    async def _aenter(_):
        idx = call_count[0]
        call_count[0] += 1
        return _make_conn(idx)

    cm = MagicMock()
    cm.__aenter__ = _aenter
    cm.__aexit__ = AsyncMock(return_value=False)
    pool.connection.return_value = cm

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "error"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client),
        patch("signal_core.modules.m13_integrations.asyncio.sleep", new_callable=AsyncMock),
    ):
        status = await deliver_webhook(pool, sub_id, event_id, "score.band_changed", {})

    assert status == "failed"
    # Verify disable UPDATE was called.
    disable_sqls = [
        sql for sql, _ in execute_calls if "SET enabled=false" in sql
    ]
    assert disable_sqls, (
        f"Expected UPDATE ... SET enabled=false, got execute_calls={execute_calls}"
    )


# ---------------------------------------------------------------------------
# replay_webhook_delivery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_replay_uses_stored_payload(
    delivery_id: UUID,
) -> None:
    """replay_webhook_delivery re-POSTs the stored payload and returns 'delivered' on 200."""
    target_url = "https://example.com/hook"
    stored_payload = {"key": "value"}
    stored_signature = "abc123"
    delivery_row = (
        str(delivery_id),
        str(uuid4()),
        stored_payload,
        stored_signature,
        "failed",  # status not 'delivered'
        target_url,
    )

    pool = _make_simple_pool({0: delivery_row})

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "ok"

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client):
        status = await replay_webhook_delivery(pool, delivery_id)

    assert status == "delivered"
    mock_client.post.assert_called_once()

    # Verify the body matches the stored payload bytes exactly.
    _args, kwargs = mock_client.post.call_args
    sent_body = kwargs.get("content", b"")
    assert sent_body == json.dumps(stored_payload).encode()

    # Verify the stored signature was reused (not re-signed).
    headers = kwargs.get("headers", {})
    assert f"sha256={stored_signature}" == headers.get("X-Signal-Signature")


@pytest.mark.asyncio
async def test_replay_idempotent_for_delivered(
    delivery_id: UUID,
) -> None:
    """replay_webhook_delivery returns 'delivered' immediately without HTTP call
    when the delivery already has status='delivered'."""
    target_url = "https://example.com/hook"
    delivery_row = (
        str(delivery_id),
        str(uuid4()),
        {"k": "v"},
        "sig",
        "delivered",  # already delivered
        target_url,
    )

    pool = _make_simple_pool({0: delivery_row})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("signal_core.modules.m13_integrations.httpx.AsyncClient", return_value=mock_client):
        status = await replay_webhook_delivery(pool, delivery_id)

    assert status == "delivered"
    # No HTTP call should have been made.
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_replay_delivery_not_found(
    delivery_id: UUID,
) -> None:
    """replay_webhook_delivery returns 'failed' when the delivery row is not found."""
    pool = _make_simple_pool({0: None})

    status = await replay_webhook_delivery(pool, delivery_id)

    assert status == "failed"
