"""M2 Radar — Python side: source record persistence and query management."""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.connectors import SourcePayload


class RadarQuery(BaseModel):
    query_text: str
    language: str = "en"
    source: Literal["hypothesis", "icp", "user"] = "hypothesis"


class RadarQuerySet(BaseModel):
    queries: list[RadarQuery]
    # Terms whose presence marks a relevant record (M2 pre-filter); empty lets everything through.
    lexicon: list[str] = []


async def create_source_records(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    connector_id: str,
    source_type: str,
    payloads: list[SourcePayload],
    *,
    radar_id: UUID | None = None,
    query_ids: dict[str, UUID] | None = None,
) -> int:
    """Persist *payloads* by calling ``app.create_source_record`` for each one.

    Returns the count of newly-inserted rows (rows where the RPC returned a
    non-NULL id — i.e. rows that did *not* already exist by content hash).
    """
    new_count = 0
    ids = query_ids or {}
    async with pool.connection() as conn:
        for payload in payloads:
            async with conn.cursor() as cur:
                await cur.execute(
                    "select app.create_source_record("
                    "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(org_id),
                        str(workspace_id),
                        connector_id,
                        source_type,
                        payload.url,
                        payload.fetched_at,
                        payload.content_hash,
                        payload.payload_ref,
                        payload.language,
                        # The fetched text is stored with the record: extraction reads it.
                        payload.raw.decode("utf-8", errors="replace").replace("\x00", ""),
                        str(radar_id) if radar_id else None,
                        str(ids[payload.query]) if payload.query in ids else None,
                        payload.content_kind,
                    ),
                )
                row = await cur.fetchone()
            if row is not None and row[0] is not None:
                new_count += 1
    return new_count


async def upsert_radar_queries(
    pool: AsyncConnectionPool,
    radar_id: UUID,
    query_set: RadarQuerySet,
) -> None:
    """Persist *query_set* by calling ``app.upsert_radar_queries`` (service-role RPC).

    Deletes any non-user-sourced rows for the radar, then re-inserts all
    queries in *query_set*.
    """
    queries_json = json.dumps([q.model_dump() for q in query_set.queries])
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select app.upsert_radar_queries(%s, %s, %s)",
            (str(radar_id), queries_json, query_set.lexicon or None),
        )


async def load_query_ids(pool: AsyncConnectionPool, radar_id: UUID) -> dict[str, UUID]:
    """Map each of the radar's query texts to its row id, for record attribution."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select query_text, id from public.radar_query where radar_id = %s", (str(radar_id),)
        )
        rows = await cur.fetchall()
    return {str(r[0]): UUID(str(r[1])) for r in rows}
