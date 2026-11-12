"""GET /v1/accounts — cursor-paginated account list and detail."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_accounts(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    workspace_id: UUID = Query(...),
    stage: str | None = Query(default=None),
    updated_since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    # Reject cross-workspace access when the token is scoped to a workspace
    if token.workspace_id is not None and str(workspace_id) != str(token.workspace_id):
        raise HTTPException(status_code=403, detail="Workspace not permitted")

    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    # Tenant isolation: bind to both org_id and workspace_id
    conditions = ["org_id = %s", "workspace_id = %s"]
    params: list[Any] = [str(token.org_id), str(workspace_id)]

    if stage:
        conditions.append("stage = %s")
        params.append(stage)
    if updated_since:
        conditions.append("updated_at > %s")
        params.append(updated_since)
    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"SELECT id, workspace_id, entity_id, stage, updated_at"
            f" FROM public.account WHERE {where} ORDER BY id LIMIT %s",
            params,
        )
        rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "entity_id": str(r[2]) if r[2] else None,
            "stage": r[3],
            "updated_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}


@router.get("/{account_id}")
async def get_account(
    request: Request,
    account_id: UUID,
    token: Annotated[ApiToken, Depends(get_token)],
) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, workspace_id, entity_id, stage, updated_at"
            " FROM public.account WHERE id = %s AND org_id = %s",
            (str(account_id), str(token.org_id)),
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "entity_id": str(row[2]) if row[2] else None,
        "stage": row[3],
        "updated_at": row[4].isoformat() if row[4] else None,
    }
