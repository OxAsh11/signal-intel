"""GET /v1/campaigns and POST /v1/campaigns — campaign list and creation."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from api.auth import ApiToken, TokenScope, get_token, require_scope

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_campaigns(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    workspace_id: UUID = Query(...),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    # Reject cross-workspace access when the token is scoped to a workspace
    if token.workspace_id is not None and str(workspace_id) != str(token.workspace_id):
        raise HTTPException(status_code=403, detail="Workspace not permitted")

    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    # Tenant isolation: bind to org_id and workspace_id
    conditions = ["org_id = %s", "workspace_id = %s"]
    params: list[Any] = [str(token.org_id), str(workspace_id)]

    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, workspace_id, name, motion, brief_id, status, created_at"
                f" FROM public.campaign WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "name": r[2],
            "motion": r[3],
            "brief_id": str(r[4]) if r[4] else None,
            "status": r[5],
            "created_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}


class CreateCampaignRequest(BaseModel):
    name: str
    motion: str
    brief_id: UUID
    workspace_id: UUID


@router.post("", status_code=201)
async def create_campaign(
    request: Request,
    body: CreateCampaignRequest,
    token: Annotated[ApiToken, Depends(require_scope(TokenScope.write))],
) -> dict[str, Any]:
    # Reject cross-workspace access when the token is scoped to a workspace
    if token.workspace_id is not None and str(body.workspace_id) != str(token.workspace_id):
        raise HTTPException(status_code=403, detail="Workspace not permitted")

    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_campaign(%s, %s, %s, %s, %s::public.motion, NULL)",
                (
                    str(token.org_id),
                    str(body.workspace_id),
                    str(body.brief_id),
                    body.name,
                    body.motion,
                ),
            )
            row = await cur.fetchone()

    campaign_id = row[0] if row else None
    return {"id": str(campaign_id) if campaign_id else None}


@router.get("/{campaign_id}/members")
async def list_campaign_members(
    request: Request,
    campaign_id: UUID,
    token: Annotated[ApiToken, Depends(get_token)],
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    # Tenant isolation: campaign_member has org_id
    conditions = ["campaign_id = %s", "org_id = %s"]
    params: list[Any] = [str(campaign_id), str(token.org_id)]

    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, campaign_id, account_id, person_entity_id, created_at"
                f" FROM public.campaign_member WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "campaign_id": str(r[1]),
            "account_id": str(r[2]) if r[2] else None,
            "person_entity_id": str(r[3]) if r[3] else None,
            "created_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}
