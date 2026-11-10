"""M15 Account card generation — per account per brief_version.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if none).
  2. Fetch account entity_id (early-return if account not found).
  3. Fetch entity display_name.
  4. Early-return check: if an existing account_card is more recent than the latest verdict,
     return the existing card id.
  5. Fetch latest verdict, score, stage, claims, and contact coverage.
  6. Run AI (Capability.WRITE) with the account_card prompt.
  7. Persist via app.upsert_account_card RPC.
"""

from __future__ import annotations

import json
from uuid import UUID

from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Module-level handle wired by build_app()
account_card_generate_job: object | None = None


class AccountCardOutput(BaseModel):
    bullets: list[str]
    next_step: str


async def generate_account_card(
    pool: object,
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Generate and persist an account card.

    Returns account_card id string or None when no brief_version.
    """
    # 1. Find latest published brief_version for this workspace
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT bv.id, bv.org_id, bv.config->>'hypothesis'
                FROM public.brief_version bv WHERE bv.workspace_id = %s
                ORDER BY bv.published_at DESC LIMIT 1
                """,
                (str(workspace_id),),
            )
            bv_row = await cur.fetchone()

    if bv_row is None:
        log.info("account_card.no_brief_version", workspace_id=str(workspace_id))
        return None

    brief_version_id = UUID(str(bv_row[0]))
    hypothesis: str = bv_row[2] or ""

    # 2. Fetch account entity_id
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.entity_id FROM public.account a WHERE a.id = %s AND a.workspace_id = %s",
                (str(account_id), str(workspace_id)),
            )
            account_row = await cur.fetchone()

    if account_row is None:
        log.warning(
            "account_card.account_not_found",
            account_id=str(account_id),
            workspace_id=str(workspace_id),
        )
        return None

    entity_id = UUID(str(account_row[0]))

    # 3. Fetch entity display_name
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT e.display_name FROM public.entity e WHERE e.id = %s",
                (str(entity_id),),
            )
            entity_row = await cur.fetchone()

    display_name: str = (entity_row[0] if entity_row else None) or "unknown"

    # 4. Early-return check: existing card more recent than latest verdict
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ac.id, ac.computed_at FROM public.account_card ac
                WHERE ac.account_id = %s AND ac.brief_version_id = %s LIMIT 1
                """,
                (str(account_id), str(brief_version_id)),
            )
            existing_card = await cur.fetchone()

    if existing_card is not None:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT MAX(v.computed_at) FROM public.verdict v WHERE v.entity_id = %s",
                    (str(entity_id),),
                )
                verdict_at_row = await cur.fetchone()

        card_computed_at = existing_card[1]
        latest_verdict_at = verdict_at_row[0] if verdict_at_row else None
        if latest_verdict_at is None or card_computed_at >= latest_verdict_at:
            log.info(
                "account_card.reuse_existing",
                account_id=str(account_id),
                card_id=str(existing_card[0]),
            )
            return str(existing_card[0])

    # 5. Fetch latest verdict
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT v.value, v.quotes FROM public.verdict v
                WHERE v.entity_id = %s AND v.brief_version_id = %s
                ORDER BY v.computed_at DESC LIMIT 1
                """,
                (str(entity_id), str(brief_version_id)),
            )
            verdict_row = await cur.fetchone()

    verdict_value: str = verdict_row[0] if verdict_row else "Unverified"
    verdict_quotes = verdict_row[1] if verdict_row else []
    verdict_quotes_str = json.dumps(verdict_quotes) if verdict_quotes else "[]"

    # 6. Fetch latest score
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT s.band, s.fit, s.evidence, s.scale, s.timing, s.reachability
                FROM public.score s WHERE s.account_id = %s AND s.brief_version_id = %s
                ORDER BY s.computed_at DESC LIMIT 1
                """,
                (str(account_id), str(brief_version_id)),
            )
            score_row = await cur.fetchone()

    band: str = score_row[0] if score_row else "unknown"
    fit: str = str(score_row[1]) if score_row else "N/A"
    evidence: str = str(score_row[2]) if score_row else "N/A"
    scale: str = str(score_row[3]) if score_row else "N/A"
    timing: str = str(score_row[4]) if score_row else "N/A"

    # 7. Fetch account stage
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.stage FROM public.account a WHERE a.id = %s",
                (str(account_id),),
            )
            stage_row = await cur.fetchone()

    account_stage: str = (stage_row[0] if stage_row else None) or "unknown"

    # 8. Fetch top claims
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT c.id, p.key, c.value FROM public.claim c
                JOIN public.predicate p ON p.id = c.predicate_id
                WHERE c.entity_id = %s AND c.status = 'active'
                ORDER BY c.confidence DESC LIMIT 10
                """,
                (str(entity_id),),
            )
            claims = await cur.fetchall()

    claims_summary = "\n".join(
        f"[CLAIM:{c[0]}] {c[1]}: {c[2]}" for c in claims
    ) if claims else "(no claims)"

    # 9. Fetch contact coverage
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM public.contact_point WHERE account_id = %s AND status = 'valid'",
                (str(account_id),),
            )
            contact_row = await cur.fetchone()

    contact_coverage: str = str(contact_row[0]) if contact_row else "0"

    # 10. Load prompt and call AI gateway
    prompt = load_prompt("account_card", 1)

    result = await gateway.run(
        Capability.WRITE,
        prompt,
        {
            "entity_name": display_name,
            "account_stage": account_stage,
            "hypothesis": hypothesis,
            "verdict_value": verdict_value,
            "verdict_quotes": verdict_quotes_str,
            "band": band,
            "fit": fit,
            "evidence": evidence,
            "scale": scale,
            "timing": timing,
            "claims_summary": claims_summary,
            "contact_coverage": contact_coverage,
        },
        AccountCardOutput,
        ctx,
    )

    card_output: AccountCardOutput = result.value

    # 11. Persist via app.upsert_account_card RPC
    content_json = json.dumps(card_output.model_dump())
    inputs_json = json.dumps(
        {
            "prompt_name": "account_card",
            "prompt_version": 1,
            "extractor_version_id": str(result.extractor_version_id),
        }
    )

    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.upsert_account_card(%s, %s, %s::jsonb, %s::jsonb)",
                (
                    str(account_id),
                    str(brief_version_id),
                    content_json,
                    inputs_json,
                ),
            )
            upsert_row = await cur.fetchone()

    card_id = str(upsert_row[0]) if upsert_row else None
    log.info(
        "account_card.generated",
        account_id=str(account_id),
        card_id=card_id,
    )
    return card_id
