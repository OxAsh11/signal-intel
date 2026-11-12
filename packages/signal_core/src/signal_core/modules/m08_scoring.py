"""M8 Scoring and labels — deterministic, no AI call.

For the entity's account, under the current version of every Pilot/Active Brief in the workspace:
  1. Read the verdict, the claims that still count (active, not expired) and valid contact points.
  2. Compute five dimensions (0–100): fit, evidence, scale, timing, reachability. A claim's
     weight halves every `decay_half_life_days` of its predicate.
  3. Apply gates: Contradicted verdict, exclusion list or outside the ICP → Excluded;
     Unverified caps at Low.
  4. Priority = weighted fit/evidence/scale/timing with the Brief's weights; band from the
     Brief's thresholds. Reachability is kept apart as readiness (M8).
  5. Store the score, write the labels this computation produces and remove the ones it no
     longer produces.
"""  # noqa: RUF002

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.briefs import ActiveBrief, active_brief_versions
from signal_core.derivation.writer import prune_labels, upsert_label, upsert_score
from signal_core.logging import get_logger
from signal_core.metering import CallContext
from signal_core.scoring_core import ScoreInputs, score_account

log = get_logger(__name__)

# id, predicate key, value, confidence, observed_at, half-life (days), connector that found it
_CLAIMS_SQL = """
    SELECT c.id, p.key, c.value, c.confidence, c.observed_at, p.decay_half_life_days,
           sr.connector_id
      FROM public.claim c
      JOIN public.predicate p ON p.id = c.predicate_id
      JOIN public.source_record sr ON sr.id = c.source_record_id
     WHERE c.entity_id = %s
       AND c.status = 'active'
       AND (c.expires_at IS NULL OR c.expires_at > now())
"""


async def compute_score(
    pool: AsyncConnectionPool,
    entity_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Compute and store score and labels under every active Brief version.

    Returns the band under the most recently published Brief, or None when the workspace has no
    active Brief or no account on the entity.
    """
    briefs = await active_brief_versions(pool, workspace_id)
    if not briefs:
        log.info("score.no_brief_version", workspace_id=str(workspace_id))
        return None

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT a.id FROM public.account a WHERE a.entity_id = %s AND a.workspace_id = %s",
            (str(entity_id), str(workspace_id)),
        )
        account_row = await cur.fetchone()
        if account_row is None:
            log.info("score.no_account", entity_id=str(entity_id), workspace_id=str(workspace_id))
            return None
        account_id = UUID(str(account_row[0]))

        await cur.execute(_CLAIMS_SQL, (str(entity_id),))
        claims = await cur.fetchall()

        await cur.execute(
            "SELECT count(*) FROM public.contact_point cp"
            " WHERE cp.account_id = %s AND cp.status = 'valid'",
            (str(account_id),),
        )
        contacts_row = await cur.fetchone()
    valid_contacts = int(contacts_row[0]) if contacts_row else 0

    bands = [
        await _score_for_brief(pool, brief, account_id, entity_id, claims, valid_contacts, ctx)
        for brief in briefs
    ]
    return bands[0]


async def _score_for_brief(
    pool: AsyncConnectionPool,
    brief: ActiveBrief,
    account_id: UUID,
    entity_id: UUID,
    claims: list[tuple[Any, ...]],
    valid_contacts: int,
    ctx: CallContext,
) -> str:
    now = datetime.now(UTC)

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT v.value FROM public.verdict v"
            " WHERE v.entity_id = %s AND v.brief_version_id = %s"
            " ORDER BY v.computed_at DESC LIMIT 1",
            (str(entity_id), str(brief.brief_version_id)),
        )
        verdict_row = await cur.fetchone()
        await cur.execute(
            "SELECT 1 FROM public.exclusion_list WHERE entity_id = %s AND org_id = %s LIMIT 1",
            (str(entity_id), str(brief.org_id)),
        )
        excluded_by_list = await cur.fetchone() is not None
    verdict_value: str = verdict_row[0] if verdict_row else "Unverified"

    result = score_account(
        brief.config,
        brief.weights,
        ScoreInputs(claims, verdict_value, valid_contacts, excluded_by_list),
        now,
    )
    inputs = {
        "module": "m08_scoring",
        "entity_id": str(entity_id),
        "job_id": ctx.job_id,
        "job_type": ctx.job_type,
    }

    await upsert_score(
        pool,
        account_id,
        brief.brief_version_id,
        result.fit,
        result.evidence,
        result.scale,
        result.timing,
        result.reachability,
        result.priority,
        result.reachability,
        result.band,
        result.gates,
        result.explanation,
        inputs,
    )

    for kind, value in result.labels:
        await upsert_label(pool, account_id, brief.brief_version_id, kind, value, inputs)
    await prune_labels(
        pool,
        account_id,
        brief.brief_version_id,
        [k for k, _ in result.labels],
        [v for _, v in result.labels],
    )

    log.info(
        "score.computed",
        entity_id=str(entity_id),
        brief_version_id=str(brief.brief_version_id),
        band=result.band,
        priority=result.priority,
    )
    return result.band
