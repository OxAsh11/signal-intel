"""M3 Extraction pipeline — relevance judge and structured extraction.

Phase 1d, Task 2.

Flow:
  1. judge_relevance: cheap PREJUDGE call to skip irrelevant source records.
  2. extract_structured: EXTRACT call; creates provisional entities, mentions, and
     claims in the DB; dispatches identity resolution (M4) per mention.

Each function reads the source record payload from source_record.url (Phase 2 will
swap this for real object-storage retrieval once payload_ref is fully wired).
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

RELEVANCE_PROMPT_NAME = "relevance_judge"
RELEVANCE_PROMPT_VERSION = 1
EXTRACTION_PROMPT_NAME = "extraction"
EXTRACTION_PROMPT_VERSION = 1


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class RelevanceJudgement(BaseModel):
    relevant: bool
    reason: str


class MentionExtract(BaseModel):
    entity_type: str  # 'company' | 'person'
    raw_name: str | None = None
    raw_domain: str | None = None
    raw_country: str | None = None
    raw_attributes: dict = {}


class ClaimExtract(BaseModel):
    mention_index: int  # 0-based index into the mentions list this claim references
    predicate_key: str
    value: str  # JSON string
    quote: str | None = None
    quote_original: str | None = None
    quote_language: str | None = None
    confidence: int  # 0-100


class ExtractionResult(BaseModel):
    mentions: list[MentionExtract]
    claims: list[ClaimExtract]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _load_source_record(pool: AsyncConnectionPool, source_record_id: UUID) -> dict:
    """Return id, org_id, workspace_id, url, payload_ref for the given source record."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, workspace_id, url, payload_ref"
                " FROM public.source_record WHERE id = %s",
                (str(source_record_id),),
            )
            row = await cur.fetchone()
    if row is None:
        raise ValueError(f"source_record {source_record_id} not found")
    return {
        "id": UUID(str(row[0])),
        "org_id": UUID(str(row[1])),
        "workspace_id": UUID(str(row[2])),
        "url": row[3],
        "payload_ref": row[4],
    }


def _content_text(source_record: dict) -> str:
    """Return the text to judge/extract.

    Phase 2 will add real object-storage retrieval via payload_ref.
    For now we use source_record.url as a stable text proxy.
    """
    payload_ref: str = source_record.get("payload_ref") or ""
    # A storage path (not an http URL) is reserved for Phase 2 integration.
    if payload_ref and not payload_ref.startswith("http"):
        log.debug("extraction.payload_ref_not_resolved", payload_ref=payload_ref)
    return source_record.get("url") or ""


async def _update_status(
    pool: AsyncConnectionPool,
    source_record_id: UUID,
    status: str,
    detail: str | None = None,
) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "SELECT app.update_extraction_status(%s, %s, %s)",
            (str(source_record_id), status, detail),
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def judge_relevance(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    source_record_id: UUID,
    ctx: CallContext,
) -> bool:
    """Cheap first-pass: return True if the source record is worth extracting.

    On False, persists extraction_status = 'irrelevant'.
    On AI error, persists extraction_status = 'failed' and re-raises.
    """
    prompt = load_prompt(RELEVANCE_PROMPT_NAME, RELEVANCE_PROMPT_VERSION)
    source_record = await _load_source_record(pool, source_record_id)
    text = _content_text(source_record)

    try:
        result = await gateway.run(
            Capability.PREJUDGE,
            prompt,
            {"content": text},
            RelevanceJudgement,
            ctx,
        )
    except (AiOutputInvalid, AiProviderError) as exc:
        await _update_status(pool, source_record_id, "failed", str(exc))
        raise

    if not result.value.relevant:
        await _update_status(pool, source_record_id, "irrelevant", result.value.reason)
        log.info(
            "extraction.irrelevant",
            source_record_id=str(source_record_id),
            reason=result.value.reason,
        )
        return False

    return True


async def extract_structured(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    source_record_id: UUID,
    ctx: CallContext,
) -> ExtractionResult:
    """Full structured extraction: creates provisional entities, mentions, and claims.

    On AI error, persists extraction_status = 'failed' and re-raises.
    On success, persists extraction_status = 'extracted'.
    Dispatches identity.resolve for each mention (M4; lazy import avoids circular dep).
    """
    prompt = load_prompt(EXTRACTION_PROMPT_NAME, EXTRACTION_PROMPT_VERSION)
    source_record = await _load_source_record(pool, source_record_id)
    text = _content_text(source_record)
    org_id: UUID = source_record["org_id"]
    workspace_id: UUID = source_record["workspace_id"]

    # Load workspace predicates for prompt injection and claim linkage.
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, key, extraction_hint"
                " FROM public.predicate WHERE workspace_id = %s",
                (str(workspace_id),),
            )
            pred_rows = await cur.fetchall()

    predicates_list = [{"key": row[1], "hint": row[2] or ""} for row in pred_rows]
    pred_by_key: dict[str, UUID] = {row[1]: UUID(str(row[0])) for row in pred_rows}
    predicates_text = json.dumps(predicates_list)

    try:
        result = await gateway.run(
            Capability.EXTRACT,
            prompt,
            {"content": text, "predicates": predicates_text},
            ExtractionResult,
            ctx,
        )
    except (AiOutputInvalid, AiProviderError) as exc:
        await _update_status(pool, source_record_id, "failed", str(exc))
        raise

    extraction = result.value
    entity_ids: list[UUID] = []
    mention_ids: list[UUID] = []

    # ── Create provisional entities and mentions ──────────────────────────────
    for mention in extraction.mentions:
        # Provisional entity: M4 identity resolution will resolve/merge this later.
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT app.create_entity(%s, %s::public.entity_type, %s)",
                    (str(org_id), mention.entity_type, mention.raw_name or "unknown"),
                )
                ent_row = await cur.fetchone()
        entity_ids.append(UUID(str(ent_row[0])))

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT app.create_mention(%s, %s, %s::public.entity_type,"
                    " %s, %s, %s, %s::jsonb)",
                    (
                        str(org_id),
                        str(source_record_id),
                        mention.entity_type,
                        mention.raw_name,
                        mention.raw_domain,
                        mention.raw_country,
                        json.dumps(mention.raw_attributes),
                    ),
                )
                men_row = await cur.fetchone()
        mention_id = UUID(str(men_row[0]))
        mention_ids.append(mention_id)

        # Dispatch identity resolution (lazy import avoids circular dep; M4 may not exist yet).
        try:
            from signal_core.modules import m04_identity  # noqa: PLC0415

            await m04_identity.identity_resolve_job.defer_async(
                mention_id=str(mention_id),
                workspace_id=str(workspace_id),
            )
        except (ImportError, AttributeError):
            # M4 not yet deployed — identity dispatch skipped.
            pass

    # ── Create claims ─────────────────────────────────────────────────────────
    for claim in extraction.claims:
        if claim.mention_index >= len(entity_ids):
            log.warning(
                "extraction.claim_index_oob",
                mention_index=claim.mention_index,
                mentions=len(entity_ids),
                source_record_id=str(source_record_id),
            )
            continue
        predicate_id = pred_by_key.get(claim.predicate_key)
        if predicate_id is None:
            log.warning(
                "extraction.unknown_predicate",
                predicate_key=claim.predicate_key,
                source_record_id=str(source_record_id),
            )
            continue
        entity_id = entity_ids[claim.mention_index]
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.create_claim(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, now(), %s, %s)",
                (
                    str(org_id),
                    str(entity_id),
                    str(predicate_id),
                    claim.value,
                    claim.quote,
                    claim.quote_original,
                    claim.quote_language,
                    str(source_record_id),
                    str(result.extractor_version_id),
                    claim.confidence,
                ),
            )

    # ── Mark done ─────────────────────────────────────────────────────────────
    await _update_status(pool, source_record_id, "extracted")

    log.info(
        "extraction.done",
        source_record_id=str(source_record_id),
        mentions=len(extraction.mentions),
        claims=len(extraction.claims),
    )
    return extraction
