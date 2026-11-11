"""Admin-scope endpoints: integrations and webhook subscriptions (M13).

POST /v1/integrations                        connect an integration; returns its signing secret once
POST /v1/webhook-subscriptions               subscribe a URL to event types; returns its secret once
GET  /v1/webhook-subscriptions/{id}/deliveries   the delivery log
POST /v1/webhook-deliveries/{id}/replay      re-send a stored delivery
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from api.auth import ApiToken, TokenScope, require_scope
from signal_core.contracts.enums import EventType
from signal_core.crypto import SecretsNotConfigured, generate_secret
from signal_core.modules import m13_integrations as m13

router = APIRouter(tags=["admin"])

AdminToken = Annotated[ApiToken, Depends(require_scope(TokenScope.admin))]
_EVENT_TYPES = {e.value for e in EventType}


def _check_workspace(token: ApiToken, workspace_id: UUID) -> None:
    if token.workspace_id is None or str(workspace_id) != str(token.workspace_id):
        raise HTTPException(status_code=403, detail="Workspace not permitted")


class CreateIntegrationRequest(BaseModel):
    workspace_id: UUID
    name: str
    category: str


@router.post("/integrations", status_code=201)
async def create_integration(
    request: Request, body: CreateIntegrationRequest, token: AdminToken
) -> dict[str, Any]:
    """New connections start in Read mode. Inbound events are posted to `webhook_path`, signed
    with `signing_secret` (shown once)."""
    _check_workspace(token, body.workspace_id)
    secret = generate_secret()
    try:
        integration_id = await m13.create_integration(
            request.app.state.pool,
            m13.IntegrationCtx(UUID(str(token.org_id)), body.workspace_id),
            body.name,
            body.category,
            "Read",
            signing_secret=secret,
        )
    except SecretsNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Secret storage is not set up yet") from exc
    return {
        "id": str(integration_id),
        "access_mode": "Read",
        "signing_secret": secret,
        "webhook_path": f"/v1/webhooks/{integration_id}",
    }


class CreateSubscriptionRequest(BaseModel):
    workspace_id: UUID
    url: str
    event_types: list[str]


@router.post("/webhook-subscriptions", status_code=201)
async def create_webhook_subscription(
    request: Request, body: CreateSubscriptionRequest, token: AdminToken
) -> dict[str, Any]:
    _check_workspace(token, body.workspace_id)
    unknown = sorted(set(body.event_types) - _EVENT_TYPES)
    if not body.event_types or unknown:
        raise HTTPException(status_code=422, detail=f"Unknown event types: {unknown}")
    try:
        subscription_id, secret = await m13.create_webhook_subscription(
            request.app.state.pool,
            UUID(str(token.org_id)),
            body.workspace_id,
            sorted(set(body.event_types)),
            body.url,
        )
    except m13.WebhookUrlBlocked as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SecretsNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Secret storage is not set up yet") from exc
    return {"id": str(subscription_id), "secret": secret}


@router.get("/webhook-subscriptions/{subscription_id}/deliveries")
async def list_deliveries(
    request: Request, subscription_id: UUID, token: AdminToken
) -> dict[str, Any]:
    async with request.app.state.pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT d.id, d.event_id, d.status, d.attempt_count,"
            "       d.last_attempted_at, d.delivered_at"
            "  FROM public.webhook_delivery d"
            "  JOIN public.webhook_subscription s ON s.id = d.subscription_id"
            " WHERE d.subscription_id = %s AND s.org_id = %s AND s.workspace_id = %s"
            " ORDER BY d.created_at DESC LIMIT 200",
            (str(subscription_id), str(token.org_id), str(token.workspace_id)),
        )
        rows = await cur.fetchall()
    return {
        "items": [
            {
                "id": str(r[0]),
                "event_id": str(r[1]) if r[1] else None,
                "status": r[2],
                "attempt_count": r[3],
                "last_attempted_at": r[4].isoformat() if r[4] else None,
                "delivered_at": r[5].isoformat() if r[5] else None,
            }
            for r in rows
        ]
    }


@router.post("/webhook-deliveries/{delivery_id}/replay")
async def replay_delivery(request: Request, delivery_id: UUID, token: AdminToken) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT 1 FROM public.webhook_delivery d"
            "  JOIN public.webhook_subscription s ON s.id = d.subscription_id"
            " WHERE d.id = %s AND s.org_id = %s AND s.workspace_id = %s",
            (str(delivery_id), str(token.org_id), str(token.workspace_id)),
        )
        if await cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Delivery not found")
    return {"status": await m13.replay_webhook_delivery(pool, delivery_id)}
