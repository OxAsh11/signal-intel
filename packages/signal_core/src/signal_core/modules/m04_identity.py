"""M4 Identity resolution — alias matching, review queue, and duplicate sweep.

Phase 1d, Task 3.

Flow for each unresolved mention:
  1. _lookup_aliases: query entity_alias in priority order (registry_id, profile_url,
     domain, name_country). Exact matches → confidence 95; fuzzy trgm → 70-89.
  2. If multiple candidate entities, run _ai_tiebreak (Capability.JUDGE).
  3. Route by winning confidence:
       >= 90  → resolve_mention + create_account  (returns 'attached')
       40-89  → create_review_item                (returns 'queued')
       < 40   → create_entity + alias + resolve   (returns 'created')

duplicate_sweep re-runs resolution on all unresolved mentions for an org.
"""

from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

TIEBREAK_PROMPT_NAME = "identity_tiebreak"
TIEBREAK_PROMPT_VERSION = 1

# Alias types checked in priority order (highest → lowest).
_EXACT_ALIAS_TYPES = ("registry_id", "profile_url", "domain")


# ---------------------------------------------------------------------------
# Output model for AI tiebreak
# ---------------------------------------------------------------------------


class IdentityTiebreak(BaseModel):
    winner_index: int | None
    confidence: int
    reason: str


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _fetch_mention(pool: AsyncConnectionPool, mention_id: UUID) -> dict:
    """Return mention row as a dict; raises ValueError if not found."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, source_record_id, entity_type,"
                " raw_name, raw_domain, raw_country, raw_attributes"
                " FROM public.mention WHERE id = %s",
                (str(mention_id),),
            )
            row = await cur.fetchone()
    if row is None:
        raise ValueError(f"mention {mention_id} not found")
    return {
        "id": str(row[0]),
        "org_id": str(row[1]),
        "source_record_id": str(row[2]),
        "entity_type": row[3],
        "raw_name": row[4],
        "raw_domain": row[5],
        "raw_country": row[6],
        "raw_attributes": row[7] or {},
    }


async def _lookup_aliases(
    conn,
    org_id: UUID,
    mention: dict,
) -> list[tuple[str, str, int]]:
    """Return list of (entity_id, alias_type, confidence) candidates.

    Checks alias types in priority order; stops at the first type that yields
    exact matches. Falls back to pg_trgm fuzzy matching on name_country.
    """
    raw_attrs = mention.get("raw_attributes") or {}

    # Build value map for exact-match alias types.
    alias_values: dict[str, str | None] = {
        "registry_id": raw_attrs.get("registry_id"),
        "profile_url": raw_attrs.get("profile_url"),
        "domain": mention.get("raw_domain"),
    }

    # Exact matches — stop at the first alias type that yields results.
    for alias_type in _EXACT_ALIAS_TYPES:
        value = alias_values.get(alias_type)
        if not value:
            continue
        value_norm = value.lower().strip()
        cur = await conn.execute(
            "SELECT entity_id FROM public.entity_alias"
            " WHERE org_id = %s AND alias_type = %s::public.alias_type"
            " AND value_normalised = %s"
            " LIMIT 5",
            (str(org_id), alias_type, value_norm),
        )
        rows = await cur.fetchall()
        if rows:
            return [(str(row[0]), alias_type, 95) for row in rows]

    # Fuzzy name_country match via pg_trgm.
    raw_name = mention.get("raw_name")
    if not raw_name:
        return []

    raw_country = (mention.get("raw_country") or "").lower().strip()
    name_norm = raw_name.lower().strip()
    name_country = f"{name_norm}|{raw_country}" if raw_country else name_norm

    cur = await conn.execute(
        "SELECT entity_id, similarity(value_normalised, %s) AS sim"
        " FROM public.entity_alias"
        " WHERE org_id = %s AND alias_type = 'name_country'"
        " AND similarity(value_normalised, %s) > 0.5"
        " ORDER BY sim DESC LIMIT 5",
        (name_country, str(org_id), name_country),
    )
    rows = await cur.fetchall()
    return [
        (str(row[0]), "name_country", min(89, 70 + round(float(row[1]) * 20)))
        for row in rows
    ]


async def _ai_tiebreak(
    gateway: AiGateway,
    mention: dict,
    candidates: list[tuple[str, str, int]],
    ctx: CallContext,
) -> tuple[str | None, int]:
    """Run AI tiebreak and return (winning_entity_id, confidence).

    Returns (None, 0) if the AI cannot decide.
    """
    prompt = load_prompt(TIEBREAK_PROMPT_NAME, TIEBREAK_PROMPT_VERSION)
    mention_text = json.dumps(
        {
            "raw_name": mention.get("raw_name"),
            "raw_domain": mention.get("raw_domain"),
            "raw_country": mention.get("raw_country"),
            "raw_attributes": mention.get("raw_attributes") or {},
        },
        default=str,
    )
    candidates_text = json.dumps(
        [
            {"index": i, "entity_id": c[0], "alias_type": c[1], "confidence": c[2]}
            for i, c in enumerate(candidates)
        ]
    )
    try:
        result = await gateway.run(
            Capability.JUDGE,
            prompt,
            {"mention": mention_text, "candidates": candidates_text},
            IdentityTiebreak,
            ctx,
        )
    except (AiOutputInvalid, AiProviderError) as exc:
        log.warning("identity.tiebreak_failed", mention_id=mention.get("id"), error=str(exc))
        return None, 0

    tb = result.value
    if tb.winner_index is None or not (0 <= tb.winner_index < len(candidates)):
        return None, 0
    return candidates[tb.winner_index][0], tb.confidence


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_mention(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    mention_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str:
    """Resolve a single mention to an existing entity or create a new one.

    Returns 'attached' | 'queued' | 'created'.
    """
    mention = await _fetch_mention(pool, mention_id)
    org_id = UUID(mention["org_id"])
    source_record_id = mention["source_record_id"]
    entity_type = mention["entity_type"]

    async with pool.connection() as conn:
        candidates = await _lookup_aliases(conn, org_id, mention)

    if not candidates:
        # No alias match — create a new entity.
        return await _create_new(
            pool, org_id, workspace_id, mention_id, source_record_id, entity_type, mention
        )

    # Deduplicate candidates by entity_id (keep highest confidence per entity).
    by_entity: dict[str, tuple[str, int]] = {}
    for entity_id, alias_type, conf in candidates:
        if entity_id not in by_entity or conf > by_entity[entity_id][1]:
            by_entity[entity_id] = (alias_type, conf)

    if len(by_entity) == 1:
        entity_id, (alias_type, confidence) = next(iter(by_entity.items()))
    else:
        # Multiple distinct entities — ask the AI to choose.
        tiebreak_candidates = [
            (eid, at, c) for eid, (at, c) in by_entity.items()
        ]
        entity_id, confidence = await _ai_tiebreak(gateway, mention, tiebreak_candidates, ctx)
        if entity_id is None:
            # AI couldn't decide — create new.
            return await _create_new(
                pool, org_id, workspace_id, mention_id, source_record_id, entity_type, mention
            )
        alias_type = by_entity[entity_id][0]

    if confidence >= 90:
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.resolve_mention(%s, %s, %s, %s::smallint)",
                (str(mention_id), entity_id, alias_type, confidence),
            )
            await conn.execute(
                "SELECT app.create_account(%s, %s, %s)",
                (str(org_id), str(workspace_id), entity_id),
            )
        log.info(
            "identity.attached",
            mention_id=str(mention_id),
            entity_id=entity_id,
            alias_type=alias_type,
            confidence=confidence,
        )
        return "attached"

    if confidence >= 40:
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.create_review_item(%s, %s, %s, %s, %s, %s::smallint)",
                (
                    str(org_id),
                    str(workspace_id),
                    str(mention_id),
                    entity_id,
                    alias_type,
                    confidence,
                ),
            )
        log.info(
            "identity.queued",
            mention_id=str(mention_id),
            entity_id=entity_id,
            alias_type=alias_type,
            confidence=confidence,
        )
        return "queued"

    # Confidence too low — create new entity.
    return await _create_new(
        pool, org_id, workspace_id, mention_id, source_record_id, entity_type, mention
    )


async def _create_new(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    mention_id: UUID,
    source_record_id: str,
    entity_type: str,
    mention: dict,
) -> str:
    """Create a new entity, alias, resolve the mention, and create an account."""
    display_name = mention.get("raw_name") or "unknown"

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_entity(%s, %s::public.entity_type, %s)",
                (str(org_id), entity_type, display_name),
            )
            ent_row = await cur.fetchone()
    entity_id = str(ent_row[0])

    # Create name_country alias so future mentions can find this entity.
    raw_name = mention.get("raw_name")
    if raw_name:
        raw_country = (mention.get("raw_country") or "").lower().strip()
        name_norm = raw_name.lower().strip()
        value_normalised = f"{name_norm}|{raw_country}" if raw_country else name_norm
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.create_entity_alias(%s, %s, %s::public.alias_type, %s, %s::smallint, %s)",
                (
                    str(org_id),
                    entity_id,
                    "name_country",
                    value_normalised,
                    50,  # moderate confidence for newly minted alias
                    source_record_id,
                ),
            )

    async with pool.connection() as conn:
        await conn.execute(
            "SELECT app.resolve_mention(%s, %s, %s, %s::smallint)",
            (str(mention_id), entity_id, "new_entity", 100),
        )
        await conn.execute(
            "SELECT app.create_account(%s, %s, %s)",
            (str(org_id), str(workspace_id), entity_id),
        )

    log.info(
        "identity.created",
        mention_id=str(mention_id),
        entity_id=entity_id,
        display_name=display_name,
    )
    return "created"


async def duplicate_sweep(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    org_id: UUID,
    ctx: CallContext,
) -> int:
    """Re-run identity resolution on all unresolved mentions for an org.

    Returns the count of mentions that were resolved in this sweep.
    Dispatch happens in the same process (no job deferral) so the sweep is
    idempotent and can be retried safely.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT m.id, m.workspace_id"
                " FROM public.mention m"
                " JOIN public.source_record sr ON sr.id = m.source_record_id"
                " WHERE m.org_id = %s AND m.entity_id IS NULL"
                " ORDER BY m.id",
                (str(org_id),),
            )
            rows = await cur.fetchall()

    count = 0
    for row in rows:
        mention_id = UUID(str(row[0]))
        workspace_id = UUID(str(row[1]))
        try:
            outcome = await resolve_mention(pool, gateway, mention_id, workspace_id, ctx)
            if outcome in ("attached", "created"):
                count += 1
        except Exception:
            log.exception("identity.sweep_error", mention_id=str(mention_id))

    log.info("identity.sweep_done", org_id=str(org_id), resolved=count, total=len(rows))
    return count


# ---------------------------------------------------------------------------
# Worker job handles (wired by services/worker/src/worker/app.py)
# ---------------------------------------------------------------------------

# Assigned to the live Procrastinate task objects by build_app() in app.py.
# Until then they are None; m03_extraction already wraps calls in try/except
# (ImportError, AttributeError), so None.defer_async() is silently swallowed.

identity_resolve_job: object | None = None
identity_duplicate_sweep_job: object | None = None
