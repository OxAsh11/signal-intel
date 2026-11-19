"""M8 scoring maths as a pure function: no IO, so live scoring and the edit preview agree.

Five dimensions (0–100): fit, evidence, scale, timing, reachability. A claim's weight halves every
`decay_half_life_days` of its predicate. Gates, in order: Contradicted verdict, exclusion list,
outside the Brief's ICP → Excluded; Unverified caps at Low. Priority = weighted
fit/evidence/scale/timing; band from the Brief's thresholds. Reachability is readiness, kept apart.
"""  # noqa: RUF002

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from signal_core.brief_columns import ICP_LIST_BY_ROLE, ColumnSpec, column_specs

TIMING_WINDOW_DAYS = 90
# A config with no column specs (legacy plain keys) reads these roles.
LEGACY_ROLES: dict[str, str] = {
    "vertical": "fit.vertical",
    "size": "fit.size",
    "geography": "fit.geography",
    "commercial_role": "fit.commercial_role",
    "sites": "scale",
    "seats": "scale",
    "units": "scale",
}
LABEL_KIND_BY_ROLE: dict[str, str] = {
    "fit.vertical": "vertical",
    "fit.geography": "geography",
    "fit.commercial_role": "commercial_role",
}
GATED_ROLES = ("fit.vertical", "fit.geography")
_BAND_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
_BAND_OPEN = re.compile(r"^(\d+)\+$")

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


# The connector of the source record a formula's claims are written against (0039).
DERIVED_CONNECTOR_PREFIX = "derived:"


def is_derived(claim: tuple[Any, ...]) -> bool:
    """Was this claim (a ScoreInputs row) computed by a formula rather than found in a source?"""
    return len(claim) > 6 and str(claim[6] or "").startswith(DERIVED_CONNECTOR_PREFIX)


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


def predicate_roles(config: dict[str, Any]) -> dict[str, str]:
    """Predicate key → scoring role: the Brief's column specs, or the legacy defaults."""
    return _roles_of(column_specs(config))


def _roles_of(specs: list[ColumnSpec]) -> dict[str, str]:
    return {sp.key: sp.role for sp in specs} if specs else dict(LEGACY_ROLES)


def _icp_entries(config: dict[str, Any], role: str) -> list[str]:
    name = ICP_LIST_BY_ROLE.get(role)
    if not name:
        return []
    icp = config.get("icp") or {}
    raw = icp.get(name.split(".", 1)[1])
    if not isinstance(raw, list):
        return []
    return [str(e) for e in raw if str(e).strip()]


def _word_in(needle: str, haystack: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            n = float(value.strip())
        except ValueError:
            return None
        return n if math.isfinite(n) else None
    return None


def value_matches_icp(role: str, value: Any, entries: list[str]) -> bool | None:
    """Does a claim value fall in the ICP list? None when it can't tell (never counts)."""
    if value is None or not entries:
        return None
    text = _text(value).strip().casefold()
    folded = [e.strip().casefold() for e in entries if e.strip()]
    if role == "fit.size":
        if text in folded:
            return True
        number = _as_number(value)
        if number is None:
            return None
        parsable = False
        for entry in folded:
            if m := _BAND_RANGE.match(entry):
                lo, hi = int(m.group(1)), int(m.group(2))
                if lo > hi:
                    continue
                parsable = True
                if lo <= number <= hi:
                    return True
            elif m := _BAND_OPEN.match(entry):
                parsable = True
                if number >= int(m.group(1)):
                    return True
        return False if parsable else None
    if not text:
        return None
    return any(text == e or _word_in(e, text) or _word_in(text, e) for e in folded)


def _decisive(
    role: str, entries: list[str], claims: list[tuple[Any, ...]]
) -> list[tuple[tuple[Any, ...], bool]]:
    out: list[tuple[tuple[Any, ...], bool]] = []
    for c in claims:
        hit = value_matches_icp(role, c[2], entries)
        if hit is not None:
            out.append((c, hit))
    return out


def score_account(
    config: dict[str, Any], weights_cfg: dict[str, Any], inputs: ScoreInputs, now: datetime
) -> ScoreResult:
    weights, thresholds, persona_roles = settings(config, weights_cfg)
    claims = inputs.claims
    decayed = [(c, _decay(c[4], c[5], now)) for c in claims]

    specs = column_specs(config)
    roles = _roles_of(specs)
    legacy = not specs

    # Fit: per fit-role predicate, 100 if any decisive claim is in the Brief's ICP list, else 0,
    # weighted by the freshest of its claims.
    fit_total = 0.0
    fit_weight = 0.0
    fit_ids: list[str] = []
    matched: list[str] = []
    missed: list[str] = []
    icp_miss: str | None = None
    for key, role in roles.items():
        if not role.startswith("fit."):
            continue
        entries = _icp_entries(config, role)
        pred_claims = [(c, w) for c, w in decayed if c[1] == key]
        decisive = _decisive(role, entries, [c for c, _ in pred_claims]) if entries else []
        if not decisive:
            continue
        decay_by_id = {id(c): w for c, w in pred_claims}
        hit = any(h for _, h in decisive)
        weight = max(decay_by_id[id(c)] for c, _ in decisive)
        fit_total += (100 if hit else 0) * weight
        fit_weight += weight
        fit_ids.extend(str(c[0]) for c, _ in decisive)
        (matched if hit else missed).append(key)
        if not hit and role in GATED_ROLES and icp_miss is None:
            icp_miss = key
    fit_score = round(fit_total / fit_weight) if fit_weight > 0 else 50

    # Evidence: the verdict, plus a small bonus for the weight of corroborating claims. A formula's
    # derived claim restates other claims, so it never corroborates (it still counts for fit,
    # scale, timing and labels below).
    evidence_base = VERDICT_EVIDENCE_SCORE.get(inputs.verdict, 10)
    sourced = [(c, w) for c, w in decayed if not is_derived(c)]
    corroboration = sum(w for _, w in sourced) if len(sourced) > 1 else 0.0
    evidence_score = min(evidence_base + round(corroboration * 2), 100)

    # Scale: numeric values of scale-role predicates, log-scaled.
    total_qty = 0.0
    qty_claims: list[tuple[Any, ...]] = []
    for c in claims:
        if roles.get(c[1]) != "scale":
            continue
        n = _as_number(c[2])
        if n is None:
            continue
        total_qty += n
        qty_claims.append(c)
    scale_score = int(min(math.log1p(total_qty) * 10, 100)) if qty_claims else 0

    # Timing: recent claims; with column specs only the signal-role predicates count.
    cutoff = now - timedelta(days=TIMING_WINDOW_DAYS)
    recent = [
        c
        for c in claims
        if (legacy or roles.get(c[1]) == "signal")
        and isinstance(c[4], datetime)
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
        "fit": {
            "score": fit_score,
            "claim_ids": fit_ids,
            "matched": matched,
            "missed": missed,
        },
        "evidence": {"score": evidence_score, "verdict": inputs.verdict, "claims": len(sourced)},
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
        kind = LABEL_KIND_BY_ROLE.get(roles.get(c[1], ""))
        if kind:
            labels.add((kind, _text(c[2])))
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
