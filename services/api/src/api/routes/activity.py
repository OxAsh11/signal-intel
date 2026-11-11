"""GET /v1/activity — cursor-paginated activity list."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/activity", tags=["activity"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_activity(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    workspace_id: UUID = Query(...),
    account_id: UUID | None = Query(default=None),
    kind: str | None = Query(default=None),
    since: str | None = Query(default=None),
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

    if account_id:
        conditions.append("account_id = %s")
        params.append(str(account_id))
    if kind:
        conditions.append("kind = %s")
        params.append(kind)
    if since:
        conditions.append("occurred_at > %s")
        params.append(since)
    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, workspace_id, account_id, kind, occurred_at"
                f" FROM public.activity WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "account_id": str(r[2]) if r[2] else None,
            "kind": r[3],
            "occurred_at": r[4].isoformat() if r[4] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}
