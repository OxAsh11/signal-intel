"""MCP server — read-only Signal Intel tools for LLM clients.

Mounts at /mcp as an SSE sub-app alongside the FastAPI REST service.
All queries run under service_role with SET app.channel = 'mcp'.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("signal-intel")

# Module-level pool reference — set by main.py lifespan after pool creation.
_pool = None


async def _get_pool():
    if _pool is None:
        raise RuntimeError("MCP server pool not initialised")
    return _pool


@mcp.tool()
async def get_account_card(account_id: str, workspace_id: str) -> dict:
    """Return the account card, verdict, score band, and display name for one account."""
    pool = await _get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ac.content, e.display_name, v.value, s.band
                FROM public.account_card ac
                JOIN public.account a ON a.id = ac.account_id
                JOIN public.entity e ON e.id = a.entity_id
                LEFT JOIN public.verdict v ON v.entity_id = a.entity_id
                LEFT JOIN public.score s ON s.entity_id = a.entity_id
                WHERE a.id = %s AND a.workspace_id = %s
                ORDER BY v.created_at DESC, s.created_at DESC
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
    pool = await _get_pool()
    async with pool.connection() as conn:
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
    return [
        {"account_id": str(row[0]), "display_name": row[1], "stage": row[2]}
        for row in rows
    ]


@mcp.tool()
async def get_claims(entity_id: str, predicate_key: str | None = None) -> list[dict]:
    """Return active claims for an entity, optionally filtered by predicate key."""
    pool = await _get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            if predicate_key is not None:
                await cur.execute(
                    """
                    SELECT id, predicate_key, object_value, quote, source_record_id
                    FROM public.claim
                    WHERE entity_id = %s AND predicate_key = %s AND retracted_at IS NULL
                    ORDER BY created_at DESC
                    """,
                    (entity_id, predicate_key),
                )
            else:
                await cur.execute(
                    """
                    SELECT id, predicate_key, object_value, quote, source_record_id
                    FROM public.claim
                    WHERE entity_id = %s AND retracted_at IS NULL
                    ORDER BY predicate_key, created_at DESC
                    """,
                    (entity_id,),
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
    pool = await _get_pool()
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            where = "a.workspace_id = %s"
            params: list = [workspace_id]
            if band is not None:
                where += " AND s.band = %s"
                params.append(band)
            if stage is not None:
                where += " AND a.stage = %s"
                params.append(stage)
            params.append(limit)
            await cur.execute(
                f"""
                SELECT a.id, e.display_name, a.stage, s.band
                FROM public.account a
                JOIN public.entity e ON e.id = a.entity_id
                LEFT JOIN public.score s ON s.entity_id = a.entity_id
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
