"""M8 Scoring and labels — deterministic, no AI call.

For the entity's account, under the current version of every Pilot/Active Brief in the workspace:
  1. Read the verdict, the claims that still count (active, not expired) and valid contact points.
  2. Compute five dimensions (0–100): fit, evidence, scale, timing, reachability. A claim's
     weight halves every `decay_half_life_days` of its predicate.
  3. Apply gates: Contradicted verdict or exclusion list → Excluded; Unverified caps at Low.
  4. Priority = weighted fit/evidence/scale/timing with the Brief's weights; band from the
     Brief's thresholds. Reachability is kept apart as readiness (M8).
  5. Store the score, write the labels this computation produces and remove the ones it no
     longer produces.
"""  # noqa: RUF002

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.briefs import ActiveBrief, active_brief_versions
from signal_core.derivation.writer import prune_labels, upsert_label, upsert_score
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

FIT_PREDICATES = {"vertical", "size", "geography", "commercial_role"}
QUANTITY_PREDICATES = {"sites", "seats", "units"}
LABEL_PREDICATES = ("vertical", "geography", "commercial_role")
TIMING_WINDOW_DAYS = 90

DEFAULT_WEIGHTS: dict[str, float] = {"fit": 0.25, "evidence": 0.25, "scale": 0.25, "timing": 0.25}
DEFAULT_THRESHOLDS: dict[str, float] = {"High": 70.0, "Medium": 40.0}
VERDICT_EVIDENCE_SCORE: dict[str, int] = {
    "Confirmed": 90,
    "Likely": 65,
    "Unverified": 10,
    "Contradicted": 0,
}

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


def _band_from_priority(priority: float, thresholds: dict[str, float]) -> str:
    if priority >= thresholds.get("High", DEFAULT_THRESHOLDS["High"]):
        return "High"
    if priority >= thresholds.get("Medium", DEFAULT_THRESHOLDS["Medium"]):
        return "Medium"
    return "Low"


def _settings(brief: ActiveBrief) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Weights, band thresholds and required persona roles from the Brief version."""
    raw_weights = {**(brief.config.get("weights") or {}), **brief.weights}
    weights = {k: float(raw_weights.get(k, default)) for k, default in DEFAULT_WEIGHTS.items()}
    raw_thresholds = raw_weights.get("band_thresholds") or brief.config.get("band_thresholds") or {}
    thresholds = {
        k: float(raw_thresholds.get(k, default)) for k, default in DEFAULT_THRESHOLDS.items()
    }
    personas = brief.config.get("personas") or {}
    if isinstance(personas, dict):
        roles = [role for role, titles in personas.items() if titles]
    else:
        roles = [str(p) for p in personas]
    return weights, thresholds, roles


def _decay(observed_at: Any, half_life_days: Any, now: datetime) -> float:
    if not half_life_days or not isinstance(observed_at, datetime):
        return 1.0
    ts = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
    return float(0.5 ** (age_days / float(half_life_days)))


def _label_value(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


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
    weights, thresholds, persona_roles = _settings(brief)
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

    decayed = [(c, _decay(c[4], c[5], now)) for c in claims]

    # Fit: confidence of the ICP claims, weighted by how fresh each is.
    fit_claims = [(c, w) for c, w in decayed if c[1] in FIT_PREDICATES]
    fit_weight = sum(w for _, w in fit_claims)
    fit_score = (
        round(sum((c[3] if c[3] is not None else 50) * w for c, w in fit_claims) / fit_weight)
        if fit_weight > 0
        else 50
    )

    # Evidence: the verdict, plus a small bonus for the weight of corroborating claims.
    evidence_base = VERDICT_EVIDENCE_SCORE.get(verdict_value, 10)
    corroboration = sum(w for _, w in decayed) if len(claims) > 1 else 0.0
    evidence_score = min(evidence_base + round(corroboration * 2), 100)

    # Scale: quantity predicates (sites, seats, units), log-scaled.
    total_qty = 0.0
    qty_claims = [c for c in claims if c[1] in QUANTITY_PREDICATES]
    for c in qty_claims:
        try:
            total_qty += float(c[2])
        except (TypeError, ValueError):
            continue
    scale_score = int(min(math.log1p(total_qty) * 10, 100)) if qty_claims else 0

    # Timing: recent signals.
    cutoff = now - timedelta(days=TIMING_WINDOW_DAYS)
    recent = [
        c
        for c in claims
        if isinstance(c[4], datetime)
        and (c[4] if c[4].tzinfo else c[4].replace(tzinfo=UTC)) > cutoff
    ]
    timing_score = min(len(recent) * 10, 100)

    # Reachability: valid contact points against the persona roles the Brief asks for.
    covered = min(valid_contacts, len(persona_roles))
    reachability_score = round(covered / len(persona_roles) * 100) if persona_roles else 0

    priority = (
        fit_score * weights["fit"]
        + evidence_score * weights["evidence"]
        + scale_score * weights["scale"]
        + timing_score * weights["timing"]
    )

    gates: list[dict[str, str]] = []
    if verdict_value == "Contradicted":
        gates.append({"reason": "verdict_contradicted"})
        band = "Excluded"
    elif excluded_by_list:
        gates.append({"reason": "exclusion_list"})
        band = "Excluded"
    else:
        band = _band_from_priority(priority, thresholds)
        if verdict_value == "Unverified" and band in ("High", "Medium"):
            gates.append({"reason": "verdict_unverified"})
            band = "Low"

    explanation = {
        "fit": {"score": fit_score, "claim_ids": [str(c[0]) for c, _ in fit_claims]},
        "evidence": {"score": evidence_score, "verdict": verdict_value, "claims": len(claims)},
        "scale": {"score": scale_score, "claim_ids": [str(c[0]) for c in qty_claims]},
        "timing": {"score": timing_score, "claim_ids": [str(c[0]) for c in recent]},
        "reachability": {
            "score": reachability_score,
            "covered": covered,
            "total": len(persona_roles),
        },
        "weights": weights,
        "gates": gates,
    }
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
        fit_score,
        evidence_score,
        scale_score,
        timing_score,
        reachability_score,
        priority,
        reachability_score,
        band,
        gates,
        explanation,
        inputs,
    )

    # Labels are lenses (M8): the radar that found the account, ICP facets, persona coverage.
    labels: set[tuple[str, str]] = set()
    for c in claims:
        if c[6] and str(c[6]).startswith("radar:"):
            labels.add(("radar", str(c[6])[len("radar:") :]))
        if c[1] in LABEL_PREDICATES:
            labels.add((str(c[1]), _label_value(c[2])))
    for i, role in enumerate(persona_roles):
        labels.add(("persona_coverage", f"{role}:{'covered' if i < covered else 'uncovered'}"))

    ordered = sorted(labels)
    for kind, value in ordered:
        await upsert_label(pool, account_id, brief.brief_version_id, kind, value, inputs)
    await prune_labels(
        pool, account_id, brief.brief_version_id, [k for k, _ in ordered], [v for _, v in ordered]
    )

    log.info(
        "score.computed",
        entity_id=str(entity_id),
        brief_version_id=str(brief.brief_version_id),
        band=band,
        priority=priority,
    )
    return band
