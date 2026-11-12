"""GET /v1/briefs — cursor-paginated brief list and version history."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/briefs", tags=["briefs"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_briefs(
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

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"SELECT id, workspace_id, name, state, current_version_id, updated_at"
            f" FROM public.brief WHERE {where} ORDER BY id LIMIT %s",
            params,
        )
        rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "name": r[2],
            "state": r[3],
            "current_version_id": str(r[4]) if r[4] else None,
            "updated_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}


@router.get("/{brief_id}/versions")
async def list_brief_versions(
    request: Request,
    brief_id: UUID,
    token: Annotated[ApiToken, Depends(get_token)],
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    conditions = ["brief_id = %s"]
    params: list[Any] = [str(brief_id)]

    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn, conn.cursor() as cur:
        # Verify the brief exists and belongs to this org
        await cur.execute(
            "SELECT id FROM public.brief WHERE id = %s AND org_id = %s",
            (str(brief_id), str(token.org_id)),
        )
        if await cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Brief not found")

        await cur.execute(
            f"SELECT id, brief_id, version, published_at"
            f" FROM public.brief_version WHERE {where} ORDER BY id LIMIT %s",
            params,
        )
        rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "brief_id": str(r[1]),
            "version": r[2],
            "published_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}
