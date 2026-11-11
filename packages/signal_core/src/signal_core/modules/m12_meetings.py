"""M12 Meetings + Tasks — meeting sync, confirmation with proof rule, task creation,
and AI-powered next-step suggestion.

Flow for sync_meeting:
  1. Match attendee_domains against public.entity_alias (alias_type='domain') to find
     the best-matching account_id in this workspace.
  2. Call app.create_meeting RPC (idempotent: integration_id + external_id are unique).
  3. Return the meeting UUID.

Flow for confirm_meeting:
  1. Reject silently if proof_status not in ('confirmed', 'has_transcript') — PRD §M12.
  2. If attribution_campaign_id is None, run the 90-day attribution window query to
     find a campaign that touched the account.
  3. Call app.confirm_meeting RPC with the resolved attribution_campaign_id.

Flow for create_task:
  1. Call app.create_task RPC.
  2. Return the task UUID.

Flow for suggest_next_step:
  1. Fetch account context (stage + recent activity summary) from public.account.
  2. Call gateway.write('prompts/next_step/v1.md', context_dict) -> suggested text.
  3. Persist via app.upsert_next_step RPC.
  4. Return the suggested text.

Security note: all writes go through app.* SECURITY DEFINER RPCs.
No direct INSERT/UPDATE/DELETE from Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from signal_core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_PROOF_STATUSES = frozenset(["confirmed", "has_transcript"])
_ATTRIBUTION_DAYS = 90


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def sync_meeting(
    pool: object,
    org_id: UUID,
    workspace_id: UUID,
    integration_id: UUID,
    external_id: str,
    scheduled_at: datetime,
    attendee_domains: list[str],
) -> UUID:
    """Sync a calendar meeting into Signal Intel.

    Matches attendee_domains to a known account via entity_alias, then calls
    app.create_meeting (idempotent on integration_id + external_id).

    Parameters
    ----------
    pool:             async connection pool.
    org_id:           organisation UUID.
    workspace_id:     workspace UUID.
    integration_id:   calendar integration UUID (e.g. Google Calendar connector).
    external_id:      provider-side meeting ID.
    scheduled_at:     meeting start time (timezone-aware recommended).
    attendee_domains: e-mail domains of external attendees used to resolve account.

    Returns
    -------
    UUID of the meeting row.
    """
    # 1. Resolve account_id from attendee domains.
    account_id: UUID | None = None
    if attendee_domains:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT a.id
                    FROM public.entity_alias ea
                    JOIN public.account a ON a.entity_id = ea.entity_id
                    WHERE ea.alias_type = 'domain'
                      AND ea.alias_value = ANY(%s)
                      AND a.workspace_id = %s
                    LIMIT 1
                    """,
                    (attendee_domains, str(workspace_id)),
                )
                row = await cur.fetchone()
        if row is not None:
            account_id = UUID(str(row[0]))

    # 2. Create (or return existing) meeting row via SECURITY DEFINER RPC.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_meeting(%s, %s, %s, %s, %s, %s)",
                (
                    str(org_id),
                    str(workspace_id),
                    str(integration_id),
                    external_id,
                    scheduled_at,
                    str(account_id) if account_id is not None else None,
                ),
            )
            row = await cur.fetchone()

    meeting_id = UUID(str(row[0]))
    log.info(
        "meetings.synced",
        meeting_id=str(meeting_id),
        org_id=str(org_id),
        workspace_id=str(workspace_id),
        account_id=str(account_id) if account_id else None,
    )
    return meeting_id


async def confirm_meeting(
    pool: object,
    meeting_id: UUID,
    held_at: datetime,
    proof_status: str,
    attribution_campaign_id: UUID | None = None,
) -> None:
    """Confirm a meeting was held with proof.

    Only 'confirmed' and 'has_transcript' are accepted as proof — anything else
    is a silent no-op per PRD §M12.

    If attribution_campaign_id is None, the 90-day attribution window is queried
    automatically.

    Parameters
    ----------
    pool:                     async connection pool.
    meeting_id:               UUID of the public.meeting row.
    held_at:                  timestamp when the meeting actually occurred.
    proof_status:             'confirmed' | 'has_transcript' (all others ignored).
    attribution_campaign_id:  override the attribution lookup (use None to auto-detect).
    """
    if proof_status not in _VALID_PROOF_STATUSES:
        log.info(
            "meetings.confirm_skipped_no_proof",
            meeting_id=str(meeting_id),
            proof_status=proof_status,
        )
        return

    # Resolve attribution if not explicitly provided.
    if attribution_campaign_id is None:
        attribution_campaign_id = await _find_attribution_campaign(
            pool, meeting_id, held_at
        )

    # Call the SECURITY DEFINER RPC.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "SELECT app.confirm_meeting(%s, %s, %s, %s)",
            (
                str(meeting_id),
                held_at,
                proof_status,
                str(attribution_campaign_id) if attribution_campaign_id else None,
            ),
        )

    log.info(
        "meetings.confirmed",
        meeting_id=str(meeting_id),
        proof_status=proof_status,
        attribution_campaign_id=(
            str(attribution_campaign_id) if attribution_campaign_id else None
        ),
    )


async def _find_attribution_campaign(
    pool: object,
    meeting_id: UUID,
    held_at: datetime,
) -> UUID | None:
    """Return the most recent campaign that touched this meeting's account within
    90 days before held_at, or None if no match."""
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT cm.campaign_id
                FROM public.campaign_member cm
                JOIN public.meeting m ON m.account_id = cm.account_id
                WHERE m.id = %s
                  AND cm.pushed_at >= %s - interval '90 days'
                  AND cm.pushed_at <= %s
                LIMIT 1
                """,
                (str(meeting_id), held_at, held_at),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return UUID(str(row[0]))


async def create_task(
    pool: object,
    org_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    title: str,
    due_date: Any | None,
    origin: str,
    owner_member_id: UUID | None = None,
) -> UUID:
    """Create a task via app.create_task SECURITY DEFINER RPC.

    Parameters
    ----------
    pool:            async connection pool.
    org_id:          organisation UUID.
    workspace_id:    workspace UUID.
    account_id:      account this task is linked to.
    title:           task title / description.
    due_date:        optional due date (date or None).
    origin:          provenance string, e.g. 'reply', 'manual', 'ai'.
    owner_member_id: optional member who owns the task.

    Returns
    -------
    UUID of the new task row.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_task(%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(org_id),
                    str(workspace_id),
                    str(account_id),
                    title,
                    origin,
                    str(owner_member_id) if owner_member_id else None,
                    due_date,
                ),
            )
            row = await cur.fetchone()

    task_id = UUID(str(row[0]))
    log.info(
        "tasks.created",
        task_id=str(task_id),
        account_id=str(account_id),
        origin=origin,
    )
    return task_id


async def suggest_next_step(
    pool: object,
    gateway: Any,
    account_id: UUID,
    workspace_id: UUID,
) -> str:
    """Generate and persist a next-step suggestion for an account.

    Uses the AI gateway WRITE capability (Haiku by default) to produce a
    concise suggested action, then persists it via app.upsert_next_step.

    Parameters
    ----------
    pool:         async connection pool.
    gateway:      async gateway with `.write(prompt_path, context_dict) -> str`.
    account_id:   account to suggest next step for.
    workspace_id: workspace context.

    Returns
    -------
    The suggested next-step text.
    """
    # 1. Fetch account context summary.
    stage = "Unknown"
    recent_activity = "No recent activity"
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT stage FROM public.account WHERE id = %s",
                (str(account_id),),
            )
            row = await cur.fetchone()
    if row is not None:
        stage = str(row[0]) if row[0] is not None else "Unknown"

    # 2. Call AI gateway.
    suggestion: str = await gateway.write(
        "prompts/next_step/v1.md",
        {
            "account_id": str(account_id),
            "workspace_id": str(workspace_id),
            "stage": stage,
            "recent_activity": recent_activity,
        },
    )
    suggestion = suggestion.strip()

    # 3. Persist via SECURITY DEFINER RPC.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "SELECT app.upsert_next_step(%s, %s, %s::public.next_step_mode)",
            (str(account_id), suggestion, "ai"),
        )

    log.info(
        "meetings.next_step_suggested",
        account_id=str(account_id),
        workspace_id=str(workspace_id),
    )
    return suggestion
