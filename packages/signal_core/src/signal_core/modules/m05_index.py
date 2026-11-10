"""S9 Indexer — chunk and embed source records into memory_chunk."""

from __future__ import annotations

from uuid import UUID

from signal_core.ai_gateway.errors import AiNotConfigured
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability
from signal_core.derivation.writer import upsert_memory_chunk
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

CHUNK_SIZE = 1500
CHUNK_STEP = 1300  # 200-char overlap

# Module-level handle wired by build_app()
index_chunk_embed_job: object | None = None


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of CHUNK_SIZE with CHUNK_STEP stride."""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_STEP
    return chunks


async def index_source_record(
    pool: object,
    gateway: AiGateway,
    source_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> int:
    """Fetch, chunk, embed and persist a source_record as memory_chunks.

    Returns the number of chunks written, or 0 if no content or embedder not configured.
    """
    # 1. Fetch source record from DB
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT org_id, url, scope, created_at"
                " FROM public.source_record WHERE id = %s",
                (str(source_id),),
            )
            row = await cur.fetchone()

    # 2. Early return if not found
    if row is None:
        log.info("index.source_record_not_found", source_id=str(source_id))
        return 0

    org_id = row[0]
    content = row[1] or ""  # url as Phase 1 text proxy (matches m03._content_text)
    scope = row[2]
    occurred_at = row[3]
    entity_id = None  # source_record has no entity_id
    brief_id = None  # source_record has no brief_id

    # 3. Early return if content is empty
    if not content:
        log.info("index.source_record_no_content", source_id=str(source_id))
        return 0

    # 4. Get embedding model name (sync call before async embed)
    model_name = gateway.model_for(Capability.EMBED)

    # 5. Split content into chunks
    chunks = chunk_text(content)
    if not chunks:
        return 0

    # 6. Get embeddings (may raise AiNotConfigured)
    try:
        embeddings = await gateway.embed(chunks, ctx, input_type="document")
    except AiNotConfigured:
        log.info(
            "index.embedder_not_configured",
            source_id=str(source_id),
        )
        return 0

    # 7. Persist each chunk
    count = 0
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        await upsert_memory_chunk(
            pool,  # type: ignore[arg-type]
            org_id,
            workspace_id,
            entity_id,
            brief_id,
            "source_record",
            source_id,
            i,
            chunk,
            embedding,
            model_name,
            scope,
            occurred_at,
        )
        count += 1

    return count
