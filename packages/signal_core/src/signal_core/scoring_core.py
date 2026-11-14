"""M8 scoring maths as a pure function: no IO, so live scoring and the edit preview agree.

Five dimensions (0–100): fit, evidence, scale, timing, reachability. A claim's weight halves every
`decay_half_life_days` of its predicate. Gates, in order: Contradicted verdict, exclusion list,
outside the Brief's ICP → Excluded; Unverified caps at Low. Priority = weighted
fit/evidence/scale/timing; band from the Brief's thresholds. Reachability is readiness, kept apart.
"""  # noqa: RUF002

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

FIT_PREDICATES = {"vertical", "size", "geography", "commercial_role"}
QUANTITY_PREDICATES = {"sites", "seats", "units"}
LABEL_PREDICATES = ("vertical", "geography", "commercial_role")
TIMING_WINDOW_DAYS = 90
# Claim predicate → the Brief's ICP list it must match (when that list is set). Size is left out:
# size claims are free text and size bands are ranges, so exact matching would wrongly exclude.
ICP_LISTS = {"vertical": "verticals", "geography": "geographies"}

DEFAULT_WEIGHTS: dict[str, float] = {"fit": 0.25, "evidence": 0.25, "scale": 0.25, "timing": 0.25}
DEFAULT_THRESHOLDS: dict[str, float] = {"High": 70.0, "Medium": 40.0}
VERDICT_EVIDENCE_SCORE: dict[str, int] = {
    "Confirmed": 90,
    "Likely": 65,
    "Unverified": 10,
    "Contradicted": 0,
}


@dataclass(frozen=True)
class ScoreInputs:
    # id, predicate key, value, confidence, observed_at, half-life (days), connector
    claims: list[tuple[Any, ...]]
    verdict: str
    valid_contacts: int
    excluded_by_list: bool


@dataclass(frozen=True)
class ScoreResult:
    fit: int
    evidence: int
    scale: int
    timing: int
    reachability: int
    priority: float
    band: str
    gates: list[dict[str, str]]
    explanation: dict[str, Any]
    labels: list[tuple[str, str]] = field(default_factory=list)


def settings(
    config: dict[str, Any], weights_cfg: dict[str, Any]
) -> tuple[dict[str, float], dict[str, float], list[str]]:
    """Weights, band thresholds and required persona roles from a Brief version's config."""
    raw_weights = {**(config.get("weights") or {}), **weights_cfg}
    weights = {k: float(raw_weights.get(k, default)) for k, default in DEFAULT_WEIGHTS.items()}
    raw_thresholds = raw_weights.get("band_thresholds") or config.get("band_thresholds") or {}
    thresholds = {
        k: float(raw_thresholds.get(k, default)) for k, default in DEFAULT_THRESHOLDS.items()
    }
    personas = config.get("personas") or {}
    if isinstance(personas, dict):
        roles = [role for role, titles in personas.items() if titles]
    else:
        roles = [str(p) for p in personas]
    return weights, thresholds, roles


def _band_from_priority(priority: float, thresholds: dict[str, float]) -> str:
    if priority >= thresholds.get("High", DEFAULT_THRESHOLDS["High"]):
        return "High"
    if priority >= thresholds.get("Medium", DEFAULT_THRESHOLDS["Medium"]):
        return "Medium"
    return "Low"


def _decay(observed_at: Any, half_life_days: Any, now: datetime) -> float:
    if not half_life_days or not isinstance(observed_at, datetime):
        return 1.0
    ts = observed_at if observed_at.tzinfo else observed_at.replace(tzinfo=UTC)
    age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
    return float(0.5 ** (age_days / float(half_life_days)))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def outside_icp(config: dict[str, Any], claims: list[tuple[Any, ...]]) -> str | None:
    """The first predicate whose known values all fall outside the Brief's ICP list, if any.

    Unknown values never exclude: a predicate with no claim, or an empty ICP list, passes.
    """
    icp = config.get("icp") or {}
    for key, list_name in ICP_LISTS.items():
        allowed = {_text(v).strip().lower() for v in (icp.get(list_name) or [])}
        if not allowed:
            continue
        values = {_text(c[2]).strip().lower() for c in claims if c[1] == key}
        if values and not values & allowed:
            return key
    return None


def score_account(
    config: dict[str, Any], weights_cfg: dict[str, Any], inputs: ScoreInputs, now: datetime
) -> ScoreResult:
    weights, thresholds, persona_roles = settings(config, weights_cfg)
    claims = inputs.claims
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
    evidence_base = VERDICT_EVIDENCE_SCORE.get(inputs.verdict, 10)
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
    covered = min(inputs.valid_contacts, len(persona_roles))
    reachability_score = round(covered / len(persona_roles) * 100) if persona_roles else 0

    priority = (
        fit_score * weights["fit"]
        + evidence_score * weights["evidence"]
        + scale_score * weights["scale"]
        + timing_score * weights["timing"]
    )

    gates: list[dict[str, str]] = []
    icp_miss = outside_icp(config, claims)
    if inputs.verdict == "Contradicted":
        gates.append({"reason": "verdict_contradicted"})
        band = "Excluded"
    elif inputs.excluded_by_list:
        gates.append({"reason": "exclusion_list"})
        band = "Excluded"
    elif icp_miss:
        gates.append({"reason": "outside_icp", "predicate": icp_miss})
        band = "Excluded"
    else:
        band = _band_from_priority(priority, thresholds)
        if inputs.verdict == "Unverified" and band in ("High", "Medium"):
            gates.append({"reason": "verdict_unverified"})
            band = "Low"

    explanation = {
        "fit": {"score": fit_score, "claim_ids": [str(c[0]) for c, _ in fit_claims]},
        "evidence": {"score": evidence_score, "verdict": inputs.verdict, "claims": len(claims)},
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

    # Labels are lenses (M8): the radar that found the account, ICP facets, persona coverage.
    labels: set[tuple[str, str]] = set()
    for c in claims:
        if c[6] and str(c[6]).startswith("radar:"):
            labels.add(("radar", str(c[6])[len("radar:") :]))
        if c[1] in LABEL_PREDICATES:
            labels.add((str(c[1]), _text(c[2])))
    for i, role in enumerate(persona_roles):
        labels.add(("persona_coverage", f"{role}:{'covered' if i < covered else 'uncovered'}"))

    return ScoreResult(
        fit=fit_score,
        evidence=evidence_score,
        scale=scale_score,
        timing=timing_score,
        reachability=reachability_score,
        priority=priority,
        band=band,
        gates=gates,
        explanation=explanation,
        labels=sorted(labels),
    )
