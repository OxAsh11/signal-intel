"""M11 Stage machine — activity-driven account stage transitions + dormant detection.

Flow for advance_stage:
  1. Fetch the account's current stage from the DB.
  2. Look up the applicable transition in TRANSITIONS:
       - wildcard ('*') key → apply unconditionally.
       - stage-specific key  → only if current_stage matches.
  3. If a transition applies, call app.upsert_account_stage RPC.
  4. Return the new stage string, or None when no transition applies.

Flow for mark_dormant_accounts:
  1. Query accounts in Contacted/Engaged/Qualified with no stage event in 45 days.
  2. Call advance_stage with activity_kind='dormant_timeout' for each.
  3. Return count of accounts marked dormant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level job handle (wired by services/worker/src/worker/app.py)
# ---------------------------------------------------------------------------

stage_advance_job: object | None = None

# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

TRANSITIONS: dict[str, dict[str, str]] = {
    "message_sent":        {"Untouched": "Contacted", "Queued": "Contacted"},
    "reply_received":      {"Contacted": "Engaged"},
    "inbound_contact":     {"Untouched": "Engaged", "Contacted": "Engaged"},
    "meeting_held":        {"Contacted": "Meeting", "Engaged": "Meeting", "Qualified": "Meeting"},
    "marked_qualified":    {"Contacted": "Qualified", "Engaged": "Qualified"},
    "marked_disqualified": {"*": "Disqualified"},
    "deal_created":        {"Meeting": "Handed off"},
    "dormant_timeout":     {"Contacted": "Dormant", "Engaged": "Dormant", "Qualified": "Dormant"},
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def advance_stage(
    pool: object,
    account_id: UUID,
    activity_id: UUID | None,
    activity_kind: str,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Apply a stage transition.

    Returns the new stage string when a transition applies, or None when no
    transition applies for the current (stage, activity_kind) combination.
    """
    # 1. Fetch current stage and org_id.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.stage, a.org_id FROM public.account a"
                " WHERE a.id = %s AND a.workspace_id = %s",
                (str(account_id), str(workspace_id)),
            )
            row = await cur.fetchone()

    if row is None:
        log.warning(
            "stage.account_not_found",
            account_id=str(account_id),
            workspace_id=str(workspace_id),
        )
        return None

    current_stage: str = row[0]
    org_id: UUID = UUID(str(row[1]))

    # 2. Determine target stage from TRANSITIONS.
    stage_map = TRANSITIONS.get(activity_kind)
    if stage_map is None:
        # Unknown activity_kind — no-op.
        return None

    if "*" in stage_map:
        to_stage = stage_map["*"]
    elif current_stage in stage_map:
        to_stage = stage_map[current_stage]
    else:
        return None

    # 3. Write via RPC.
    occurred_at = datetime.now(timezone.utc)
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "SELECT app.upsert_account_stage(%s, %s::public.stage, %s, %s)",
            (
                str(account_id),
                to_stage,
                str(activity_id) if activity_id is not None else None,
                occurred_at,
            ),
        )

    log.info(
        "stage.advanced",
        account_id=str(account_id),
        from_stage=current_stage,
        to_stage=to_stage,
        activity_kind=activity_kind,
    )
    return to_stage


async def mark_dormant_accounts(
    pool: object,
    workspace_id: UUID,
    ctx: CallContext,
) -> int:
    """Mark accounts in Contacted/Engaged/Qualified with no activity in 45 days as Dormant.

    Returns the count of accounts successfully marked.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.id, a.org_id FROM public.account a"
                " WHERE a.workspace_id = %s"
                "   AND a.stage IN ('Contacted', 'Engaged', 'Qualified')"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM public.account_stage_event ase"
                "     WHERE ase.account_id = a.id"
                "       AND ase.occurred_at > now() - interval '45 days'"
                "   )",
                (str(workspace_id),),
            )
            rows = await cur.fetchall()

    count = 0
    for row in rows:
        account_id = UUID(str(row[0]))
        try:
            result = await advance_stage(
                pool,
                account_id,
                activity_id=None,
                activity_kind="dormant_timeout",
                workspace_id=workspace_id,
                ctx=ctx,
            )
            if result is not None:
                count += 1
        except Exception:
            log.exception("stage.dormant_error", account_id=str(account_id))

    log.info("stage.dormant_sweep_done", workspace_id=str(workspace_id), marked=count)
    return count
