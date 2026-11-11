"""M16 Reporting — metric computation and funnel view.

Wraps the ``app.compute_metric`` PostgreSQL RPC and provides a stage-funnel
aggregation over ``public.account``.

Functions:
  compute_metric(pool, workspace_id, brief_id, metric_key, filters={}) -> dict
  get_funnel(pool, workspace_id, brief_id) -> list[dict]
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

from signal_core.logging import get_logger

log = get_logger(__name__)

# Canonical stage order for the funnel view — matches public.stage enum
STAGE_ORDER: list[str] = [
    "Untouched",
    "Queued",
    "Contacted",
    "Engaged",
    "Qualified",
    "Meeting",
    "Handed off",
    "Dormant",
    "Disqualified",
]


async def compute_metric(
    pool: object,
    workspace_id: UUID,
    brief_id: UUID,
    metric_key: str,
    filters: dict = {},
) -> dict:
    """Call ``app.compute_metric`` RPC and return a normalised result dict.

    Returns::

        {
            "value": <int | float | None>,
            "metric_key": str,
            "computed_at": str,       # ISO-8601 UTC
            "definition_version": int,
        }

    Raises ``ValueError`` if the RPC returns NULL (unknown / unpublished metric).
    """
    filters_json = json.dumps(filters)
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.compute_metric(%s, %s, %s, %s)",
                (str(workspace_id), str(brief_id), metric_key, filters_json),
            )
            row = await cur.fetchone()

    raw: object = row[0] if row else None

    if raw is None:
        raise ValueError(f"unknown metric: {metric_key}")

    # app.compute_metric returns a jsonb scalar (e.g. a number produced by
    # to_jsonb(count(*))).  psycopg3 decodes jsonb as a Python object directly.
    # Normalise to a plain Python value.
    value = raw

    # Fetch the current published version for this key so we can embed it.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT version
                  FROM public.metric_definition
                 WHERE key = %s
                   AND published_at IS NOT NULL
                 ORDER BY version DESC
                 LIMIT 1
                """,
                (metric_key,),
            )
            ver_row = await cur.fetchone()

    definition_version: int = int(ver_row[0]) if ver_row else 1

    computed_at = datetime.now(tz=timezone.utc).isoformat()

    log.info(
        "reporting.metric_computed",
        workspace_id=str(workspace_id),
        brief_id=str(brief_id),
        metric_key=metric_key,
        value=value,
    )

    return {
        "value": value,
        "metric_key": metric_key,
        "computed_at": computed_at,
        "definition_version": definition_version,
    }


async def get_funnel(
    pool: object,
    workspace_id: UUID,
    brief_id: UUID,
) -> list[dict]:
    """Return stage counts, conversion rates, and median days in stage.

    Queries ``public.account`` grouped by ``stage`` for the given workspace.
    The ``brief_id`` is used to scope to accounts that appear in any
    brief_version belonging to that brief.

    Returns a list of dicts in STAGE_ORDER::

        [
            {
                "stage": str,
                "count": int,
                "conversion_rate": float | None,  # fraction into next stage
                "median_days_in_stage": float | None,
            },
            ...
        ]
    """
    # 1. Stage counts
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT a.stage, count(*) AS cnt
                  FROM public.account a
                 WHERE a.workspace_id = %s
                   AND EXISTS (
                     SELECT 1 FROM public.verdict v
                       JOIN public.entity e ON e.id = v.entity_id
                      WHERE e.id = a.entity_id
                        AND v.brief_version_id IN (
                          SELECT bv.id
                            FROM public.brief_version bv
                           WHERE bv.brief_id = %s
                        )
                   )
                 GROUP BY a.stage
                """,
                (str(workspace_id), str(brief_id)),
            )
            stage_rows = await cur.fetchall()

    counts: dict[str, int] = {}
    for stage_name, cnt in (stage_rows or []):
        if stage_name:
            counts[stage_name] = int(cnt)

    # 2. Median days in each stage via account_stage_event
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT
                    ase.to_stage AS stage,
                    percentile_cont(0.5) WITHIN GROUP (
                        ORDER BY
                            EXTRACT(EPOCH FROM (
                                COALESCE(
                                    (SELECT MIN(ase2.occurred_at)
                                       FROM public.account_stage_event ase2
                                      WHERE ase2.account_id = ase.account_id
                                        AND ase2.occurred_at > ase.occurred_at),
                                    now()
                                ) - ase.occurred_at
                            )) / 86400.0
                    ) AS median_days
                  FROM public.account_stage_event ase
                  JOIN public.account a ON a.id = ase.account_id
                 WHERE a.workspace_id = %s
                 GROUP BY ase.to_stage
                """,
                (str(workspace_id),),
            )
            median_rows = await cur.fetchall()

    median_days: dict[str, float | None] = {}
    for stage_name, med in (median_rows or []):
        if stage_name:
            median_days[stage_name] = float(med) if med is not None else None

    # 3. Build ordered funnel rows
    funnel: list[dict] = []
    for i, stage in enumerate(STAGE_ORDER):
        cnt = counts.get(stage, 0)
        funnel.append(
            {
                "stage": stage,
                "count": cnt,
                "conversion_rate": None,  # filled below
                "median_days_in_stage": median_days.get(stage),
            }
        )

    # 4. Compute conversion_rate: count[i] / count[i-1] for active->next pairs
    for i in range(1, len(funnel)):
        prev_count = funnel[i - 1]["count"]
        curr_count = funnel[i]["count"]
        if prev_count > 0:
            funnel[i]["conversion_rate"] = curr_count / prev_count
        else:
            funnel[i]["conversion_rate"] = None

    log.info(
        "reporting.funnel_computed",
        workspace_id=str(workspace_id),
        brief_id=str(brief_id),
        stages=len(funnel),
    )

    return funnel
