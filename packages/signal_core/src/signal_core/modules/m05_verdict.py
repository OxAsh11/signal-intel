"""M5 Verdict computation — per entity, per Brief version.

Flow:
  1. Take the current version of every Pilot/Active Brief in the workspace (none → return None).
  2. Fetch the entity's claims that still count: active and not expired (F3 "claims age").
  3. For each Brief version: no claims → Unverified without an AI call; otherwise judge the
     claims against that Brief's hypothesis (Capability.JUDGE) and store the verdict with the
     claim ids and quotes it rests on.
  4. Queue the account card.

AI failures are re-raised so the job retries: a verdict is never downgraded because a call failed.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import Any, Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

import signal_core.modules.m15_account_card as _m15
from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.briefs import ActiveBrief, active_brief_versions, holds_pilot_slot
from signal_core.derivation.writer import upsert_verdict
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Module-level handles wired by build_app()
verdict_compute_job: Any | None = None
account_card_generate_job: Any | None = None

# Claims that count towards a verdict: active and not past their expiry.
LIVE_CLAIMS_SQL = """
    SELECT c.id, p.key, c.value, c.quote, c.confidence
      FROM public.claim c
      JOIN public.predicate p ON p.id = c.predicate_id
     WHERE c.entity_id = %s
       AND c.status = 'active'
       AND (c.expires_at IS NULL OR c.expires_at > now())
     ORDER BY c.observed_at DESC
"""


class VerdictOutput(BaseModel):
    value: Literal["Confirmed", "Likely", "Unverified", "Contradicted"]
    reasoning: str
    supporting_claim_indices: list[int]
    conflicting_claim_indices: list[int] = []


def conflicting_claims(claims: list[tuple[Any, ...]], flagged: list[int]) -> list[UUID]:
    """Claims that disagree: those the judge flagged, plus any yes/no predicate holding both
    answers. Fewer than two claims is not a conflict."""
    ids = {str(claims[i][0]) for i in flagged if 0 <= i < len(claims)}
    answers: dict[str, dict[bool, list[str]]] = {}
    for c in claims:
        if isinstance(c[2], bool):
            answers.setdefault(str(c[1]), {}).setdefault(c[2], []).append(str(c[0]))
    for by_answer in answers.values():
        if len(by_answer) == 2:
            ids.update(i for group in by_answer.values() for i in group)
    return [UUID(i) for i in sorted(ids)] if len(ids) >= 2 else []


async def sample_for_review(pool: AsyncConnectionPool, workspace_id: UUID) -> int:
    """Queue this week's sample of new Confirmed verdicts for human review (M5)."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT app.sample_verdicts_for_review(%s)", (str(workspace_id),))
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def compute_verdict(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    entity_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Compute and store the entity's verdict under every active Brief version in the workspace.

    Returns the verdict under the most recently published Brief that judged it, or None when
    no Brief did (no active Brief, or only pilots whose sample is full).
    """
    briefs = await active_brief_versions(pool, workspace_id)
    if not briefs:
        log.info("verdict.no_brief_version", workspace_id=str(workspace_id))
        return None

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute("SELECT display_name FROM public.entity WHERE id = %s", (str(entity_id),))
        entity_row = await cur.fetchone()
        display_name: str = (entity_row[0] if entity_row else None) or "unknown"

        await cur.execute(LIVE_CLAIMS_SQL, (str(entity_id),))
        claims = await cur.fetchall()

    values = [
        await _verdict_for_brief(
            pool,
            gateway,
            brief,
            entity_id,
            display_name,
            claims,
            replace(ctx, brief_id=brief.brief_id),
        )
        for brief in briefs
    ]

    await _defer_account_card(pool, entity_id, workspace_id)
    return next((v for v in values if v is not None), None)


async def _verdict_for_brief(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    brief: ActiveBrief,
    entity_id: UUID,
    display_name: str,
    claims: list[tuple[Any, ...]],
    ctx: CallContext,
) -> str | None:
    # A Pilot Brief judges only the accounts in its sample (M1).
    if brief.state == "Pilot" and not await holds_pilot_slot(
        pool, brief.brief_version_id, entity_id, claim=True
    ):
        return None
    if not claims:
        await upsert_verdict(
            pool, brief.brief_version_id, entity_id, "Unverified", [], [], {"reason": "no_claims"}
        )
        return "Unverified"

    # A human mark holds until the entity's claims change (M5 human checks).
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT value, inputs FROM public.verdict"
            " WHERE brief_version_id = %s AND entity_id = %s",
            (str(brief.brief_version_id), str(entity_id)),
        )
        existing = await cur.fetchone()
    if existing is not None:
        inputs = existing[1] if isinstance(existing[1], dict) else {}
        if inputs.get("human_override") and set(inputs.get("live_claim_ids") or []) == {
            str(c[0]) for c in claims
        }:
            return str(existing[0])

    claims_text = "\n".join(
        f"[{i}] {c[1]}: {c[2]} (confidence: {c[4]}) | quote: {c[3] or 'N/A'}"
        for i, c in enumerate(claims)
    )
    try:
        result = await gateway.run(
            Capability.JUDGE,
            load_prompt("verdict", 2),
            {
                "hypothesis": brief.hypothesis,
                "entity_name": display_name,
                "claims_text": claims_text,
            },
            VerdictOutput,
            ctx,
        )
    except (AiProviderError, AiOutputInvalid) as exc:
        log.warning(
            "verdict.ai_failed",
            entity_id=str(entity_id),
            brief_version_id=str(brief.brief_version_id),
            error=str(exc),
        )
        raise

    supporting = [claims[i] for i in result.value.supporting_claim_indices if 0 <= i < len(claims)]
    # Confirmed needs at least one quoted claim behind it (M5); otherwise it is only Likely.
    final_value: str = result.value.value
    if final_value == "Confirmed" and not any(c[3] for c in supporting):
        final_value = "Likely"

    verdict_id = await upsert_verdict(
        pool,
        brief.brief_version_id,
        entity_id,
        final_value,
        [UUID(str(c[0])) for c in supporting],
        [{"claim_id": str(c[0]), "quote": c[3]} for c in supporting if c[3]],
        {
            "prompt_name": "verdict",
            "prompt_version": 2,
            "extractor_version_id": str(result.extractor_version_id),
        },
    )

    # Conflicting claims never cancel silently: a person decides, with the claims shown.
    conflict = conflicting_claims(claims, result.value.conflicting_claim_indices)
    if conflict:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT app.queue_verdict_review(%s, 'conflict', %s::uuid[])",
                (str(verdict_id), [str(c) for c in conflict]),
            )
    return final_value


async def _defer_account_card(
    pool: AsyncConnectionPool,
    entity_id: UUID,
    workspace_id: UUID,
) -> None:
    """Queue the account card for the entity's account in this workspace, if the job is wired."""
    job = _m15.account_card_generate_job
    if job is None:
        return

    row = None
    with contextlib.suppress(Exception):
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT a.id, a.org_id FROM public.account a"
                " WHERE a.entity_id = %s AND a.workspace_id = %s LIMIT 1",
                (str(entity_id), str(workspace_id)),
            )
            row = await cur.fetchone()
    if row is None:
        log.info(
            "verdict.no_account_for_entity",
            entity_id=str(entity_id),
            workspace_id=str(workspace_id),
        )
        return

    with contextlib.suppress(Exception):
        await job.configure(queueing_lock=f"card:{row[0]}").defer_async(
            account_id=str(row[0]),
            workspace_id=str(workspace_id),
            org_id=str(row[1]),
        )
