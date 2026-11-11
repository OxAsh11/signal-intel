"""MCP server — read-only Signal Intel tools for LLM clients.

Mounts at /mcp as an SSE sub-app alongside the FastAPI REST service.
All queries run under service_role with SET LOCAL app.channel = 'mcp'
inside an explicit transaction so the channel never leaks to recycled
pool connections.
Auth: Bearer token validated against public.api_token before each request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("signal-intel")

# Module-level pool reference — set by main.py lifespan after pool creation.
_pool = None

# The workspace the connection's token is scoped to. Set by BearerAuthMiddleware for the
# lifetime of the MCP connection; every tool refuses any other workspace.
_token_workspace: ContextVar[str | None] = ContextVar("mcp_token_workspace", default=None)


class WorkspaceNotPermitted(PermissionError):
    """The tool was asked for a workspace the token is not scoped to."""


def require_workspace(workspace_id: str) -> str:
    """Tools take workspace_id from the model, so it is checked against the token (M13: tools
    respect token scopes). Returns the permitted workspace id."""
    permitted = _token_workspace.get()
    if permitted is None or str(workspace_id).lower() != permitted.lower():
        raise WorkspaceNotPermitted("This token is not scoped to that workspace")
    return permitted


def get_pool() -> Any:
    """Return the shared pool; raises if not yet initialised."""
    if _pool is None:
        raise RuntimeError("MCP server pool not initialised")
    return _pool


# ---------------------------------------------------------------------------
# ASGI auth middleware — validates Bearer token before any /mcp request
# ---------------------------------------------------------------------------

_401_body = b'{"detail":"Missing or invalid Bearer token"}'
_503_body = b'{"detail":"Service unavailable"}'


async def _send_http_response(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BearerAuthMiddleware:
    """ASGI middleware: validates api_token Bearer before forwarding to FastMCP."""

    def __init__(self, app: Any, get_pool_fn: Callable) -> None:
        self._app = app
        self._get_pool = get_pool_fn

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = {k.lower(): v for k, v in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode()
            if not auth.startswith("Bearer "):
                await _send_http_response(send, 401, _401_body)
                return
            token_raw = auth[7:]
            try:
                pool = self._get_pool()
            except RuntimeError:
                await _send_http_response(send, 503, _503_body)
                return
            token_hash = hashlib.sha256(token_raw.encode()).hexdigest()
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, workspace_id FROM public.api_token"
                    " WHERE token_hash = %s AND revoked_at IS NULL"
                    " AND (expires_at IS NULL OR expires_at > now())",
                    (token_hash,),
                )
                row = await cur.fetchone()
                if row is not None:
                    await cur.execute("SELECT app.touch_api_token(%s)", (str(row[0]),))
            if row is None:
                await _send_http_response(send, 401, _401_body)
                return
            # Tasks started while serving this request inherit the binding.
            _token_workspace.set(str(row[1]))
        await self._app(scope, receive, send)


# ---------------------------------------------------------------------------
# MCP tools — each uses SET LOCAL inside a transaction so the channel
# setting is transaction-scoped and never leaks back to the pool.
# ---------------------------------------------------------------------------


@mcp.tool()
async def get_account_card(account_id: str, workspace_id: str) -> dict:
    """Return the account card, verdict, score band, and display name for one account."""
    workspace_id = require_workspace(workspace_id)
    pool = get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL app.channel = 'mcp'")
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT ac.content, e.display_name, v.value, s.band
                    FROM public.account_card ac
                    JOIN public.account a ON a.id = ac.account_id
                    JOIN public.entity e ON e.id = a.entity_id
                    LEFT JOIN public.verdict v ON v.entity_id = a.entity_id
                    LEFT JOIN public.score s ON s.account_id = a.id
                    WHERE a.id = %s AND a.workspace_id = %s
                    ORDER BY v.computed_at DESC, s.computed_at DESC
                    LIMIT 1
                    """,
                (account_id, workspace_id),
            )
            row = await cur.fetchone()
    if row is None:
        return {}
    content, display_name, verdict_value, score_band = row
    return {
        "account_id": account_id,
        "display_name": display_name,
        "verdict": verdict_value,
        "score_band": score_band,
        "content": content,
    }


@mcp.tool()
async def search_account(query: str, workspace_id: str, limit: int = 10) -> list[dict]:
    """Full-text search accounts by entity display_name or alias value."""
    workspace_id = require_workspace(workspace_id)
    pool = get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL app.channel = 'mcp'")
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT a.id, e.display_name, a.stage
                    FROM public.account a
                    JOIN public.entity e ON e.id = a.entity_id
                    WHERE a.workspace_id = %s
                      AND (
                          e.display_name ILIKE %s
                          OR EXISTS (
                              SELECT 1 FROM public.entity_alias ea
                              WHERE ea.entity_id = a.entity_id
                                AND ea.value_normalised ILIKE %s
                          )
                      )
                    ORDER BY e.display_name
                    LIMIT %s
                    """,
                (workspace_id, f"%{query}%", f"%{query}%", limit),
            )
            rows = await cur.fetchall()
    return [{"account_id": str(row[0]), "display_name": row[1], "stage": row[2]} for row in rows]


@mcp.tool()
async def get_claims(
    entity_id: str, workspace_id: str, predicate_key: str | None = None
) -> list[dict]:
    """Return active claims for an entity in a workspace, optionally filtered by predicate key."""
    workspace_id = require_workspace(workspace_id)
    pool = get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL app.channel = 'mcp'")
        async with conn.cursor() as cur:
            workspace_check = (
                "AND EXISTS ("
                "SELECT 1 FROM public.account a "
                "WHERE a.entity_id = c.entity_id AND a.workspace_id = %s"
                ")"
            )
            if predicate_key is not None:
                await cur.execute(
                    f"""
                        SELECT c.id, p.key, c.value, c.quote, c.source_record_id
                        FROM public.claim c
                        JOIN public.predicate p ON p.id = c.predicate_id
                        WHERE c.entity_id = %s AND p.key = %s AND c.status = 'active'
                          {workspace_check}
                        ORDER BY c.created_at DESC
                        """,
                    (entity_id, predicate_key, workspace_id),
                )
            else:
                await cur.execute(
                    f"""
                        SELECT c.id, p.key, c.value, c.quote, c.source_record_id
                        FROM public.claim c
                        JOIN public.predicate p ON p.id = c.predicate_id
                        WHERE c.entity_id = %s AND c.status = 'active'
                          {workspace_check}
                        ORDER BY p.key, c.created_at DESC
                        """,
                    (entity_id, workspace_id),
                )
            rows = await cur.fetchall()
    return [
        {
            "claim_id": str(row[0]),
            "predicate_key": row[1],
            "value": row[2],
            "quote": row[3],
            "source_record_id": str(row[4]) if row[4] else None,
        }
        for row in rows
    ]


@mcp.tool()
async def find_accounts(
    workspace_id: str,
    band: str | None = None,
    stage: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List accounts in a workspace, optionally filtered by score band and/or stage."""
    workspace_id = require_workspace(workspace_id)
    pool = get_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SET LOCAL app.channel = 'mcp'")
        async with conn.cursor() as cur:
            where = "a.workspace_id = %s"
            params: list = [workspace_id]
            if band is not None:
                where += " AND latest_s.band = %s"
                params.append(band)
            if stage is not None:
                where += " AND a.stage = %s"
                params.append(stage)
            params.append(limit)
            await cur.execute(
                f"""
                    WITH latest_s AS (
                        SELECT DISTINCT ON (account_id) account_id, band
                        FROM public.score
                        ORDER BY account_id, computed_at DESC
                    )
                    SELECT a.id, e.display_name, a.stage, latest_s.band
                    FROM public.account a
                    JOIN public.entity e ON e.id = a.entity_id
                    LEFT JOIN latest_s ON latest_s.account_id = a.id
                    WHERE {where}
                    ORDER BY e.display_name
                    LIMIT %s
                    """,
                tuple(params),
            )
            rows = await cur.fetchall()
    return [
        {
            "account_id": str(row[0]),
            "display_name": row[1],
            "stage": row[2],
            "band": row[3],
        }
        for row in rows
    ]
