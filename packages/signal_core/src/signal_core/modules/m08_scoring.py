"""M8 Scoring engine — purely mathematical, no AI gateway call.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if None).
  2. Find account_id from entity + workspace.
  3. Fetch the latest verdict for entity+brief_version.
  4. Fetch all active claims for entity.
  5. Compute 5 dimensions: fit, evidence, scale, timing, reachability.
  6. Apply gates (Contradicted → Excluded; exclusion_list → Excluded; Unverified → Low cap).
  7. Compute weighted priority score and assign band.
  8. Persist via app.upsert_score and batch app.upsert_label calls.
  9. Return the band string.
"""

from __future__ import annotations

import math
import json
from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import UUID

from signal_core.derivation.writer import upsert_score, upsert_label
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Predicates considered "Fit" claims
FIT_PREDICATES = {"vertical", "size", "geography", "commercial_role"}

# Default weights
DEFAULT_WEIGHTS: dict[str, float] = {
    "fit": 0.25,
    "evidence": 0.25,
    "scale": 0.25,
    "timing": 0.25,
}

# Default band thresholds
DEFAULT_THRESHOLDS: dict[str, float] = {
    "High": 70.0,
    "Medium": 40.0,
    "Low": 0.0,
}

# Evidence verdict → score
VERDICT_EVIDENCE_SCORE: dict[str, int] = {
    "Confirmed": 90,
    "Likely": 65,
    "Unverified": 10,
    "Contradicted": 0,
}


def _band_from_priority(priority: float, thresholds: dict[str, float]) -> str:
    if priority >= thresholds.get("High", 70.0):
        return "High"
    if priority >= thresholds.get("Medium", 40.0):
        return "Medium"
    return "Low"


async def compute_score(
    pool: object,  # AsyncConnectionPool
    entity_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Compute and persist score+labels for entity in workspace.

    Returns band string ('High','Medium','Low','Excluded') or None when no brief_version.
    NO AI gateway — purely mathematical.
    """
    # 1. Find latest published brief_version for this workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT bv.id, bv.org_id, bv.config
                FROM public.brief_version bv
                WHERE bv.workspace_id = %s
                ORDER BY bv.published_at DESC
                LIMIT 1
                """,
                (str(workspace_id),),
            )
            bv_row = await cur.fetchone()

    if bv_row is None:
        log.info("score.no_brief_version", workspace_id=str(workspace_id))
        return None

    brief_version_id = UUID(str(bv_row[0]))
    org_id = UUID(str(bv_row[1]))
    config: dict[str, Any] = bv_row[2] or {}

    # Parse weights + thresholds from config
    weights_raw = config.get("weights") or {}
    if isinstance(weights_raw, str):
        weights_raw = json.loads(weights_raw)
    weights = {
        "fit": float(weights_raw.get("fit", DEFAULT_WEIGHTS["fit"])),
        "evidence": float(weights_raw.get("evidence", DEFAULT_WEIGHTS["evidence"])),
        "scale": float(weights_raw.get("scale", DEFAULT_WEIGHTS["scale"])),
        "timing": float(weights_raw.get("timing", DEFAULT_WEIGHTS["timing"])),
    }

    thresholds_raw = config.get("band_thresholds") or {}
    if isinstance(thresholds_raw, str):
        thresholds_raw = json.loads(thresholds_raw)
    thresholds = {
        "High": float(thresholds_raw.get("High", DEFAULT_THRESHOLDS["High"])),
        "Medium": float(thresholds_raw.get("Medium", DEFAULT_THRESHOLDS["Medium"])),
        "Low": float(thresholds_raw.get("Low", DEFAULT_THRESHOLDS["Low"])),
    }

    # Parse personas from config
    personas_raw = config.get("personas") or []
    if isinstance(personas_raw, str):
        personas_raw = json.loads(personas_raw)
    required_personas: list[str] = [str(p) for p in personas_raw] if personas_raw else []

    # 2. Find account_id from entity + workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.id, a.org_id FROM public.account a "
                "WHERE a.entity_id = %s AND a.workspace_id = %s",
                (str(entity_id), str(workspace_id)),
            )
            acct_row = await cur.fetchone()

    if acct_row is None:
        log.info(
            "score.no_account",
            entity_id=str(entity_id),
            workspace_id=str(workspace_id),
        )
        return None

    account_id = UUID(str(acct_row[0]))

    # 3. Fetch latest verdict for entity + brief_version
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT v.value FROM public.verdict v "
                "WHERE v.entity_id = %s AND v.brief_version_id = %s "
                "ORDER BY v.computed_at DESC LIMIT 1",
                (str(entity_id), str(brief_version_id)),
            )
            verdict_row = await cur.fetchone()

    verdict_value: str = verdict_row[0] if verdict_row else "Unverified"

    # 4. Fetch all active claims for entity
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT c.id, p.key, c.value, c.confidence, c.created_at, c.source_record_id
                FROM public.claim c
                JOIN public.predicate p ON p.id = c.predicate_id
                WHERE c.entity_id = %s AND c.status = 'active'
                """,
                (str(entity_id),),
            )
            claims = await cur.fetchall()

    # 5a. Compute fit score
    fit_claims = [c for c in claims if c[1] in FIT_PREDICATES]
    if fit_claims:
        fit_confidences = [
            int(c[3]) if c[3] is not None else 50 for c in fit_claims
        ]
        fit_score = int(sum(fit_confidences) / len(fit_confidences))
    else:
        fit_score = 50

    fit_claim_ids = [str(c[0]) for c in fit_claims]

    # 5b. Compute evidence score from verdict
    evidence_base = VERDICT_EVIDENCE_SCORE.get(verdict_value, 10)
    claim_bonus = min(len(claims) * 2, 100 - evidence_base) if len(claims) > 1 else 0
    evidence_score = min(evidence_base + claim_bonus, 100)

    # 5c. Compute scale score from quantity predicates
    qty_predicates = {"sites", "seats", "units"}
    qty_claims = [c for c in claims if c[1] in qty_predicates]
    if qty_claims:
        total_qty = 0.0
        for c in qty_claims:
            try:
                total_qty += float(c[2])
            except (TypeError, ValueError):
                pass
        scale_score = int(min(math.log1p(total_qty) * 10, 100))
    else:
        scale_score = 0

    # 5d. Compute timing score: count recent claims (last 90 days)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=90)
    recent_count = 0
    for c in claims:
        created_at = c[4]
        if created_at is not None:
            # created_at may be a datetime or a string
            if isinstance(created_at, datetime):
                ts = created_at
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            else:
                try:
                    ts = datetime.fromisoformat(str(created_at))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            if ts > cutoff:
                recent_count += 1

    timing_score = min(recent_count * 10, 100)

    # 5e. Compute reachability: valid contact_points / required_personas
    if required_personas:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT cp.id FROM public.contact_point cp "
                    "WHERE cp.account_id = %s AND cp.status = 'valid'",
                    (str(account_id),),
                )
                cp_rows = await cur.fetchall()
        covered_count = min(len(cp_rows), len(required_personas))
        reachability_score = int(covered_count / len(required_personas) * 100)
        covered_personas = required_personas[:covered_count]
        uncovered_personas = required_personas[covered_count:]
    else:
        reachability_score = 0
        covered_personas = []
        uncovered_personas = []

    readiness_score = reachability_score  # synonym in this context

    # 6. Apply gates
    gates: list[dict] = []

    # Gate 1: Contradicted verdict → Excluded
    if verdict_value == "Contradicted":
        gates.append({"reason": "verdict_contradicted"})
        band = "Excluded"
        log.info("score.gate.contradicted", entity_id=str(entity_id))
    else:
        # Gate 2: exclusion_list check
        excluded_by_list = False
        try:
            async with pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT 1 FROM public.exclusion_list "
                        "WHERE entity_id = %s AND org_id = %s LIMIT 1",
                        (str(entity_id), str(org_id)),
                    )
                    ex_row = await cur.fetchone()
                    excluded_by_list = ex_row is not None
        except Exception:
            excluded_by_list = False

        if excluded_by_list:
            gates.append({"reason": "exclusion_list"})
            band = "Excluded"
        else:
            # Compute priority score
            priority = (
                fit_score * weights["fit"]
                + evidence_score * weights["evidence"]
                + scale_score * weights["scale"]
                + timing_score * weights["timing"]
            )

            # Assign band from thresholds
            band = _band_from_priority(priority, thresholds)

            # Gate 3: Unverified caps at Low
            if verdict_value == "Unverified" and band in ("High", "Medium"):
                gates.append({"reason": "verdict_unverified"})
                band = "Low"

    # Priority (computed even for Excluded, for the explanation)
    priority = (
        fit_score * weights["fit"]
        + evidence_score * weights["evidence"]
        + scale_score * weights["scale"]
        + timing_score * weights["timing"]
    )

    # Build explanation JSON
    explanation = {
        "fit": {"score": fit_score, "claim_ids": fit_claim_ids},
        "evidence": {"score": evidence_score, "verdict": verdict_value},
        "scale": {"score": scale_score, "qty_claim_count": len(qty_claims)},
        "timing": {"score": timing_score, "recent_count": recent_count},
        "reachability": {
            "score": reachability_score,
            "covered": len(covered_personas),
            "total": len(required_personas),
        },
        "gates": gates,
    }

    inputs_dict = {
        "module": "m08_scoring",
        "workspace_id": str(workspace_id),
        "entity_id": str(entity_id),
        "job_id": ctx.job_id,
        "job_type": ctx.job_type,
    }

    # 7. Persist score
    await upsert_score(
        pool,  # type: ignore[arg-type]
        account_id,
        brief_version_id,
        fit_score,
        evidence_score,
        scale_score,
        timing_score,
        reachability_score,
        priority,
        readiness_score,
        band,
        gates,
        explanation,
        inputs_dict,
    )

    # 8. Persist labels
    # radar:<source> — one per unique source_record_id in claims
    seen_sources: set[str] = set()
    for c in claims:
        src = c[5]
        if src is not None:
            src_str = str(src)
            if src_str not in seen_sources:
                seen_sources.add(src_str)
                await upsert_label(
                    pool,  # type: ignore[arg-type]
                    account_id,
                    brief_version_id,
                    f"radar:{src_str}",
                    src_str,
                    inputs_dict,
                )

    # vertical:<value> — one per vertical claim value
    for c in claims:
        if c[1] == "vertical":
            await upsert_label(
                pool,  # type: ignore[arg-type]
                account_id,
                brief_version_id,
                f"vertical:{c[2]}",
                str(c[2]),
                inputs_dict,
            )

    # geography:<country> — one per geography claim value
    for c in claims:
        if c[1] == "geography":
            await upsert_label(
                pool,  # type: ignore[arg-type]
                account_id,
                brief_version_id,
                f"geography:{c[2]}",
                str(c[2]),
                inputs_dict,
            )

    # persona_coverage:<persona_key>:<covered|uncovered>
    for persona_key in covered_personas:
        await upsert_label(
            pool,  # type: ignore[arg-type]
            account_id,
            brief_version_id,
            f"persona_coverage:{persona_key}:covered",
            persona_key,
            inputs_dict,
        )
    for persona_key in uncovered_personas:
        await upsert_label(
            pool,  # type: ignore[arg-type]
            account_id,
            brief_version_id,
            f"persona_coverage:{persona_key}:uncovered",
            persona_key,
            inputs_dict,
        )

    log.info(
        "score.computed",
        entity_id=str(entity_id),
        workspace_id=str(workspace_id),
        band=band,
        priority=priority,
    )
    return band
