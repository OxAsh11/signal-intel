"""API token authentication and scope enforcement."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer_scheme = HTTPBearer(auto_error=False)


class TokenScope(str, Enum):
    read = "read"
    write = "write"


@dataclass(frozen=True)
class ApiToken:
    id: UUID
    org_id: UUID
    workspace_id: UUID | None
    scopes: list[str]


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def get_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> ApiToken:
    """FastAPI dependency: validates Bearer token against public.api_token."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token_hash = _hash_token(credentials.credentials)
    pool = request.app.state.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, workspace_id, scopes FROM public.api_token"
                " WHERE token_hash = %s AND revoked_at IS NULL"
                " AND (expires_at IS NULL OR expires_at > now())",
                (token_hash,),
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=401, detail="Invalid or expired token")

            token = ApiToken(
                id=row[0],
                org_id=row[1],
                workspace_id=row[2],
                scopes=row[3] if row[3] is not None else [],
            )

            # Update last_used_at via SECURITY DEFINER RPC (S8 write discipline).
            await cur.execute("SELECT app.touch_api_token(%s)", (str(token.id),))

    return token


def require_scope(scope: TokenScope):
    """FastAPI dependency factory: raises 403 if the token lacks the required scope."""

    async def _check(token: Annotated[ApiToken, Depends(get_token)]) -> ApiToken:
        if scope == TokenScope.write and "write" not in token.scopes:
            raise HTTPException(status_code=403, detail="Write scope required")
        return token

    return _check
