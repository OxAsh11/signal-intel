"""M2 targeted search: one narrow web search for an entity whose verdict is Unverified (M5).

Throttled in SQL (app.claim_targeted_search): once per Brief and entity per 30 days, at most 100
per Brief per day, and never for an entity known only from earlier targeted searches.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.connectors.base import FetchGuard
from signal_core.connectors.brave import TARGETED_KIND, BraveSearchConnector, targeted_search
from signal_core.logging import get_logger
from signal_core.metering import BudgetPaused, CallContext
from signal_core.modules.m02_radar import create_source_records

log = get_logger(__name__)

JOB_TYPE = "source.targeted_search"


async def run_targeted_search(
    pool: AsyncConnectionPool,
    make_connector: Callable[[CallContext], BraveSearchConnector],
    *,
    entity_id: str,
    brief_id: str,
    workspace_id: str,
    approval_id: str | None,
    configured: bool,
    guard: FetchGuard | None = None,
) -> dict[str, Any]:
    """Run the search and store its results. A failed search gives its throttle slot back."""
    if not configured:
        # Checked before claiming, so the slot is kept for when the key is added.
        return {"searched": False, "reason": "not_configured"}
    async with pool.connection() as conn:
        # A resumed run (its budget approval granted) already holds the slot.
        if not approval_id:
            cur = await conn.execute(
                "select app.claim_targeted_search(%s, %s)", (brief_id, entity_id)
            )
            claimed = await cur.fetchone()
            if not (claimed and claimed[0]):
                return {"searched": False, "reason": "throttled"}
        cur = await conn.execute(
            """select e.display_name, b.org_id,
                      coalesce((select array_agg(distinct t)
                                  from public.radar r, unnest(r.lexicon) t
                                 where r.brief_id = b.id and r.status <> 'paused'), '{}')
                 from public.entity e, public.brief b
                where e.id = %s and b.id = %s""",
            (entity_id, brief_id),
        )
        row = await cur.fetchone()
    if row is None or not row[0]:
        return {"searched": False, "reason": "unknown_entity"}
    name, org_id, lexicon = str(row[0]), UUID(str(row[1])), sorted(row[2] or [])

    job_args: dict[str, Any] = {
        "entity_id": entity_id,
        "brief_id": brief_id,
        "workspace_id": workspace_id,
    }
    if approval_id:
        job_args["approval_id"] = approval_id
    ctx = CallContext(org_id, UUID(workspace_id), UUID(brief_id), None, JOB_TYPE, job_args)
    try:
        payloads = await targeted_search(make_connector(ctx), name, lexicon, guard or FetchGuard())
    except BudgetPaused:
        raise  # the slot stays held: budget.resume re-runs this search with approval_id
    except Exception:
        async with pool.connection() as conn:
            await conn.execute("select app.release_targeted_search(%s, %s)", (brief_id, entity_id))
        log.warning("targeted_search.failed", entity_id=entity_id, brief_id=brief_id)
        raise
    created = await create_source_records(
        pool, org_id, UUID(workspace_id), f"targeted:{brief_id}", TARGETED_KIND, payloads
    )
    return {"searched": True, "created": created}
