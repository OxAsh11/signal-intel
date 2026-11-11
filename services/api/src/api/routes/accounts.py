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
    brief_id: UUID | None = Query(default=None),
    band: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    updated_since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    conditions = ["workspace_id = %s"]
    params: list[Any] = [str(workspace_id)]

    if brief_id:
        conditions.append("brief_id = %s")
        params.append(str(brief_id))
    if band:
        conditions.append("band = %s")
        params.append(band)
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

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, workspace_id, brief_id, entity_id, band, stage, updated_at"
                f" FROM public.account WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "brief_id": str(r[2]) if r[2] else None,
            "entity_id": str(r[3]) if r[3] else None,
            "band": r[4],
            "stage": r[5],
            "updated_at": r[6].isoformat() if r[6] else None,
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
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, workspace_id, brief_id, entity_id, band, stage, updated_at"
                " FROM public.account WHERE id = %s",
                (str(account_id),),
            )
            row = await cur.fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Account not found")

    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "brief_id": str(row[2]) if row[2] else None,
        "entity_id": str(row[3]) if row[3] else None,
        "band": row[4],
        "stage": row[5],
        "updated_at": row[6].isoformat() if row[6] else None,
    }
