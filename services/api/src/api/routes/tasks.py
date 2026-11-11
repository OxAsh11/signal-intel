"""GET /v1/tasks — cursor-paginated task list."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_tasks(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    workspace_id: UUID = Query(...),
    account_id: UUID | None = Query(default=None),
    status: str | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    conditions = ["workspace_id = %s"]
    params: list[Any] = [str(workspace_id)]

    if account_id:
        conditions.append("account_id = %s")
        params.append(str(account_id))
    if status:
        conditions.append("status = %s")
        params.append(status)
    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = " AND ".join(conditions)
    params.append(limit)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, workspace_id, account_id, title, status, due_at, updated_at"
                f" FROM public.task WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "account_id": str(r[2]) if r[2] else None,
            "title": r[3],
            "status": r[4],
            "due_at": r[5].isoformat() if r[5] else None,
            "updated_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}
