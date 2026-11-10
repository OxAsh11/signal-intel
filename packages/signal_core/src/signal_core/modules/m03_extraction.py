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
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, field_validator
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext
import signal_core.modules.m05_index as _m05_index

log = get_logger(__name__)

# Deferrable handle wired by worker/app.py (like m04_identity.identity_resolve_job).
# None until the worker app calls: m03_extraction.extract_relevance_judge_job = <task>
extract_relevance_judge_job: Any = None

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
    entity_type: Literal["company", "person"]
    raw_name: str | None = None
    raw_domain: str | None = None
    raw_country: str | None = None
    raw_attributes: dict = {}


class ClaimExtract(BaseModel):
    mention_index: int  # 0-based index into the mentions list this claim references
    predicate_key: str
    value: str  # JSON string — validated below
    quote: str | None = None
    quote_original: str | None = None
    quote_language: str | None = None
    confidence: int  # 0-100

    @field_validator("value")
    @classmethod
    def value_must_be_json(cls, v: str) -> str:
        try:
            json.loads(v)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"claim.value must be valid JSON: {exc}") from exc
        return v


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
    # Gate: skip if already processed or missing.
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT extraction_status FROM public.source_record WHERE id = %s",
                (str(source_record_id),),
            )
            sr_row = await cur.fetchone()
    if sr_row is None or sr_row[0] != "pending":
        log.info(
            "extraction.skipped_not_pending",
            source_record_id=str(source_record_id),
            status=sr_row[0] if sr_row else "missing",
        )
        return False

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
                " FROM public.predicate WHERE workspace_id = %s OR workspace_id IS NULL",
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
        try:
            # Provisional entity: M4 identity resolution will resolve/merge this later.
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT app.create_entity(%s, %s::public.entity_type, %s)",
                        (str(org_id), mention.entity_type, mention.raw_name or "unknown"),
                    )
                    ent_row = await cur.fetchone()
            provisional_entity_id = UUID(str(ent_row[0]))
            entity_ids.append(provisional_entity_id)

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

            # Link provisional entity to mention so M4 can reuse it instead of creating a second one.
            async with pool.connection() as conn:
                await conn.execute(
                    "SELECT app.resolve_mention(%s, %s, %s, %s::smallint)",
                    (str(mention_id), str(provisional_entity_id), "provisional", 0),
                )

        except Exception as exc:
            await _update_status(pool, source_record_id, "failed", str(exc))
            raise

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
        try:
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
        except Exception as exc:
            await _update_status(pool, source_record_id, "failed", str(exc))
            raise

    # ── Dispatch identity resolution — after all claims are written ──────────
    # Deferring here (not inside the mention loop) guarantees claims exist when
    # verdict.compute runs: identity and derive queues are concurrent, and an
    # early dispatch would let compute_verdict see zero claims and write Unverified.
    from signal_core.modules import m04_identity  # noqa: PLC0415

    if m04_identity.identity_resolve_job is not None:
        for mid in mention_ids:
            await m04_identity.identity_resolve_job.defer_async(
                mention_id=str(mid),
                workspace_id=str(workspace_id),
            )
    else:
        for mid in mention_ids:
            log.warning("extraction.m4_not_wired", mention_id=str(mid))

    # ── Mark done ─────────────────────────────────────────────────────────────
    await _update_status(pool, source_record_id, "extracted")

    # Defer indexing — silently skip if job handle not yet wired
    try:
        if _m05_index.index_chunk_embed_job is not None:
            import contextlib
            from procrastinate.exceptions import AlreadyEnqueued
            with contextlib.suppress(AlreadyEnqueued):
                await _m05_index.index_chunk_embed_job.configure(
                    queueing_lock=f"index:{source_record_id}"
                ).defer_async(source_record_id=str(source_record_id))
    except (ImportError, AttributeError):
        pass

    log.info(
        "extraction.done",
        source_record_id=str(source_record_id),
        mentions=len(extraction.mentions),
        claims=len(extraction.claims),
    )
    return extraction


async def extract_backfill(
    pool: AsyncConnectionPool,
    brief_id: UUID,
    workspace_id: UUID,
) -> int:
    """Re-queue all pending source_records for relevance judging (up to 500).

    Called when a brief version is published so that any source_records already
    ingested but not yet processed are picked up under the new brief context.

    Returns the number of records queued.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM public.source_record"
                " WHERE workspace_id = %s AND extraction_status = 'pending'"
                " ORDER BY created_at LIMIT 500",
                (str(workspace_id),),
            )
            rows = await cur.fetchall()

    for (sr_id,) in rows:
        await extract_relevance_judge_job.defer_async(source_record_id=str(sr_id))

    log.info(
        "extraction.backfill",
        brief_id=str(brief_id),
        workspace_id=str(workspace_id),
        queued=len(rows),
    )
    return len(rows)
