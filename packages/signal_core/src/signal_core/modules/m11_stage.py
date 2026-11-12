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

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level job handle (wired by services/worker/src/worker/app.py)
# ---------------------------------------------------------------------------

stage_advance_job: Any | None = None

# ---------------------------------------------------------------------------
# Transition table
# ---------------------------------------------------------------------------

# Keys are activity kinds, plus three derived triggers that are not activities:
#   campaign_member_added  a person from the account entered a live campaign (Queued)
#   genuine_reply          a reply classified Interested or Referral (a raw reply never moves a stage)  # noqa: E501
#   dormant_timeout        no activity inside the workspace's dormancy window
# A genuine reply or inbound contact brings a Dormant account back into the flow.
TRANSITIONS: dict[str, dict[str, str]] = {
    "campaign_member_added": {"Untouched": "Queued"},
    "message_sent": {"Untouched": "Contacted", "Queued": "Contacted"},
    "genuine_reply": {
        "Untouched": "Engaged",
        "Queued": "Engaged",
        "Contacted": "Engaged",
        "Dormant": "Engaged",
    },
    "inbound_contact": {
        "Untouched": "Engaged",
        "Queued": "Engaged",
        "Contacted": "Engaged",
        "Dormant": "Engaged",
    },
    "meeting_held": {
        "Queued": "Meeting",
        "Contacted": "Meeting",
        "Engaged": "Meeting",
        "Qualified": "Meeting",
        "Dormant": "Meeting",
    },
    "marked_qualified": {"Contacted": "Qualified", "Engaged": "Qualified"},
    "marked_disqualified": {"*": "Disqualified"},
    "deal_created": {"Engaged": "Qualified", "Contacted": "Qualified", "Meeting": "Handed off"},
    "dormant_timeout": {"Contacted": "Dormant", "Engaged": "Dormant", "Qualified": "Dormant"},
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def advance_stage(
    pool: AsyncConnectionPool,
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
    async with pool.connection() as conn:
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
    UUID(str(row[1]))

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
    occurred_at = datetime.now(UTC)
    async with pool.connection() as conn:
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
    pool: AsyncConnectionPool,
    workspace_id: UUID,
    ctx: CallContext,
) -> int:
    """Mark accounts in Contacted/Engaged/Qualified as Dormant when nothing has happened on them
    inside the workspace's dormancy window (workspace_settings.dormancy_days, default 45).

    Returns the count of accounts successfully marked.
    """
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.id, a.org_id FROM public.account a"
                "  JOIN public.workspace_settings ws ON ws.workspace_id = a.workspace_id"
                " WHERE a.workspace_id = %s"
                "   AND a.stage IN ('Contacted', 'Engaged', 'Qualified')"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM public.activity act"
                "     WHERE act.account_id = a.id"
                "       AND act.occurred_at > now() - make_interval(days => ws.dormancy_days)"
                "   )"
                "   AND NOT EXISTS ("
                "     SELECT 1 FROM public.account_stage_event ase"
                "     WHERE ase.account_id = a.id"
                "       AND ase.occurred_at > now() - make_interval(days => ws.dormancy_days)"
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
