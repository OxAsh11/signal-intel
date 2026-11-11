"""M13 Integrations Hub — integration management, webhook delivery with retry/replay.

Flow for create_integration:
  1. Call app.create_integration RPC.
  2. Return integration UUID.

Flow for deliver_webhook:
  1. Load subscription from public.webhook_subscription.
  2. Sign payload with HMAC-SHA256 using subscription.secret.
  3. POST to subscription.url with X-Signal-Signature header.
  4. On each attempt call app.record_webhook_delivery RPC.
  5. Retry with exponential backoff; delays = [10, 30, 90, 270, 810, 1800, 3600, 7200,
     14400, 28800] seconds; max 10 attempts.
  6. After 10 consecutive failures: UPDATE public.webhook_subscription SET enabled=false.
  7. Return 'delivered' | 'failed'.

Flow for replay_webhook_delivery:
  1. Load delivery from public.webhook_delivery (JOIN webhook_subscription for url).
  2. If status == 'delivered': return 'delivered' (idempotent).
  3. Otherwise: re-POST stored payload with stored signature — do NOT re-sign.

Security note: all writes go through app.* SECURITY DEFINER RPCs except the operational
UPDATE to webhook_subscription.enabled after 10 consecutive failures, which is performed
directly via service_role as permitted by the brief.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

import httpx

from signal_core.logging import get_logger

log = get_logger(__name__)

# Retry delays in seconds for deliver_webhook (10 attempts total).
_RETRY_DELAYS: list[int] = [10, 30, 90, 270, 810, 1800, 3600, 7200, 14400, 28800]


# ---------------------------------------------------------------------------
# Context helper
# ---------------------------------------------------------------------------


class IntegrationCtx:
    """Minimal context object carrying org_id and workspace_id.

    Callers may pass any object with `.org_id` and `.workspace_id` UUID attributes.
    """

    def __init__(self, org_id: UUID, workspace_id: UUID) -> None:
        self.org_id = org_id
        self.workspace_id = workspace_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_integration(
    pool: object,
    ctx: Any,
    name: str,
    category: str,
    access_mode: str,
) -> UUID:
    """Create an integration record via app.create_integration RPC.

    Parameters
    ----------
    pool:        async connection pool.
    ctx:         context object with `.org_id` and `.workspace_id` UUID attributes.
    name:        integration name (e.g. 'HubSpot').
    category:    integration category (e.g. 'CRMs').
    access_mode: access mode string (e.g. 'Read').

    Returns
    -------
    UUID of the new integration row.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_integration(%s, %s, %s, %s, %s)",
                (
                    str(ctx.org_id),
                    str(ctx.workspace_id),
                    name,
                    category,
                    access_mode,
                ),
            )
            row = await cur.fetchone()

    integration_id = UUID(str(row[0]))
    log.info(
        "integrations.created",
        integration_id=str(integration_id),
        name=name,
        category=category,
    )
    return integration_id


async def deliver_webhook(
    pool: object,
    subscription_id: UUID,
    event_id: UUID,
    event_type: str,
    payload_dict: dict[str, Any],
) -> str:
    """Deliver a webhook event to a subscription endpoint with retry logic.

    Signs the payload with HMAC-SHA256 using the subscription's secret.
    Retries up to 10 times with exponential backoff.  After 10 consecutive
    failures, disables the subscription via a direct UPDATE (service_role
    operational update, NOT an RPC write per brief).

    Parameters
    ----------
    pool:            async connection pool.
    subscription_id: UUID of the webhook_subscription row.
    event_id:        UUID of the event being delivered.
    event_type:      event type string (e.g. 'score.band_changed').
    payload_dict:    JSON-serialisable payload dict.

    Returns
    -------
    'delivered' | 'failed'
    """
    # 1. Load subscription row.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, url, secret, enabled"
                " FROM public.webhook_subscription WHERE id = %s",
                (str(subscription_id),),
            )
            row = await cur.fetchone()

    if row is None:
        log.warning(
            "webhooks.subscription_not_found",
            subscription_id=str(subscription_id),
        )
        return "failed"

    _sub_id, _org_id, url, secret, enabled = (
        row[0],
        row[1],
        row[2],
        row[3],
        row[4],
    )

    if not enabled:
        log.info(
            "webhooks.subscription_disabled",
            subscription_id=str(subscription_id),
        )
        return "failed"

    # 2. Sign payload — sort_keys ensures canonical byte order that survives JSONB round-trip.
    payload_bytes = json.dumps(payload_dict, sort_keys=True).encode()
    signature = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Signal-Signature": f"sha256={signature}",
    }

    # 3. Attempt delivery with retry and backoff.
    async with httpx.AsyncClient() as client:
        for attempt, delay in enumerate(_RETRY_DELAYS):
            response_status: int | None = None
            response_body: str | None = None
            error_message: str | None = None

            try:
                response = await client.post(url, content=payload_bytes, headers=headers)
                response_status = response.status_code
                response_body = response.text

                if response_status == 200:
                    await _record_delivery(
                        pool,
                        subscription_id,
                        event_id,
                        event_type,
                        payload_dict,
                        signature,
                        response_status=response_status,
                        response_body=response_body,
                    )
                    log.info(
                        "webhooks.delivered",
                        subscription_id=str(subscription_id),
                        event_id=str(event_id),
                        attempt=attempt,
                    )
                    return "delivered"

                error_message = f"HTTP {response_status}"

            except Exception as exc:
                error_message = str(exc)

            # Record the failed attempt.
            await _record_delivery(
                pool,
                subscription_id,
                event_id,
                event_type,
                payload_dict,
                signature,
                response_status=response_status,
                response_body=response_body,
                error_message=error_message,
            )

            # Sleep before next retry (skip sleep after the last attempt).
            if attempt < len(_RETRY_DELAYS) - 1:
                await asyncio.sleep(delay)

    # All 10 attempts exhausted — disable subscription (operational update, not RPC).
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "UPDATE public.webhook_subscription SET enabled=false WHERE id=%s",
            (str(subscription_id),),
        )
    log.warning(
        "webhooks.subscription_disabled_after_failures",
        subscription_id=str(subscription_id),
        event_id=str(event_id),
    )
    return "failed"


async def replay_webhook_delivery(
    pool: object,
    delivery_id: UUID,
) -> str:
    """Replay a stored webhook delivery without re-signing the payload.

    Idempotent: returns 'delivered' immediately when the delivery already
    has status='delivered'.

    Parameters
    ----------
    pool:        async connection pool.
    delivery_id: UUID of the webhook_delivery row to replay.

    Returns
    -------
    'delivered' | 'failed'
    """
    # 1. Load delivery row joined with its subscription for the target URL.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT d.id, d.subscription_id, d.event_id, d.event_type,
                       d.payload, d.signature, d.status, s.url
                FROM public.webhook_delivery d
                JOIN public.webhook_subscription s ON s.id = d.subscription_id
                WHERE d.id = %s
                """,
                (str(delivery_id),),
            )
            row = await cur.fetchone()

    if row is None:
        log.warning(
            "webhooks.replay_delivery_not_found",
            delivery_id=str(delivery_id),
        )
        return "failed"

    _delivery_uuid, subscription_id, event_id, event_type, payload, signature, status, url = (
        row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
    )

    # 2. Idempotency guard.
    if status == "delivered":
        log.info("webhooks.replay_idempotent", delivery_id=str(delivery_id))
        return "delivered"

    # 3. Re-POST stored payload with stored signature; do NOT re-sign.
    #    Use sort_keys=True so byte order matches the original signed bytes (JSONB
    #    round-trips do not preserve key order; canonical serialization fixes this).
    payload_bytes = (
        json.dumps(payload, sort_keys=True).encode() if isinstance(payload, dict) else payload
    )
    headers = {
        "Content-Type": "application/json",
        "X-Signal-Signature": f"sha256={signature}",
    }

    response_status: int | None = None
    response_body: str | None = None
    error_message: str | None = None

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, content=payload_bytes, headers=headers)
        response_status = response.status_code
        response_body = response.text

        if response_status == 200:
            await _record_delivery(
                pool,
                UUID(str(subscription_id)),
                UUID(str(event_id)),
                str(event_type),
                payload if isinstance(payload, dict) else {},
                str(signature),
                response_status=response_status,
                response_body=response_body,
            )
            log.info("webhooks.replay_delivered", delivery_id=str(delivery_id))
            return "delivered"

        error_message = f"HTTP {response_status}"

    except Exception as exc:
        error_message = str(exc)

    await _record_delivery(
        pool,
        UUID(str(subscription_id)),
        UUID(str(event_id)),
        str(event_type),
        payload if isinstance(payload, dict) else {},
        str(signature),
        response_status=response_status,
        response_body=response_body,
        error_message=error_message,
    )
    log.warning(
        "webhooks.replay_failed",
        delivery_id=str(delivery_id),
        status_code=response_status,
    )
    return "failed"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _record_delivery(
    pool: object,
    subscription_id: UUID,
    event_id: UUID,
    event_type: str,
    payload_dict: dict[str, Any],
    signature: str,
    *,
    response_status: int | None = None,
    response_body: str | None = None,
    error_message: str | None = None,
) -> UUID:
    """Call app.record_webhook_delivery RPC and return the delivery UUID."""
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.record_webhook_delivery(%s, %s, %s, %s::jsonb, %s, %s, %s, %s)",
                (
                    str(subscription_id),
                    str(event_id),
                    event_type,
                    json.dumps(payload_dict),
                    signature,
                    response_status,
                    response_body,
                    error_message,
                ),
            )
            row = await cur.fetchone()
    return UUID(str(row[0]))
