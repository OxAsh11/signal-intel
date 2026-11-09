"""M5 Verdict computation — per entity per brief_version.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if none).
  2. Fetch entity display_name + all active claims linked to the entity in the workspace.
  3. If no claims → Unverified without AI call.
  4. Otherwise run AI (Capability.JUDGE) with the verdict prompt.
  5. Write verdict via app.upsert_verdict (through derivation writer).
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.derivation.writer import upsert_verdict
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Module-level handle wired by build_app()
verdict_compute_job: object | None = None


class VerdictOutput(BaseModel):
    value: Literal["Confirmed", "Likely", "Unverified", "Contradicted"]
    reasoning: str
    supporting_claim_indices: list[int]


async def compute_verdict(
    pool: object,
    gateway: AiGateway,
    entity_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Compute and persist a verdict for the given entity in the workspace.

    Returns the verdict value string, or None if no published brief_version is found.
    """
    # 1. Find latest published brief_version for this workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT bv.id, b.draft_config->>'hypothesis'
                FROM public.brief_version bv
                JOIN public.brief b ON b.id = bv.brief_id
                WHERE bv.workspace_id = %s
                ORDER BY bv.created_at DESC
                LIMIT 1
                """,
                (str(workspace_id),),
            )
            row = await cur.fetchone()

    if row is None:
        log.info("verdict.no_brief_version", workspace_id=str(workspace_id))
        return None

    brief_version_id = UUID(str(row[0]))
    hypothesis: str = row[1] or ""

    # 2. Fetch entity display_name
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT display_name FROM public.entity WHERE id = %s",
                (str(entity_id),),
            )
            entity_row = await cur.fetchone()

    display_name: str = (entity_row[0] if entity_row else None) or "unknown"

    # 3. Fetch active claims for this entity in the workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT c.id, c.predicate_key, c.value, c.quote, c.confidence
                FROM public.claim c
                JOIN public.mention m ON m.id = c.mention_id
                WHERE m.entity_id = %s AND c.workspace_id = %s AND c.status = 'active'
                """,
                (str(entity_id), str(workspace_id)),
            )
            claims = await cur.fetchall()

    # 4. No claims → Unverified without AI call
    if len(claims) == 0:
        await upsert_verdict(
            pool,  # type: ignore[arg-type]
            brief_version_id,
            entity_id,
            "Unverified",
            [],
            [],
            {"reason": "no_claims"},
        )
        return "Unverified"

    # 5. Build claims_text
    claims_text = "\n".join(
        f"[{i}] {c[1]}: {c[2]} (confidence: {c[4]}) | quote: {c[3] or 'N/A'}"
        for i, c in enumerate(claims)
    )

    # 6. Load verdict prompt
    prompt = load_prompt("verdict", 1)

    # 7. Call AI gateway
    try:
        result = await gateway.run(
            Capability.JUDGE,
            prompt,
            {
                "hypothesis": hypothesis,
                "entity_name": display_name,
                "claims_text": claims_text,
            },
            VerdictOutput,
            ctx,
        )
    except (AiOutputInvalid, AiProviderError) as exc:
        log.warning(
            "verdict.ai_error",
            entity_id=str(entity_id),
            workspace_id=str(workspace_id),
            error=str(exc),
        )
        await upsert_verdict(
            pool,  # type: ignore[arg-type]
            brief_version_id,
            entity_id,
            "Unverified",
            [],
            [],
            {"reason": "ai_error", "error": str(exc)},
        )
        return "Unverified"

    # 8. Map supporting_claim_indices to claim_ids
    supporting_claim_rows = [
        claims[i] for i in result.value.supporting_claim_indices if 0 <= i < len(claims)
    ]

    # 9. Downgrade Confirmed → Likely if no supporting claims (guards against prompt bug)
    final_value: str = result.value.value
    if final_value == "Confirmed" and len(supporting_claim_rows) == 0:
        final_value = "Likely"

    # 10. Build quotes list from supporting claims with non-null quote
    quotes = [
        {"claim_id": str(claims[i]["id"] if hasattr(claims[i], "__getitem__") else claims[i][0]),
         "quote": claims[i][3]}
        for i in result.value.supporting_claim_indices
        if 0 <= i < len(claims) and claims[i][3]
    ]

    # 11. Build inputs dict
    inputs = {
        "prompt_name": "verdict",
        "prompt_version": 1,
        "extractor_version_id": str(result.extractor_version_id),
    }

    # 12. Persist verdict
    claim_ids = [UUID(str(c[0])) for c in supporting_claim_rows]
    await upsert_verdict(
        pool,  # type: ignore[arg-type]
        brief_version_id,
        entity_id,
        final_value,
        claim_ids,
        quotes,
        inputs,
    )

    return final_value
