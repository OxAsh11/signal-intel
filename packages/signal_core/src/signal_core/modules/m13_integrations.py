"""M13 Integrations Hub — integrations, webhook subscriptions, signed delivery with retry and replay.

A delivery is one HTTP attempt. The worker re-queues a failed attempt after the next delay in
RETRY_DELAYS, so no job sleeps between attempts; after the last attempt the subscription is
disabled. The body is {"id", "type", "data"}, serialised canonically and signed with HMAC-SHA256
using the subscription's secret; the same bytes and signature are sent on every retry and replay.

Secrets are stored encrypted (signal_core.crypto). All writes go through app.* RPCs (S8).
"""  # noqa: E501

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID

import httpx
from psycopg_pool import AsyncConnectionPool

from signal_core.connectors.base import FetchBlocked, FetchGuard
from signal_core.crypto import decrypt_secret, encrypt_secret, generate_secret
from signal_core.logging import get_logger

log = get_logger(__name__)

# Seconds to wait after each failed attempt; the failure after the last delay is final
# (eleven attempts over about 16 hours).
RETRY_DELAYS: list[int] = [10, 30, 90, 270, 810, 1800, 3600, 7200, 14400, 28800]
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
_TIMEOUT_SECONDS = 10.0


class WebhookUrlBlocked(ValueError):
    """The URL is not https or resolves to a private address."""


@dataclass(frozen=True)
class DeliveryOutcome:
    status: Literal["delivered", "retry", "failed"]
    retry_in: int | None = None


class IntegrationCtx:
    """Minimal context carrying org_id and workspace_id."""

    def __init__(self, org_id: UUID, workspace_id: UUID) -> None:
        self.org_id = org_id
        self.workspace_id = workspace_id


def check_webhook_url(url: str) -> None:
    """Refuse URLs that could reach internal services (SSRF): https only, public addresses only."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebhookUrlBlocked("webhook URLs must be https")
    try:
        FetchGuard._check_private_ip(parsed.hostname)
    except FetchBlocked as exc:
        raise WebhookUrlBlocked(str(exc)) from exc


def canonical_body(event_id: UUID, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(event_id), "type": event_type, "data": data}


def _serialise(body: dict[str, Any]) -> bytes:
    # Sorted keys and fixed separators: the bytes survive a JSONB round trip, so a replay
    # re-sends exactly what was signed.
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()


def sign(secret: str, payload_bytes: bytes) -> str:
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


async def create_integration(
    pool: AsyncConnectionPool,
    ctx: Any,
    name: str,
    category: str,
    access_mode: str = "Read",
    *,
    signing_secret: str | None = None,
) -> UUID:
    """Create an integration (default access mode Read). `signing_secret`, when given, is the
    shared secret its inbound webhooks are signed with; it is stored encrypted."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.create_integration(%s, %s, %s, %s, %s::public.access_mode, %s)",
            (
                str(ctx.org_id),
                str(ctx.workspace_id),
                name,
                category,
                access_mode,
                encrypt_secret(signing_secret) if signing_secret else None,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    integration_id = UUID(str(row[0]))
    log.info("integrations.created", integration_id=str(integration_id), category=category)
    return integration_id


async def create_webhook_subscription(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    event_types: list[str],
    url: str,
) -> tuple[UUID, str]:
    """Create a subscription. Returns (id, secret); the secret is shown once and stored encrypted."""  # noqa: E501
    check_webhook_url(url)
    secret = generate_secret()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.create_webhook_subscription(%s, %s, %s::text[], %s, %s)",
            (str(org_id), str(workspace_id), event_types, url, encrypt_secret(secret)),
        )
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0])), secret


async def subscriptions_for_event(
    pool: AsyncConnectionPool, workspace_id: UUID, event_type: str
) -> list[UUID]:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM public.webhook_subscription"
            " WHERE workspace_id = %s AND enabled AND %s = ANY(event_types)",
            (str(workspace_id), event_type),
        )
        rows = await cur.fetchall()
    return [UUID(str(r[0])) for r in rows]


async def _post(url: str, payload_bytes: bytes, signature: str) -> tuple[int | None, str | None]:
    """One attempt. Returns (status_code, error)."""
    try:
        check_webhook_url(url)
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client:
            response = await client.post(
                url,
                content=payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-Signal-Signature": f"sha256={signature}",
                },
            )
        return response.status_code, None
    except Exception as exc:
        return None, str(exc)


async def deliver_webhook(
    pool: AsyncConnectionPool,
    subscription_id: UUID,
    event_id: UUID,
    event_type: str,
    payload_dict: dict[str, Any],
    attempt: int = 1,
) -> DeliveryOutcome:
    """Make delivery attempt number `attempt` and record it."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT url, secret_vault_key, enabled FROM public.webhook_subscription WHERE id = %s",
            (str(subscription_id),),
        )
        row = await cur.fetchone()

    if row is None or not row[2]:
        log.info("webhooks.subscription_unavailable", subscription_id=str(subscription_id))
        return DeliveryOutcome("failed")
    url, stored_secret = str(row[0]), str(row[1])

    body = canonical_body(event_id, event_type, payload_dict)
    payload_bytes = _serialise(body)
    signature = sign(decrypt_secret(stored_secret), payload_bytes)

    status_code, error = await _post(url, payload_bytes, signature)
    delivered = status_code is not None and 200 <= status_code < 300
    final_failure = not delivered and attempt >= MAX_ATTEMPTS
    status = "delivered" if delivered else "failed" if final_failure else "pending"
    await _record_delivery(pool, subscription_id, event_id, body, signature, status, attempt)

    if delivered:
        log.info("webhooks.delivered", subscription_id=str(subscription_id), attempt=attempt)
        return DeliveryOutcome("delivered")

    log.warning(
        "webhooks.attempt_failed",
        subscription_id=str(subscription_id),
        event_id=str(event_id),
        attempt=attempt,
        status_code=status_code,
        error=error,
    )
    if final_failure:
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.disable_webhook_subscription(%s)", (str(subscription_id),)
            )
        return DeliveryOutcome("failed")
    return DeliveryOutcome("retry", RETRY_DELAYS[attempt - 1])


async def replay_webhook_delivery(pool: AsyncConnectionPool, delivery_id: UUID) -> str:
    """Re-send a stored delivery with its stored signature. Returns 'delivered' or 'failed'."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT d.subscription_id, d.event_id, d.payload, d.signature, d.attempt_count, s.url
              FROM public.webhook_delivery d
              JOIN public.webhook_subscription s ON s.id = d.subscription_id
             WHERE d.id = %s
            """,
            (str(delivery_id),),
        )
        row = await cur.fetchone()

    if row is None:
        log.warning("webhooks.replay_delivery_not_found", delivery_id=str(delivery_id))
        return "failed"

    subscription_id, event_id, payload, signature, attempts, url = row
    body: dict[str, Any] = json.loads(payload) if isinstance(payload, str) else payload
    status_code, error = await _post(str(url), _serialise(body), str(signature))
    delivered = status_code is not None and 200 <= status_code < 300
    await _record_delivery(
        pool,
        UUID(str(subscription_id)),
        UUID(str(event_id)),
        body,
        str(signature),
        "delivered" if delivered else "failed",
        int(attempts) + 1,
    )
    log.info(
        "webhooks.replayed",
        delivery_id=str(delivery_id),
        delivered=delivered,
        status_code=status_code,
        error=error,
    )
    return "delivered" if delivered else "failed"


async def _record_delivery(
    pool: AsyncConnectionPool,
    subscription_id: UUID,
    event_id: UUID,
    body: dict[str, Any],
    signature: str,
    status: str,
    attempt: int,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "SELECT app.record_webhook_delivery(%s, %s, %s::jsonb, %s, %s, %s,"
            " CASE WHEN %s THEN now() END)",
            (
                str(subscription_id),
                str(event_id),
                json.dumps(body, default=str),
                signature,
                status,
                attempt,
                status == "delivered",
            ),
        )
