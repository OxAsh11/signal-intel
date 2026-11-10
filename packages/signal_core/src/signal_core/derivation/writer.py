"""Derivation writer — async helpers calling DB RPCs for verdicts and memory chunks.

All writes go through app.* SECURITY DEFINER RPCs (S8 write discipline).
No direct INSERT/UPDATE/DELETE.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool


async def upsert_verdict(
    pool: AsyncConnectionPool,
    brief_version_id: UUID,
    entity_id: UUID,
    value: str,
    claim_ids: list[UUID],
    quotes: list[dict],
    inputs: dict,
) -> UUID:
    """Call app.upsert_verdict RPC and return the verdict UUID."""
    sql = (
        "SELECT app.upsert_verdict("
        "%s, %s, %s::public.verdict_value, %s::uuid[], %s::jsonb, %s::jsonb"
        ")"
    )
    params = (
        str(brief_version_id),
        str(entity_id),
        value,
        [str(c) for c in claim_ids],
        json.dumps(quotes),
        json.dumps(inputs),
    )
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return UUID(str(row[0]))


async def upsert_memory_chunk(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID | None,
    entity_id: UUID | None,
    brief_id: UUID | None,
    doc_type: str,
    doc_id: UUID,
    chunk_index: int,
    content: str,
    embedding: list[float],
    embedding_model: str,
    scope: str,
    occurred_at: Any | None,
) -> UUID:
    """Call app.upsert_memory_chunk RPC and return the chunk UUID."""
    sql = (
        "SELECT app.upsert_memory_chunk("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s::text::extensions.vector, %s, "
        "%s::public.data_scope, %s"
        ")"
    )
    params = (
        str(org_id),
        str(workspace_id) if workspace_id is not None else None,
        str(entity_id) if entity_id is not None else None,
        str(brief_id) if brief_id is not None else None,
        doc_type,
        str(doc_id),
        chunk_index,
        content,
        json.dumps(embedding),
        embedding_model,
        scope,
        occurred_at,
    )
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
    return UUID(str(row[0]))
