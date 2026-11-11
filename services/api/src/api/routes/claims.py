"""GET /v1/claims — cursor-paginated claim list."""

from __future__ import annotations

import base64
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from api.auth import ApiToken, get_token

router = APIRouter(prefix="/claims", tags=["claims"])


def _decode_cursor(after: str | None) -> str | None:
    if after is None:
        return None
    try:
        return base64.urlsafe_b64decode(after + "==").decode()
    except Exception:
        return None


@router.get("")
async def list_claims(
    request: Request,
    token: Annotated[ApiToken, Depends(get_token)],
    entity_id: UUID | None = Query(default=None),
    predicate_key: str | None = Query(default=None),
    status: str | None = Query(default=None),
    updated_since: str | None = Query(default=None),
    after: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    pool = request.app.state.pool
    cursor_id = _decode_cursor(after)

    conditions: list[str] = []
    params: list[Any] = []

    if entity_id:
        conditions.append("entity_id = %s")
        params.append(str(entity_id))
    if predicate_key:
        conditions.append("predicate_key = %s")
        params.append(predicate_key)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if updated_since:
        conditions.append("updated_at > %s")
        params.append(updated_since)
    if cursor_id:
        conditions.append("id > %s")
        params.append(cursor_id)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, entity_id, predicate_key, value, status, updated_at"
                f" FROM public.claim{where} ORDER BY id LIMIT %s",
                params,
            )
            rows = await cur.fetchall()

    items = [
        {
            "id": str(r[0]),
            "entity_id": str(r[1]) if r[1] else None,
            "predicate_key": r[2],
            "value": r[3],
            "status": r[4],
            "updated_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
    next_cursor = None
    if len(rows) == limit:
        last_id = str(rows[-1][0])
        next_cursor = base64.urlsafe_b64encode(last_id.encode()).rstrip(b"=").decode()

    return {"items": items, "next_cursor": next_cursor}
