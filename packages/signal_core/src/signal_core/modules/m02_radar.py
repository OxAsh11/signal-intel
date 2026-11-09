"""M2 Radar — Python side: source record persistence."""

from __future__ import annotations

from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.connectors import SourcePayload


async def create_source_records(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    connector_id: str,
    payloads: list[SourcePayload],
) -> int:
    """Persist *payloads* by calling ``app.create_source_record`` for each one.

    Returns the count of newly-inserted rows (rows where the RPC returned a
    non-NULL id — i.e. rows that did *not* already exist by content hash).
    """
    new_count = 0
    async with pool.connection() as conn:
        for payload in payloads:
            row = await conn.fetchrow(
                "select app.create_source_record(%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(org_id),
                    str(workspace_id),
                    connector_id,
                    payload.url,
                    payload.fetched_at,
                    payload.content_hash,
                    payload.payload_ref,
                    payload.language,
                ),
            )
            if row is not None and row[0] is not None:
                new_count += 1
    return new_count
