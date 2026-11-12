"""Standalone kill-switch helper for modules that need a simple is_enabled check.

Modules that use a pool directly (rather than the job wrapper) import this
function instead of instantiating DbKillSwitch themselves.
"""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool


async def is_enabled(pool: AsyncConnectionPool, feature: str) -> bool:
    """Return True if the given feature/job_type is enabled in job_type_config.

    Unknown features default to False (disabled).
    """
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT enabled FROM public.job_type_config WHERE job_type = %s",
            (feature,),
        )
        row = await cur.fetchone()
    return bool(row[0]) if row else False
