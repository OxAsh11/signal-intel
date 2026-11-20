"""M3 Extraction pipeline — relevance judge and structured extraction.

Phase 1d, Task 2.

Flow:
  1. judge_relevance: cheap PREJUDGE call to skip irrelevant source records.
  2. extract_structured: EXTRACT call; creates provisional entities, mentions, and
     claims in the DB; dispatches identity resolution (M4) per mention.

Both read the text stored with the source record (public.source_payload).
"""

from __future__ import annotations

import contextlib
import json
import math
import unicodedata
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, JsonValue, WithJsonSchema

import signal_core.modules.m05_index as _m05_index
from signal_core import briefs
from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.ai_gateway.registry import Predicate, validate_claim_value
from signal_core.brief_columns import column_specs, extract_keys
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Deferrable handle wired by worker/app.py (like m04_identity.identity_resolve_job).
# None until the worker app calls: m03_extraction.extract_relevance_judge_job = <task>
extract_relevance_judge_job: Any = None
# Wired the same way: the extract.backfill_record task queued by queue_predicate_backfill.
extract_backfill_record_job: Any = None

RELEVANCE_PROMPT_NAME = "relevance_judge"
RELEVANCE_PROMPT_VERSION = 2
NO_BRIEF_HYPOTHESIS = "Any organisation of commercial interest to the user."
EXTRACTION_PROMPT_NAME = "extraction"
EXTRACTION_PROMPT_VERSION = 2
BACKFILL_PROMPT_NAME = "extraction_backfill"
BACKFILL_PROMPT_VERSION = 1
# Records queued per backfill event; the rest are logged as extraction.backfill_capped.
BACKFILL_CAP = 500
# Records a new column of a Brief is backfilled over: extracted, and found by one of the Brief's
# radars or by no radar; newest first. Params: (workspace_id, brief_id, limit).
BACKFILL_SCOPE_SQL = """
    SELECT sr.id, sr.org_id
      FROM public.source_record sr
      LEFT JOIN public.radar r ON r.id = sr.radar_id
     WHERE sr.workspace_id = %s
       AND sr.extraction_status = 'extracted'
       AND (sr.radar_id IS NULL OR r.brief_id = %s)
     ORDER BY sr.created_at DESC, sr.id DESC
     LIMIT %s
"""
# Longest text sent to the model for one record (about 10k tokens).
MAX_CONTENT_CHARS = 40_000


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
    raw_attributes: dict[str, Any] = {}


# The schema the model is asked to follow stays strict; parsing accepts any JSON so that one
# malformed claim (null, a list, NaN, confidence 0.9 or 150) is dropped on its own in
# extract_structured instead of failing the whole record.
_ScalarValue = Annotated[
    JsonValue,
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string"},
                {"type": "integer"},
                {"type": "number"},
                {"type": "boolean"},
            ],
            "title": "Value",
        }
    ),
]
_Confidence = Annotated[JsonValue, WithJsonSchema({"title": "Confidence", "type": "integer"})]


class ClaimExtract(BaseModel):
    mention_index: int  # 0-based index into the mentions list this claim references
    predicate_key: str
    value: _ScalarValue  # a typed JSON scalar; checked against the predicate per claim
    quote: str | None = None
    quote_original: str | None = None
    quote_language: str | None = None
    confidence: _Confidence  # an integer 0-100; checked per claim by claim_confidence()


def claim_confidence(raw: object) -> int | None:
    """The confidence as an int when it is an integral number in 0-100, else None (never coerced:
    "80", true and 0.9 are rejected; 80.0 is the integer 80)."""
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    if isinstance(raw, float) and not (math.isfinite(raw) and raw.is_integer()):
        return None
    value = int(raw)
    return value if 0 <= value <= 100 else None


class ExtractionResult(BaseModel):
    mentions: list[MentionExtract]
    claims: list[ClaimExtract]
    source_date: str | None = None  # ISO date of the article or posting, else null


class BackfillResult(BaseModel):
    """extraction_backfill output: ExtractionResult's claims and source_date. The mentions are
    given to the model, so any it returns are ignored."""

    claims: list[ClaimExtract]
    source_date: str | None = None


def parse_source_date(value: str | None, fetched_at: datetime) -> datetime:
    """The ISO date at 00:00 UTC; *fetched_at* when missing, unparseable or in the future."""
    if not value:
        return fetched_at
    try:
        parsed = datetime.combine(date.fromisoformat(value.strip()), datetime.min.time(), UTC)
    except ValueError:
        return fetched_at
    return fetched_at if parsed > fetched_at else parsed


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _load_source_record(pool: AsyncConnectionPool, source_record_id: UUID) -> dict[str, Any]:
    """Return id, org_id, workspace_id, url, the stored text and fetched_at for a source record."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT sr.id, sr.org_id, sr.workspace_id, sr.url, sp.content, sr.fetched_at"
            " FROM public.source_record sr"
            " LEFT JOIN public.source_payload sp ON sp.source_record_id = sr.id"
            " WHERE sr.id = %s",
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
        "content": row[4],
        "fetched_at": row[5],
    }


def _content_text(source_record: dict[str, Any]) -> str:
    """The text to judge and extract from: the stored payload, headed by its URL."""
    url = source_record.get("url") or ""
    content = (source_record.get("content") or "").strip()
    if not content:
        return url
    text = f"Source: {url}\n\n{content}" if url else content
    return text[:MAX_CONTENT_CHARS]


def _fold(text: str) -> str:
    """NFKC forms folded, accents removed, case folded: "Rénovation" matches "renovation"."""
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def passes_lexicon(text: str, lexicon: list[str]) -> bool:
    """M2 pre-filter: True when any lexicon term occurs in *text*; an empty lexicon passes all."""
    terms = [_fold(t).strip() for t in lexicon if t and t.strip()]
    if not terms:
        return True
    folded = _fold(text)
    return any(term in folded for term in terms)


async def _radar_lexicon(pool: AsyncConnectionPool, source_record_id: UUID) -> list[str]:
    """The lexicon of the radar that found the record; empty when it has none or no radar."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT r.lexicon FROM public.radar r"
            " WHERE r.id = (SELECT radar_id FROM public.source_record WHERE id = %s)",
            (str(source_record_id),),
        )
        row = await cur.fetchone()
    return list(row[0] or []) if row else []


async def _radar_brief_config(
    pool: AsyncConnectionPool, source_record_id: UUID
) -> dict[str, Any] | None:
    """The current config of the Brief behind the record's radar; None without one."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT bv.config FROM public.source_record sr"
            " JOIN public.radar r ON r.id = sr.radar_id"
            " JOIN public.brief b ON b.id = r.brief_id"
            " JOIN public.brief_version bv ON bv.id = b.current_version_id"
            " WHERE sr.id = %s",
            (str(source_record_id),),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return config if isinstance(config, dict) else {}


def _brief_keys(config: dict[str, Any]) -> set[str] | None:
    """The keys of a Brief's extract columns; None for a legacy config (no column specs),
    which sends all. A formula column is never among them (P3, R1)."""
    return set(extract_keys(config)) if column_specs(config) else None


async def scope_predicate_keys(
    pool: AsyncConnectionPool, source_record_id: UUID, workspace_id: UUID
) -> set[str] | None:
    """The predicate keys extraction sends for a record; None means send every predicate.

    A record from a radar uses the columns of that radar's Brief (None when it is legacy). A
    record without one uses the union over the workspace's active Briefs, but only when every one
    of them has column specs: a legacy Brief in reach relies on every workspace and global
    predicate, so any legacy Brief (or no Brief at all) gives None.
    """
    config = await _radar_brief_config(pool, source_record_id)
    if config is not None:
        return _brief_keys(config)
    keys: set[str] = set()
    active = await briefs.active_brief_versions(pool, workspace_id)
    for brief in active:
        brief_keys = _brief_keys(brief.config)
        if brief_keys is None:
            return None
        keys |= brief_keys
    return keys if active else None


async def relevance_hypothesis(
    pool: AsyncConnectionPool, source_record_id: UUID, workspace_id: UUID
) -> str:
    """What the relevance judge tests the record against.

    The hypothesis of the Brief behind the record's radar; failing that the hypotheses of the
    workspace's active Briefs; failing that a neutral line.
    """
    config = await _radar_brief_config(pool, source_record_id)
    if config is not None:
        hypothesis = str(config.get("hypothesis") or "").strip()
        if hypothesis:
            return hypothesis
    active = [b.hypothesis.strip() for b in await briefs.active_brief_versions(pool, workspace_id)]
    active = [h for h in active if h]
    return "\n- ".join(active) if active else NO_BRIEF_HYPOTHESIS


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
    async with pool.connection() as conn, conn.cursor() as cur:
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

    source_record = await _load_source_record(pool, source_record_id)
    text = _content_text(source_record)

    # Cheap lexicon check before the paid judge (M2). Filtered records are marked, never dropped.
    if not passes_lexicon(text, await _radar_lexicon(pool, source_record_id)):
        await _update_status(pool, source_record_id, "irrelevant", "prefilter")
        log.info("extraction.prefiltered", source_record_id=str(source_record_id))
        return False

    hypothesis = await relevance_hypothesis(pool, source_record_id, source_record["workspace_id"])
    prompt = load_prompt(RELEVANCE_PROMPT_NAME, RELEVANCE_PROMPT_VERSION)
    try:
        result = await gateway.run(
            Capability.PREJUDGE,
            prompt,
            {"hypothesis": hypothesis, "content": text},
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


async def _extraction_status(pool: AsyncConnectionPool, source_record_id: UUID) -> str | None:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT extraction_status FROM public.source_record WHERE id = %s",
            (str(source_record_id),),
        )
        row = await cur.fetchone()
    return str(row[0]) if row is not None else None


async def _dispatch_identity(mention_ids: list[UUID], workspace_id: UUID) -> None:
    """Queue identity.resolve per mention; one queued job per mention however often it is called."""
    from procrastinate.exceptions import AlreadyEnqueued

    from signal_core.modules import m04_identity

    job = m04_identity.identity_resolve_job
    if job is None:
        for mid in mention_ids:
            log.warning("extraction.m4_not_wired", mention_id=str(mid))
        return
    for mid in mention_ids:
        with contextlib.suppress(AlreadyEnqueued):
            await job.configure(queueing_lock=f"identity:{mid}").defer_async(
                mention_id=str(mid),
                workspace_id=str(workspace_id),
            )


async def _defer_index(source_record_id: UUID) -> None:
    """Queue chunk embedding once per record (the queueing lock makes repeats a no-op)."""
    try:
        if _m05_index.index_chunk_embed_job is not None:
            from procrastinate.exceptions import AlreadyEnqueued

            with contextlib.suppress(AlreadyEnqueued):
                await _m05_index.index_chunk_embed_job.configure(
                    queueing_lock=f"index:{source_record_id}"
                ).defer_async(source_record_id=str(source_record_id))
    except (ImportError, AttributeError):
        pass


async def _resume_extracted(pool: AsyncConnectionPool, source_record_id: UUID) -> None:
    """A retry of a job whose transaction already committed: no AI call, no writes.

    Re-dispatch identity for the record's mentions that are still provisional (the crashed
    run may have died before dispatching them) and make sure indexing is queued.
    """
    source_record = await _load_source_record(pool, source_record_id)
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id FROM public.mention"
            " WHERE source_record_id = %s AND match_method = 'provisional'"
            " ORDER BY id",
            (str(source_record_id),),
        )
        rows = await cur.fetchall()
    pending = [UUID(str(row[0])) for row in rows]
    await _dispatch_identity(pending, source_record["workspace_id"])
    await _defer_index(source_record_id)
    log.info(
        "extraction.already_extracted",
        source_record_id=str(source_record_id),
        redispatched=len(pending),
    )


class _Predicates:
    """The predicates one extraction may use: rows of public.predicate, by key."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.ids: dict[str, UUID] = {row[1]: UUID(str(row[0])) for row in rows}
        self.specs: dict[str, Predicate] = {
            row[1]: Predicate(key=row[1], value_type=row[3], allowed_values=row[4]) for row in rows
        }

    def prompt_json(self) -> str:
        return json.dumps(
            [
                {
                    "key": row[1],
                    "label": row[2],
                    "value_type": row[3],
                    "allowed_values": row[4],
                    "hint": row[5] or "",
                }
                for row in self.rows
            ]
        )


async def _load_predicates(
    pool: AsyncConnectionPool, workspace_id: UUID, keys: set[str] | None
) -> _Predicates:
    """The workspace's and global extract predicates; only *keys* when given (None: all of them).

    Only fill_method 'extract' is ever loaded, so a formula column (or any column filled another
    way) never reaches an extraction prompt, scoped, legacy or backfill alike (P3, R1)."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, key, label, value_type, allowed_values, extraction_hint"
            " FROM public.predicate"
            " WHERE (workspace_id = %s OR workspace_id IS NULL) AND fill_method = 'extract'",
            (str(workspace_id),),
        )
        rows = list(await cur.fetchall())
    return _Predicates([r for r in rows if keys is None or r[1] in keys])


def _validate_claims(
    claims: list[ClaimExtract], predicates: _Predicates, source_record_id: UUID
) -> list[tuple[ClaimExtract, UUID, int]]:
    """(claim, predicate id, confidence) for each claim that is valid against its predicate.

    Invalid output is rejected, never coerced; one bad claim drops only itself."""
    valid: list[tuple[ClaimExtract, UUID, int]] = []
    for claim in claims:
        predicate = predicates.specs.get(claim.predicate_key)
        if predicate is None:
            log.warning(
                "extraction.unknown_predicate",
                predicate_key=claim.predicate_key,
                source_record_id=str(source_record_id),
            )
            continue
        error = validate_claim_value(predicate, claim.value)
        confidence = claim_confidence(claim.confidence)
        if error is None and confidence is None:
            error = f"confidence {claim.confidence!r} is not an integer from 0 to 100"
        if error is not None or confidence is None:
            log.warning(
                "extraction.claim_invalid",
                predicate_key=claim.predicate_key,
                error=error,
                source_record_id=str(source_record_id),
            )
            continue
        valid.append((claim, predicates.ids[claim.predicate_key], confidence))
    return valid


async def extract_structured(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    source_record_id: UUID,
    ctx: CallContext,
) -> ExtractionResult:
    """Full structured extraction: creates provisional entities, mentions, and claims.

    On AI error, persists extraction_status = 'failed' and re-raises.
    On success, persists extraction_status = 'extracted' in the same transaction as the writes,
    so a retried job (Procrastinate retry=2) sees 'extracted' and neither calls the AI nor
    writes again; it only re-dispatches identity for still-provisional mentions.
    Dispatches identity.resolve for each mention (M4; lazy import avoids circular dep).
    """
    if await _extraction_status(pool, source_record_id) == "extracted":
        await _resume_extracted(pool, source_record_id)
        return ExtractionResult(mentions=[], claims=[])

    prompt = load_prompt(EXTRACTION_PROMPT_NAME, EXTRACTION_PROMPT_VERSION)
    source_record = await _load_source_record(pool, source_record_id)
    text = _content_text(source_record)
    org_id: UUID = source_record["org_id"]
    workspace_id: UUID = source_record["workspace_id"]

    # Only the Brief's columns are sent (and accepted); legacy Briefs send every predicate.
    keys = await scope_predicate_keys(pool, source_record_id, workspace_id)
    predicates = await _load_predicates(pool, workspace_id, keys)

    try:
        result = await gateway.run(
            Capability.EXTRACT,
            prompt,
            {"content": text, "predicates": predicates.prompt_json()},
            ExtractionResult,
            ctx,
        )
    except (AiOutputInvalid, AiProviderError) as exc:
        await _update_status(pool, source_record_id, "failed", str(exc))
        raise

    extraction = result.value
    observed_at = parse_source_date(extraction.source_date, source_record["fetched_at"])
    valid_claims = _validate_claims(extraction.claims, predicates, source_record_id)

    mention_ids: list[UUID] = []
    try:
        # One transaction: a failure anywhere leaves no partial entities, mentions or claims,
        # and the 'extracted' status commits together with them.
        async with pool.connection() as conn, conn.transaction():
            entity_ids: list[UUID] = []
            for mention in extraction.mentions:
                # Provisional entity: M4 identity resolution will resolve/merge this later.
                cur = await conn.execute(
                    "SELECT app.create_entity(%s, %s::public.entity_type, %s)",
                    (str(org_id), mention.entity_type, mention.raw_name or "unknown"),
                )
                ent_row = await cur.fetchone()
                assert ent_row is not None
                provisional_entity_id = UUID(str(ent_row[0]))
                entity_ids.append(provisional_entity_id)

                cur = await conn.execute(
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
                assert men_row is not None
                mention_id = UUID(str(men_row[0]))
                mention_ids.append(mention_id)

                # Link provisional entity to mention so M4 can reuse it instead of creating a second one.  # noqa: E501
                await conn.execute(
                    "SELECT app.resolve_mention(%s, %s, %s, %s::smallint)",
                    (str(mention_id), str(provisional_entity_id), "provisional", 0),
                )

            for claim, predicate_id, confidence in valid_claims:
                if claim.mention_index < 0 or claim.mention_index >= len(entity_ids):
                    log.warning(
                        "extraction.claim_index_oob",
                        mention_index=claim.mention_index,
                        mentions=len(entity_ids),
                        source_record_id=str(source_record_id),
                    )
                    continue
                await conn.execute(
                    "SELECT app.create_claim(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        str(org_id),
                        str(entity_ids[claim.mention_index]),
                        str(predicate_id),
                        json.dumps(claim.value, allow_nan=False),
                        claim.quote,
                        claim.quote_original,
                        claim.quote_language,
                        str(source_record_id),
                        observed_at,
                        str(result.extractor_version_id),
                        confidence,
                    ),
                )

            # ── Mark done, atomically with the writes above ──────────────────────
            await conn.execute(
                "SELECT app.update_extraction_status(%s, %s, %s)",
                (str(source_record_id), "extracted", None),
            )
    except Exception as exc:
        # The transaction has rolled back; record the failure on a fresh connection.
        await _update_status(pool, source_record_id, "failed", str(exc))
        raise

    # ── Dispatch identity resolution — after all claims are committed ────────
    # Deferring here (not inside the mention loop) guarantees claims exist when
    # verdict.compute runs: identity and derive queues are concurrent, and an
    # early dispatch would let compute_verdict see zero claims and write Unverified.
    # If this process dies before or during dispatch, the retried job finds the record
    # 'extracted' and re-dispatches the mentions that are still provisional.
    await _dispatch_identity(mention_ids, workspace_id)
    await _defer_index(source_record_id)

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
    async with pool.connection() as conn, conn.cursor() as cur:
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


# ---------------------------------------------------------------------------
# Column backfill (P2): a new Brief version adds columns; fill them over extracted records
# ---------------------------------------------------------------------------


async def queue_predicate_backfill(
    pool: AsyncConnectionPool, brief_id: UUID, workspace_id: UUID, keys: list[str]
) -> int:
    """Queue extract.backfill_record for each record in the Brief's backfill scope.

    Newest first, at most BACKFILL_CAP per call; one queued job per record and key set however
    often the event is delivered. Returns the number of records queued.
    """
    if not keys:
        return 0
    job = extract_backfill_record_job
    if job is None:
        log.warning("extraction.backfill_not_wired", brief_id=str(brief_id))
        return 0
    # Only extract columns are backfilled; a version adding only formula columns queues nothing
    # (they are filled by their own runs, never by extraction).
    sorted_keys = sorted((await _load_predicates(pool, workspace_id, set(keys))).ids)
    if not sorted_keys:
        log.info(
            "extraction.backfill_no_extract_keys",
            brief_id=str(brief_id),
            predicate_keys=sorted(set(keys)),
        )
        return 0
    cap = BACKFILL_CAP
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(BACKFILL_SCOPE_SQL, (str(workspace_id), str(brief_id), cap + 1))
        rows = list(await cur.fetchall())
    if len(rows) > cap:
        log.warning(
            "extraction.backfill_capped",
            brief_id=str(brief_id),
            workspace_id=str(workspace_id),
            predicate_keys=sorted_keys,
            cap=cap,
        )
        rows = rows[:cap]

    from procrastinate.exceptions import AlreadyEnqueued

    joined = ",".join(sorted_keys)
    for record_id, org_id in rows:
        with contextlib.suppress(AlreadyEnqueued):
            await job.configure(queueing_lock=f"backfill:{record_id}:{joined}").defer_async(
                source_record_id=str(record_id),
                predicate_keys=sorted_keys,
                org_id=str(org_id),
                workspace_id=str(workspace_id),
                brief_id=str(brief_id),
            )
    log.info(
        "extraction.backfill_queued",
        brief_id=str(brief_id),
        workspace_id=str(workspace_id),
        predicate_keys=sorted_keys,
        queued=len(rows),
    )
    return len(rows)


async def _live_mentions(
    pool: AsyncConnectionPool, source_record_id: UUID
) -> list[tuple[UUID, str, str]]:
    """(entity id, entity type, name) of the record's mentions that point at a live entity.

    A mention never resolved (no entity) or whose entity was merged away is skipped."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT m.entity_id, m.entity_type::text, coalesce(m.raw_name, e.display_name),"
            " e.status"
            " FROM public.mention m LEFT JOIN public.entity e ON e.id = m.entity_id"
            " WHERE m.source_record_id = %s ORDER BY m.created_at, m.id",
            (str(source_record_id),),
        )
        rows = await cur.fetchall()
    return [
        (UUID(str(entity_id)), str(entity_type), str(name or ""))
        for entity_id, entity_type, name, status in rows
        if entity_id is not None and status != "merged"
    ]


async def backfill_record(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    source_record_id: UUID,
    keys: list[str],
    ctx: CallContext,
) -> int:
    """Extract *keys* from an already extracted record onto its existing mentions' entities.

    Creates no entity or mention and leaves the extraction status alone; the claims are written
    in one transaction, and their claim.created events re-judge the verdicts. app.create_claim
    dedupes, so a rerun adds no rows. Returns the number of valid claims sent to
    app.create_claim, deduplicated ones included (not the number of new rows).
    """
    mentions = await _live_mentions(pool, source_record_id)
    if not mentions:
        log.info("extraction.backfill_no_mentions", source_record_id=str(source_record_id))
        return 0
    source_record = await _load_source_record(pool, source_record_id)
    predicates = await _load_predicates(pool, source_record["workspace_id"], set(keys))
    if not predicates.rows:
        log.warning(
            "extraction.backfill_no_predicates",
            source_record_id=str(source_record_id),
            predicate_keys=keys,
        )
        return 0

    result = await gateway.run(
        Capability.EXTRACT,
        load_prompt(BACKFILL_PROMPT_NAME, BACKFILL_PROMPT_VERSION),
        {
            "mentions": json.dumps(
                [
                    {"index": i, "entity_type": entity_type, "name": name}
                    for i, (_, entity_type, name) in enumerate(mentions)
                ]
            ),
            "predicates": predicates.prompt_json(),
            "content": _content_text(source_record),
        },
        BackfillResult,
        ctx,
    )
    output = result.value
    observed_at = parse_source_date(output.source_date, source_record["fetched_at"])
    valid_claims = _validate_claims(output.claims, predicates, source_record_id)
    written = 0
    async with pool.connection() as conn, conn.transaction():
        for claim, predicate_id, confidence in valid_claims:
            if not 0 <= claim.mention_index < len(mentions):
                log.warning(
                    "extraction.claim_index_oob",
                    mention_index=claim.mention_index,
                    mentions=len(mentions),
                    source_record_id=str(source_record_id),
                )
                continue
            await conn.execute(
                "SELECT app.create_claim(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(source_record["org_id"]),
                    str(mentions[claim.mention_index][0]),
                    str(predicate_id),
                    json.dumps(claim.value, allow_nan=False),
                    claim.quote,
                    claim.quote_original,
                    claim.quote_language,
                    str(source_record_id),
                    observed_at,
                    str(result.extractor_version_id),
                    confidence,
                ),
            )
            written += 1
    log.info(
        "extraction.backfill_done",
        source_record_id=str(source_record_id),
        predicate_keys=keys,
        claims=written,
    )
    return written
