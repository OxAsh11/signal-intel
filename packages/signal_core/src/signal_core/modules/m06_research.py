"""M6 Research engine — AI document generation with source citations.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if none).
  2. Fetch account entity_id, org_id, and entity display_name.
  3. Reuse check: if an existing research_doc exists for this brief_version with no
     newer claims, return the existing doc id.
  4. Gather source material: the entity's live claims, their quotes and an excerpt of each
     source record's stored text. No sources → no document.
  5. Run AI (Capability.WRITE) with the research prompt.
  6. Parse [SOURCE:uuid] citations and keep those that match a source that was given.
  7. Persist via app.create_research_doc RPC.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

_SOURCE_RE = re.compile(r"\[SOURCE:([0-9a-fA-F\-]{36})\]")
_EXCERPT_CHARS = 1500
_MAX_SOURCE_CLAIMS = 60
# Used when the Brief has no research template (M1: "default applies").
DEFAULT_SECTIONS = ["Company overview", "Evidence for the hypothesis", "Why now", "Open questions"]


class ResearchSection(BaseModel):
    title: str
    content: str


class ResearchOutput(BaseModel):
    sections: list[ResearchSection]


async def generate_research_doc(
    pool: AsyncConnectionPool,  # AsyncConnectionPool
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Generate and persist a research doc for the account.

    Returns research_doc id string or None when already up to date or no brief_version.
    """
    # 1. Find latest published brief_version for this workspace
    async with pool.connection() as conn:
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
    async with pool.connection() as conn:
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
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT e.display_name FROM public.entity e WHERE e.id = %s",
                (str(entity_id),),
            )
            entity_row = await cur.fetchone()

    display_name: str = (entity_row[0] if entity_row else None) or "unknown"

    # 4. Reuse check — existing doc with no newer claims
    async with pool.connection() as conn:
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
        async with pool.connection() as conn:
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

    # 5. Source material: the entity's live claims with their quotes, grouped by the source
    #    record they came from, plus an excerpt of each record's stored text. A document is
    #    only written from stored sources (M6: the research document is never itself evidence).
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT sr.id, sr.url, left(sp.content, %s), p.key, c.value, c.quote
                FROM public.claim c
                JOIN public.predicate p ON p.id = c.predicate_id
                JOIN public.source_record sr ON sr.id = c.source_record_id
                LEFT JOIN public.source_payload sp ON sp.source_record_id = sr.id
                WHERE c.entity_id = %s AND c.status = 'active'
                  AND (c.expires_at IS NULL OR c.expires_at > now())
                ORDER BY c.observed_at DESC
                LIMIT %s
                """,
                (_EXCERPT_CHARS, str(entity_id), _MAX_SOURCE_CLAIMS),
            )
            source_rows = await cur.fetchall()

    if not source_rows:
        log.info("research.no_sources", account_id=str(account_id))
        return None

    by_source: dict[str, dict[str, Any]] = {}
    for sr_id, url, excerpt, pred_key, value, quote in source_rows:
        entry = by_source.setdefault(
            str(sr_id), {"url": url or "", "excerpt": excerpt or "", "facts": []}
        )
        fact = f"- {pred_key}: {value}"
        if quote:
            fact += f' — "{quote}"'
        entry["facts"].append(fact)

    sources_text = "\n\n".join(
        f"[SOURCE:{sr_id}] {entry['url']}\n"
        + "\n".join(entry["facts"])
        + (f"\nExcerpt: {entry['excerpt']}" if entry["excerpt"] else "")
        for sr_id, entry in by_source.items()
    )

    # Build sections JSON for the prompt
    sections_json = json.dumps(sections_config or DEFAULT_SECTIONS)

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

    # Keep only citations of sources that were actually given: every link must resolve to a
    # stored source record (M6 acceptance).
    unique_source_ids = [s for s in dict.fromkeys(all_source_ids) if s in by_source]

    # 9. Persist research doc via app.create_research_doc RPC
    content_json = json.dumps([s.model_dump() for s in research_output.sections])
    inputs_json = json.dumps(
        {
            "prompt_name": "research",
            "prompt_version": 1,
            "extractor_version_id": str(result.extractor_version_id),
        }
    )

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT app.create_research_doc(%s, %s, %s, %s, %s, %s, %s::jsonb, %s::uuid[], %s::jsonb)
                """,  # noqa: E501
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
