"""M1 edit preview: what a Brief edit would change, and what it would cost, before it applies.

The tier comes from the fields that differ (highest wins). The Narrow part is exact: scores are
recomputed with the candidate config over stored facts (no AI). Redefine and Expand are estimates
from counts and `model_price` (S6). The result lands on `brief_preview` for the UI.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.config import get_settings
from signal_core.logging import get_logger
from signal_core.scoring_core import ScoreInputs, score_account

log = get_logger(__name__)

NARROW_FIELDS = {"icp", "personas", "scoring", "budget", "weights", "band_thresholds"}
EXPAND_FIELDS = {"hypothesis", "sources", "seeds", "keywords"}
# Everything else (examples, predicates, evidence_rules, offer, research_template, unknown) is
# Redefine: it changes how existing evidence is read.
REJUDGE_FIELDS = {"hypothesis", "examples", "evidence_rules"}
TIER_ORDER = {"Narrow": 1, "Redefine": 2, "Expand": 3}

# Token assumptions per call (input, output).
JUDGE_TOKENS = (2000, 300)
EXTRACT_TOKENS = (3000, 500)
RELEVANCE_TOKENS = (1000, 50)
QUERIES_PER_RADAR_TYPE = 10
RECORDS_PER_QUERY = 20
CALLS_PER_MINUTE = 30
MIN_EXPAND_MINUTES = 60
LIST_CAP = 200


def classify_edit(old: dict[str, Any], new: dict[str, Any]) -> tuple[str | None, bool, list[str]]:
    """(tier, whether verdicts must be re-judged, changed top-level fields)."""
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    if not changed:
        return None, False, []
    tiers = [
        "Narrow" if k in NARROW_FIELDS else "Expand" if k in EXPAND_FIELDS else "Redefine"
        for k in changed
    ]
    tier = max(tiers, key=TIER_ORDER.__getitem__)
    return tier, any(k in REJUDGE_FIELDS for k in changed), changed


def _call_cost(
    prices: dict[tuple[str, str], Decimal], model: str, tokens: tuple[int, int]
) -> Decimal:
    rate_in = prices.get((model, "ai_input_tokens"), Decimal(0))
    rate_out = prices.get((model, "ai_output_tokens"), Decimal(0))
    return (tokens[0] * rate_in + tokens[1] * rate_out) / Decimal(1_000_000)


def estimate_cost(
    prices: dict[tuple[str, str], Decimal], models: dict[str, str], counts: dict[str, int]
) -> tuple[Decimal, int]:
    """(USD, minutes) for re-judging, re-extracting and collecting with new radar types."""
    rejudge = counts.get("rejudge_verdicts", 0)
    reextract = counts.get("reextract_records", 0)
    new_types = counts.get("new_radar_types", 0)
    judge = _call_cost(prices, models["judge"], JUDGE_TOKENS)
    extract = _call_cost(prices, models["extract"], EXTRACT_TOKENS)
    relevance = _call_cost(prices, models["prejudge"], RELEVANCE_TOKENS)

    new_records = new_types * QUERIES_PER_RADAR_TYPE * RECORDS_PER_QUERY
    usd = rejudge * judge + reextract * extract + new_records * (relevance + extract)
    minutes = math.ceil((rejudge + reextract) / CALLS_PER_MINUTE)
    if new_records:
        minutes += max(MIN_EXPAND_MINUTES, math.ceil(2 * new_records / CALLS_PER_MINUTE))
    return usd.normalize() if usd else Decimal(0), minutes


def over_budget(caps: list[tuple[Decimal, Decimal]], estimate: Decimal) -> bool:
    """True when spending `estimate` would pass any (cap, already spent) pair."""
    return any(spent + estimate > cap for cap, spent in caps)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


_PREVIEW_SQL = """
    SELECT p.brief_id, p.workspace_id, p.draft_config, p.base_version_id, bv.config, bv.weights
      FROM public.brief_preview p
      JOIN public.brief_version bv ON bv.id = p.base_version_id
     WHERE p.id = %s
"""

# Accounts scored under the base version, with what scoring needs.
_ACCOUNTS_SQL = """
    SELECT a.id, a.entity_id, e.display_name, s.band,
           coalesce(v.value::text, 'Unverified'),
           (SELECT count(*) FROM public.contact_point cp
             WHERE cp.account_id = a.id AND cp.status = 'valid'),
           EXISTS (SELECT 1 FROM public.exclusion_list x
                    WHERE x.entity_id = a.entity_id AND x.org_id = a.org_id)
      FROM public.score s
      JOIN public.account a ON a.id = s.account_id
      JOIN public.entity e ON e.id = a.entity_id
      LEFT JOIN public.verdict v
             ON v.entity_id = a.entity_id AND v.brief_version_id = s.brief_version_id
     WHERE s.brief_version_id = %s
"""

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


async def build_preview(pool: AsyncConnectionPool, preview_id: UUID) -> None:
    """Fill a pending `brief_preview`. Marks it failed (and re-raises) on any error."""
    try:
        await _build(pool, preview_id)
    except Exception:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute("SELECT app.fail_brief_preview(%s)", (str(preview_id),))
        raise


async def _build(pool: AsyncConnectionPool, preview_id: UUID) -> None:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(_PREVIEW_SQL, (str(preview_id),))
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"brief_preview {preview_id} not found")
    brief_id, workspace_id, draft, base_version_id, base_cfg, base_weights = row
    new_cfg, old_cfg, weights = _as_dict(draft), _as_dict(base_cfg), _as_dict(base_weights)

    tier, rejudge, changed = classify_edit(old_cfg, new_cfg)
    result = await _account_changes(pool, base_version_id, old_cfg, new_cfg, weights)
    result["changed_fields"] = changed

    counts = await _counts(pool, workspace_id, base_version_id, old_cfg, new_cfg, rejudge, changed)
    result["counts"] = counts
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT model, unit, usd_per_million FROM public.model_price")
        prices = {(r[0], str(r[1])): Decimal(str(r[2])) for r in await cur.fetchall()}
        await cur.execute(
            "SELECT b.cap_usd, app.period_spend(b.workspace_id, b.brief_id, b.period)"
            "  FROM public.budget b"
            " WHERE b.workspace_id = %s AND (b.brief_id IS NULL OR b.brief_id = %s)",
            (str(workspace_id), str(brief_id)),
        )
        caps = [(Decimal(str(r[0])), Decimal(str(r[1] or 0))) for r in await cur.fetchall()]

    s = get_settings()
    models = {
        "judge": s.ai_model_judge,
        "extract": s.ai_model_extract,
        "prejudge": s.ai_model_prejudge,
    }
    usd, minutes = estimate_cost(prices, models, counts)
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.complete_brief_preview(%s, %s, %s, %s::jsonb, %s, %s, %s)",
            (
                str(preview_id),
                tier,
                rejudge,
                json.dumps(result, default=str),
                usd,
                minutes,
                over_budget(caps, usd),
            ),
        )
    log.info("brief.preview_ready", preview_id=str(preview_id), tier=tier, estimate=str(usd))


async def _account_changes(
    pool: AsyncConnectionPool,
    version_id: Any,
    old_cfg: dict[str, Any],
    new_cfg: dict[str, Any],
    weights: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    entering: list[dict[str, Any]] = []
    leaving: list[dict[str, Any]] = []
    band_changes: list[dict[str, Any]] = []
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(_ACCOUNTS_SQL, (str(version_id),))
        accounts = await cur.fetchall()
        for account_id, entity_id, name, _stored, verdict, contacts, excluded in accounts:
            await cur.execute(_CLAIMS_SQL, (str(entity_id),))
            claims = await cur.fetchall()
            inputs = ScoreInputs(list(claims), str(verdict), int(contacts or 0), bool(excluded))
            before = score_account(old_cfg, weights, inputs, now).band
            after = score_account(new_cfg, weights, inputs, now).band
            if before == after:
                continue
            item = {"account_id": str(account_id), "name": name, "from": before, "to": after}
            if before == "Excluded":
                entering.append(item)
            elif after == "Excluded":
                leaving.append(item)
            else:
                band_changes.append(item)
    return {
        "accounts": len(accounts),
        "totals": {
            "entering": len(entering),
            "leaving": len(leaving),
            "band_changes": len(band_changes),
        },
        "entering": entering[:LIST_CAP],
        "leaving": leaving[:LIST_CAP],
        "band_changes": band_changes[:LIST_CAP],
    }


async def _counts(
    pool: AsyncConnectionPool,
    workspace_id: Any,
    version_id: Any,
    old_cfg: dict[str, Any],
    new_cfg: dict[str, Any],
    rejudge: bool,
    changed: list[str],
) -> dict[str, int]:
    counts = {"rejudge_verdicts": 0, "reextract_records": 0, "new_radar_types": 0}
    async with pool.connection() as conn, conn.cursor() as cur:
        if rejudge:
            await cur.execute(
                "SELECT count(*) FROM public.verdict WHERE brief_version_id = %s",
                (str(version_id),),
            )
            row = await cur.fetchone()
            counts["rejudge_verdicts"] = int(row[0]) if row else 0
        added = set(new_cfg.get("predicates") or []) - set(old_cfg.get("predicates") or [])
        if added:
            await cur.execute(
                "SELECT count(*) FROM public.source_record WHERE workspace_id = %s",
                (str(workspace_id),),
            )
            row = await cur.fetchone()
            counts["reextract_records"] = int(row[0]) if row else 0
    new_types = set((new_cfg.get("sources") or {}).get("radar_types") or [])
    old_types = set((old_cfg.get("sources") or {}).get("radar_types") or [])
    # A new hypothesis, keywords or seeds re-run every radar's queries; new sources run only theirs.
    if {"hypothesis", "keywords", "seeds"} & set(changed):
        counts["new_radar_types"] = len(new_types)
    else:
        counts["new_radar_types"] = len(new_types - old_types)
    return counts
