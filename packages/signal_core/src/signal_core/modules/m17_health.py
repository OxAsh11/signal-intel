"""M17 Health — data completeness checks across 4 pipelines.

For each pipeline, counts accounts processed vs total in the last 30 days and
persists via the app.record_data_completeness RPC.

Kill-switch: health.check
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.kill_switch import is_enabled
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pipeline definitions
# ---------------------------------------------------------------------------

PIPELINES: dict[str, dict[str, str]] = {
    "scoring": {
        "expected_sql": "SELECT COUNT(*) FROM public.account WHERE workspace_id = %s",
        "actual_sql": """
            SELECT COUNT(DISTINCT s.account_id) FROM public.score s
            JOIN public.account a ON a.id = s.account_id
            WHERE a.workspace_id = %s
              AND s.computed_at > now() - interval '30 days'
        """,
    },
    "research": {
        "expected_sql": "SELECT COUNT(*) FROM public.account WHERE workspace_id = %s",
        "actual_sql": """
            SELECT COUNT(DISTINCT rd.account_id) FROM public.research_doc rd
            JOIN public.account a ON a.id = rd.account_id
            WHERE a.workspace_id = %s
              AND rd.published_at > now() - interval '30 days'
        """,
    },
    "enrichment": {
        "expected_sql": "SELECT COUNT(*) FROM public.account WHERE workspace_id = %s",
        "actual_sql": """
            SELECT COUNT(DISTINCT cp.account_id) FROM public.contact_point cp
            JOIN public.account a ON a.id = cp.account_id
            WHERE a.workspace_id = %s
              AND cp.created_at > now() - interval '30 days'
        """,
    },
    "stage": {
        "expected_sql": "SELECT COUNT(*) FROM public.account WHERE workspace_id = %s AND stage != 'Untouched'",  # noqa: E501
        "actual_sql": """
            SELECT COUNT(DISTINCT ase.account_id) FROM public.account_stage_event ase
            JOIN public.account a ON a.id = ase.account_id
            WHERE a.workspace_id = %s
              AND ase.occurred_at > now() - interval '30 days'
        """,
    },
}


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------


async def check_completeness(
    pool: AsyncConnectionPool,  # AsyncConnectionPool
    workspace_id: UUID,
    ctx: CallContext,
) -> dict[str, dict[str, Any]]:
    """Check data completeness for all pipelines in a workspace.

    Returns dict mapping pipeline_name → {'expected': int, 'actual': int}.
    """
    # Kill-switch check
    if not await is_enabled(pool, "health.check"):
        log.info("health.check.disabled", workspace_id=str(workspace_id))
        return {}

    # Fetch org_id for this workspace (needed for the RPC call)
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT w.org_id FROM public.workspace w WHERE w.id = %s",
                (str(workspace_id),),
            )
            workspace_row = await cur.fetchone()

    if workspace_row is None:
        log.warning("health.workspace_not_found", workspace_id=str(workspace_id))
        return {}

    org_id: str = str(workspace_row[0])

    now = datetime.now(tz=UTC)
    period_start = now - timedelta(days=30)
    period_end = now

    result: dict[str, dict[str, Any]] = {}

    for pipeline_name, pipeline in PIPELINES.items():
        # Run expected and actual counts
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(pipeline["expected_sql"], (str(workspace_id),))
                expected_row = await cur.fetchone()
                expected = int(expected_row[0]) if expected_row else 0

                await cur.execute(pipeline["actual_sql"], (str(workspace_id),))
                actual_row = await cur.fetchone()
                actual = int(actual_row[0]) if actual_row else 0

        # Persist via RPC (S8: no direct INSERT)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT app.record_data_completeness(%s, %s, %s, %s, %s, %s, %s)",
                    (
                        org_id,
                        str(workspace_id),
                        pipeline_name,
                        period_start,
                        period_end,
                        expected,
                        actual,
                    ),
                )

        result[pipeline_name] = {"expected": expected, "actual": actual}
        log.info(
            "health.pipeline_checked",
            workspace_id=str(workspace_id),
            pipeline=pipeline_name,
            expected=expected,
            actual=actual,
        )

    return result
