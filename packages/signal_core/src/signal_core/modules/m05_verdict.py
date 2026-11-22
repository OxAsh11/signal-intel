"""M5 Verdict computation — per entity, per Brief version.

Flow:
  1. Take the current version of every Pilot/Active Brief in the workspace (none → return None).
  2. Fetch the entity's claims that still count: active and not expired (F3 "claims age").
  3. For each Brief version: no claims → Unverified without an AI call. A version with evidence
     rules runs the deterministic rubric (signal_core.rubric): a decided outcome is stored
     without the AI, an open one is judged by verdict v3, which can never return Confirmed.
     A version without rules (legacy) is judged by verdict v2 against its hypothesis. Every
     verdict is stored with the claim ids and quotes it rests on, how it was reached
     (`inputs.method`: rubric · rubric_ai · ai) and, when the AI judged, its reasoning.
  4. Queue the account card.

Every path keeps: a human mark holds until the claims change; Confirmed needs a quoted claim,
otherwise it is only Likely; conflicting claims go to review.

AI failures are re-raised so the job retries: a verdict is never downgraded because a call failed.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict

import signal_core.modules.m15_account_card as _m15
from signal_core.ai_gateway.errors import AiOutputInvalid, AiProviderError
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.brief_columns import EvidenceRule, evidence_rules
from signal_core.briefs import ActiveBrief, active_brief_versions, holds_pilot_slot
from signal_core.derivation.writer import upsert_verdict
from signal_core.logging import get_logger
from signal_core.metering import CallContext
from signal_core.rubric import (
    RubricClaim,
    RubricOutcome,
    describe_result,
    describe_rule,
    evaluate_rubric,
    source_of,
)

log = get_logger(__name__)

# Wired by the worker app: the deferrable source.targeted_search task (M2 targeted search).
targeted_search_job: Any = None

# Module-level handles wired by build_app()
verdict_compute_job: Any | None = None
account_card_generate_job: Any | None = None

# Claims that count towards a verdict: active and not past their expiry. Each row is
# (id, key, value, quote, confidence, observed_at, record url, record id).
# The evidence a verdict weighs. A formula's derived claim (source record derived:formula)
# restates other claims and carries no quote, so it never reaches the rubric or the judge. A
# person's manual value (derived:manual) is evidence.
LIVE_CLAIMS_SQL = """
    SELECT c.id, p.key, c.value, c.quote, c.confidence, c.observed_at, sr.url, sr.id
      FROM public.claim c
      JOIN public.predicate p ON p.id = c.predicate_id
      JOIN public.source_record sr ON sr.id = c.source_record_id
     WHERE c.entity_id = %s
       AND c.status = 'active'
       AND (c.expires_at IS NULL OR c.expires_at > now())
       AND sr.connector_id <> 'derived:formula'
     ORDER BY c.observed_at DESC, c.id
"""


# Which of these claims a formula derived (source record derived:formula).
DERIVED_IDS_SQL = """
    SELECT c.id::text FROM public.claim c
      JOIN public.source_record sr ON sr.id = c.source_record_id
     WHERE c.id = ANY(%s::uuid[]) AND sr.connector_id = 'derived:formula'
"""


async def _derived_claim_ids(pool: AsyncConnectionPool, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(DERIVED_IDS_SQL, (ids,))
        rows = await cur.fetchall()
    return {str(r[0]) for r in rows}


async def _mark_still_holds(
    pool: AsyncConnectionPool, stored: list[str], claims: list[tuple[Any, ...]]
) -> bool:
    """A human mark holds while the evidence it was given is unchanged. The mark stored every
    active claim (public.resolve_verdict_review); the live claims leave derived ones out, so the
    stored derived ids are set aside before comparing."""
    live = {str(c[0]) for c in claims}
    kept = set(stored)
    if kept == live:
        return True
    return kept - await _derived_claim_ids(pool, sorted(kept - live)) == live


class VerdictOutput(BaseModel):
    value: Literal["Confirmed", "Likely", "Unverified", "Contradicted"]
    reasoning: str
    supporting_claim_indices: list[int]
    conflicting_claim_indices: list[int] = []


class _OpenVerdictSchema(BaseModel):
    """What verdict v3 is asked to answer: the strict schema sent to the model."""

    model_config = ConfigDict(title="OpenVerdictOutput")

    value: Literal["Likely", "Unverified", "Contradicted"]
    reasoning: str
    supporting_claim_indices: list[int]
    conflicting_claim_indices: list[int] = []


class OpenVerdictOutput(BaseModel):
    """Verdict v3, for a case the rubric left open: only the rules can confirm.

    The model is offered three values (the schema of _OpenVerdictSchema). A "Confirmed" it
    returns anyway is still parsed, so a paid answer is not thrown away and retried, and
    _open demotes it to Likely."""

    value: Literal["Confirmed", "Likely", "Unverified", "Contradicted"]
    reasoning: str
    supporting_claim_indices: list[int]
    conflicting_claim_indices: list[int] = []

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return _OpenVerdictSchema.model_json_schema(*args, **kwargs)


def rubric_claim(c: tuple[Any, ...]) -> RubricClaim:
    """A LIVE_CLAIMS_SQL row as the rubric sees it."""
    return RubricClaim(
        id=str(c[0]),
        key=str(c[1]),
        value=c[2],
        quote=c[3],
        confidence=c[4],
        observed_at=c[5],
        source=source_of(c[6], str(c[7])),
    )


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
    *,
    only_version: UUID | None = None,
) -> str | None:
    """Compute and store the entity's verdict under every active Brief version in the workspace.

    With only_version, judge under that Brief version alone (re-derivation after an edit).

    Returns the verdict under the most recently published Brief that judged it, or None when
    no Brief did (no active Brief, or only pilots whose sample is full).
    """
    briefs = await active_brief_versions(pool, workspace_id)
    if only_version is not None:
        briefs = [b for b in briefs if b.brief_version_id == only_version]
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

    await defer_targeted_searches(briefs, values, entity_id, workspace_id)
    await _defer_account_card(pool, entity_id, workspace_id)
    return next((v for v in values if v is not None), None)


async def defer_targeted_searches(
    briefs: list[ActiveBrief],
    values: list[str | None],
    entity_id: UUID,
    workspace_id: UUID,
) -> int:
    """Ask for one narrow search per Brief whose verdict on the entity is Unverified (M2).

    The job itself throttles to once per Brief and entity per 30 days.
    """
    job = targeted_search_job
    if job is None:
        return 0
    queued = 0
    for brief, value in zip(briefs, values, strict=True):
        if value != "Unverified":
            continue
        with contextlib.suppress(Exception):
            await job.configure(queueing_lock=f"ts:{brief.brief_id}:{entity_id}").defer_async(
                entity_id=str(entity_id),
                brief_id=str(brief.brief_id),
                workspace_id=str(workspace_id),
            )
            queued += 1
    return queued


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
        if inputs.get("human_override") and await _mark_still_holds(
            pool, [str(i) for i in inputs.get("live_claim_ids") or []], claims
        ):
            return str(existing[0])

    rules = evidence_rules(brief.config)
    if not rules:
        return await _judge_legacy(pool, gateway, brief, entity_id, display_name, claims, ctx)

    outcome = evaluate_rubric(rules, [rubric_claim(c) for c in claims], datetime.now(UTC))
    if outcome.value is None:
        return await _judge_open(
            pool, gateway, brief, entity_id, display_name, claims, rules, outcome, ctx
        )
    cited = set(outcome.claim_ids)
    return await _store(
        pool,
        brief,
        entity_id,
        claims,
        outcome.value,
        [c for c in claims if str(c[0]) in cited],
        {"method": "rubric", "rubric": [r.as_dict() for r in outcome.rules]},
        [],
    )


async def _judge_legacy(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    brief: ActiveBrief,
    entity_id: UUID,
    display_name: str,
    claims: list[tuple[Any, ...]],
    ctx: CallContext,
) -> str:
    """A Brief version without evidence rules: verdict v2 against the hypothesis."""
    claims_text = "\n".join(
        f"[{i}] {c[1]}: {c[2]} (confidence: {c[4]}) | quote: {c[3] or 'N/A'}"
        for i, c in enumerate(claims)
    )
    result = await _run(
        gateway,
        brief,
        entity_id,
        2,
        {"hypothesis": brief.hypothesis, "entity_name": display_name, "claims_text": claims_text},
        VerdictOutput,
        ctx,
    )
    out: VerdictOutput = result.value
    return await _store(
        pool,
        brief,
        entity_id,
        claims,
        out.value,
        _pick(claims, out.supporting_claim_indices),
        {
            "method": "ai",
            "reasoning": out.reasoning,
            "prompt_name": "verdict",
            "prompt_version": 2,
            "extractor_version_id": str(result.extractor_version_id),
        },
        out.conflicting_claim_indices,
    )


async def _judge_open(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    brief: ActiveBrief,
    entity_id: UUID,
    display_name: str,
    claims: list[tuple[Any, ...]],
    rules: list[EvidenceRule],
    outcome: RubricOutcome,
    ctx: CallContext,
) -> str:
    """A case the rubric left open: verdict v3, given the rules and what the evidence showed."""
    examples = brief.config.get("examples")
    if not isinstance(examples, dict):
        examples = {}
    claims_text = "\n".join(
        f"[{i}] {c[1]}: {c[2]} | confidence: {c[4]}"
        f" | observed: {c[5].date().isoformat() if isinstance(c[5], datetime) else 'unknown'}"
        f" | source: {source_of(c[6], str(c[7]))} | quote: {c[3] or 'N/A'}"
        for i, c in enumerate(claims)
    )
    result = await _run(
        gateway,
        brief,
        entity_id,
        3,
        {
            "hypothesis": brief.hypothesis,
            "examples_positive": _bullets(examples.get("positive")),
            "examples_negative": _bullets(examples.get("negative")),
            "rules": "\n".join(f"- {describe_rule(r)}" for r in rules),
            "rule_results": "\n".join(
                f"- {describe_result(r, res)}" for r, res in zip(rules, outcome.rules, strict=True)
            ),
            "entity_name": display_name,
            "claims_text": claims_text,
        },
        OpenVerdictOutput,
        ctx,
    )
    out: OpenVerdictOutput = result.value
    value = out.value
    if value == "Confirmed":
        log.warning(
            "verdict.open_confirmed_demoted",
            entity_id=str(entity_id),
            brief_version_id=str(brief.brief_version_id),
        )
        value = "Likely"
    return await _store(
        pool,
        brief,
        entity_id,
        claims,
        value,
        _pick(claims, out.supporting_claim_indices),
        {
            "method": "rubric_ai",
            "reasoning": out.reasoning,
            "rubric": [r.as_dict() for r in outcome.rules],
            "prompt_name": "verdict",
            "prompt_version": 3,
            "extractor_version_id": str(result.extractor_version_id),
        },
        out.conflicting_claim_indices,
    )


async def _run(
    gateway: AiGateway,
    brief: ActiveBrief,
    entity_id: UUID,
    version: int,
    variables: dict[str, str],
    output: type[BaseModel],
    ctx: CallContext,
) -> Any:
    """Call verdict v<version>. Failures are logged and re-raised so the job retries."""
    try:
        return await gateway.run(
            Capability.JUDGE, load_prompt("verdict", version), variables, output, ctx
        )
    except (AiProviderError, AiOutputInvalid) as exc:
        log.warning(
            "verdict.ai_failed",
            entity_id=str(entity_id),
            brief_version_id=str(brief.brief_version_id),
            prompt_version=version,
            error=str(exc),
        )
        raise


def _bullets(items: Any) -> str:
    if not isinstance(items, list) or not items:
        return "- none given"
    return "\n".join(f"- {item}" for item in items)


def _pick(claims: list[tuple[Any, ...]], indices: list[int]) -> list[tuple[Any, ...]]:
    return [claims[i] for i in indices if 0 <= i < len(claims)]


async def _store(
    pool: AsyncConnectionPool,
    brief: ActiveBrief,
    entity_id: UUID,
    claims: list[tuple[Any, ...]],
    value: str,
    supporting: list[tuple[Any, ...]],
    inputs: dict[str, Any],
    flagged: list[int],
) -> str:
    """Store the verdict with the claims it rests on, and queue any conflict for review."""
    # Confirmed needs at least one quoted claim behind it (M5); otherwise it is only Likely.
    if value == "Confirmed" and not any(c[3] for c in supporting):
        value = "Likely"

    verdict_id = await upsert_verdict(
        pool,
        brief.brief_version_id,
        entity_id,
        value,
        [UUID(str(c[0])) for c in supporting],
        [{"claim_id": str(c[0]), "quote": c[3]} for c in supporting if c[3]],
        inputs,
    )

    # Conflicting claims never cancel silently: a person decides, with the claims shown.
    conflict = conflicting_claims(claims, flagged)
    if conflict:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT app.queue_verdict_review(%s, 'conflict', %s::uuid[])",
                (str(verdict_id), [str(c) for c in conflict]),
            )
    return value


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
