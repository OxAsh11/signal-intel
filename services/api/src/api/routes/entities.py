"""GET /v1/entities — cursor-paginated entity list and detail."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/entities", tags=["entities"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_entities(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    workspace_id: UUID = Query(...),
    entity_type: str | None = Query(default=None),
    updated_since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    conditions = ["workspace_id = %s"]
    params: list[Any] = [str(workspace_id)]

    if entity_type:
        conditions.append("entity_type = %s")
        params.append(entity_type)
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
                f"SELECT id, workspace_id, entity_type, name, domain, updated_at"
                f" FROM public.entity WHERE {where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "workspace_id": str(r[1]),
            "entity_type": r[2],
            "name": r[3],
            "domain": r[4],
            "updated_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}


@router.get("/{entity_id}")
async def get_entity(
    request: Request,
    entity_id: UUID,
    token: Annotated[ApiToken, Depends(get_token)],
) -> dict[str, Any]:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, workspace_id, entity_type, name, domain, updated_at"
                " FROM public.entity WHERE id = %s",
                (str(entity_id),),
            )
            row = await cur.fetchone()

    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Entity not found")

    return {
        "id": str(row[0]),
        "workspace_id": str(row[1]),
        "entity_type": row[2],
        "name": row[3],
        "domain": row[4],
        "updated_at": row[5].isoformat() if row[5] else None,
    }
