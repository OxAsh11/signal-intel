"""M6 Research engine — AI document generation with source citations.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if none).
  2. Fetch account entity_id, org_id, and entity display_name.
  3. Reuse check: if an existing research_doc exists for this brief_version with no
     newer claims, return the existing doc id.
  4. Fetch source material for the org.
  5. Run AI (Capability.WRITE) with the research prompt.
  6. Parse [SOURCE:uuid] patterns; write-back claims via app.create_claim.
  7. Persist via app.create_research_doc RPC.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

_SOURCE_RE = re.compile(r"\[SOURCE:([0-9a-fA-F\-]{36})\]")


class ResearchSection(BaseModel):
    title: str
    content: str


class ResearchOutput(BaseModel):
    sections: list[ResearchSection]


async def generate_research_doc(
    pool: object,  # AsyncConnectionPool
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Generate and persist a research doc for the account.

    Returns research_doc id string or None when already up to date or no brief_version.
    """
    # 1. Find latest published brief_version for this workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT bv.id, bv.org_id, bv.config->>'hypothesis', bv.config->'sections'
                FROM public.brief_version bv
                WHERE bv.workspace_id = %s
                ORDER BY bv.published_at DESC
                LIMIT 1
                """,
                (str(workspace_id),),
            )
            bv_row = await cur.fetchone()

    if bv_row is None:
        log.info("research.no_brief_version", workspace_id=str(workspace_id))
        return None

    brief_version_id = UUID(str(bv_row[0]))
    org_id = UUID(str(bv_row[1]))
    hypothesis: str = bv_row[2] or ""
    sections_config = bv_row[3]

    # 2. Fetch account entity_id and org_id
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT a.entity_id, a.org_id FROM public.account a
                WHERE a.id = %s AND a.workspace_id = %s
                """,
                (str(account_id), str(workspace_id)),
            )
            account_row = await cur.fetchone()

    if account_row is None:
        log.warning(
            "research.account_not_found",
            account_id=str(account_id),
            workspace_id=str(workspace_id),
        )
        return None

    entity_id = UUID(str(account_row[0]))
    org_id = UUID(str(account_row[1]))

    # 3. Fetch entity display_name
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT e.display_name FROM public.entity e WHERE e.id = %s",
                (str(entity_id),),
            )
            entity_row = await cur.fetchone()

    display_name: str = (entity_row[0] if entity_row else None) or "unknown"

    # 4. Reuse check — existing doc with no newer claims
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT rd.id, rd.published_at FROM public.research_doc rd
                WHERE rd.account_id = %s AND rd.brief_version_id = %s
                ORDER BY rd.published_at DESC LIMIT 1
                """,
                (str(account_id), str(brief_version_id)),
            )
            existing_doc = await cur.fetchone()

    if existing_doc is not None:
        existing_doc_id = str(existing_doc[0])
        existing_published_at = existing_doc[1]

        # Check if any claims have been created after the doc was published
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT MAX(c.created_at) FROM public.claim c WHERE c.entity_id = %s",
                    (str(entity_id),),
                )
                claims_row = await cur.fetchone()

        latest_claim_at = claims_row[0] if claims_row else None
        if latest_claim_at is None or latest_claim_at <= existing_published_at:
            log.info(
                "research.reuse_existing_doc",
                account_id=str(account_id),
                research_doc_id=existing_doc_id,
            )
            return existing_doc_id

    # 5. Source material — deferred to Phase 3 (requires memory_chunk / storage integration)
    sources_text = "(no source material available)"

    # Build sections JSON for the prompt
    sections_json = json.dumps(sections_config) if sections_config else "[]"

    # 6. Load research prompt
    prompt = load_prompt("research", 1)

    # 7. Call AI gateway
    result = await gateway.run(
        Capability.WRITE,
        prompt,
        {
            "entity_name": display_name,
            "hypothesis": hypothesis,
            "sections_json": sections_json,
            "sources": sources_text,
        },
        ResearchOutput,
        ctx,
    )

    research_output: ResearchOutput = result.value

    # 8. Parse source citations — claim write-back deferred to Phase 3
    all_source_ids: list[str] = []
    for section in research_output.sections:
        all_source_ids.extend(_SOURCE_RE.findall(section.content))

    # Deduplicate source ids
    unique_source_ids = list(dict.fromkeys(all_source_ids))

    # 9. Persist research doc via app.create_research_doc RPC
    content_json = json.dumps([s.model_dump() for s in research_output.sections])
    inputs_json = json.dumps(
        {
            "prompt_name": "research",
            "prompt_version": 1,
            "extractor_version_id": str(result.extractor_version_id),
        }
    )

    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT app.create_research_doc(%s, %s, %s, %s, %s, %s, %s::jsonb, %s::uuid[], %s::jsonb)
                """,
                (
                    str(org_id),
                    str(workspace_id),
                    str(account_id),
                    str(brief_version_id),
                    str(entity_id),
                    1,
                    content_json,
                    unique_source_ids,
                    inputs_json,
                ),
            )
            doc_row = await cur.fetchone()

    doc_id = str(doc_row[0]) if doc_row else None
    log.info(
        "research.doc_created",
        account_id=str(account_id),
        research_doc_id=doc_id,
    )
    return doc_id
