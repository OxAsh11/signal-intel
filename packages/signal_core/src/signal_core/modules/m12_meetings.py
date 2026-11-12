"""M12 Meetings, tasks and next steps.

sync_meeting      match a calendar event to an account by attendee domain and store it.
                  An unmatched meeting is stored without an account, for review (never dropped).
confirm_meeting   record proof that a meeting was held. app.confirm_meeting resolves outbound
                  attribution (person, then account, inside the attribution window), logs the
                  meeting_held activity and emits meeting.held.
create_task       create a task.
suggest_next_step write the next step for an account. In Active mode it also becomes a task.

All writes go through app.* RPCs (S8).
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

_VALID_PROOF_STATUSES = frozenset(["confirmed", "has_transcript"])


class NextStepOutput(BaseModel):
    next_step: str


async def sync_meeting(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    integration_id: UUID,
    external_id: str,
    scheduled_at: datetime,
    attendee_domains: list[str],
) -> UUID:
    """Store a calendar meeting (idempotent on integration + external id). Returns its id."""
    account_id: UUID | None = None
    domains = sorted({d.strip().lower() for d in attendee_domains if d and d.strip()})
    if domains:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                SELECT a.id
                  FROM public.entity_alias ea
                  JOIN public.account a ON a.entity_id = ea.entity_id
                 WHERE ea.org_id = %s
                   AND ea.alias_type = 'domain'
                   AND ea.value_normalised = ANY(%s::text[])
                   AND a.workspace_id = %s
                 ORDER BY ea.confidence DESC, a.created_at
                 LIMIT 1
                """,
                (str(org_id), domains, str(workspace_id)),
            )
            row = await cur.fetchone()
        if row is not None:
            account_id = UUID(str(row[0]))

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.create_meeting(%s, %s, %s, %s, %s, %s, %s)",
            (
                str(org_id),
                str(workspace_id),
                str(account_id) if account_id else None,
                None,
                external_id,
                str(integration_id),
                scheduled_at,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    meeting_id = UUID(str(row[0]))
    log.info(
        "meetings.synced",
        meeting_id=str(meeting_id),
        workspace_id=str(workspace_id),
        matched=account_id is not None,
    )
    return meeting_id


async def confirm_meeting(
    pool: AsyncConnectionPool,
    meeting_id: UUID,
    held_at: datetime,
    proof_status: str,
    attribution_campaign_id: UUID | None = None,
) -> bool:
    """Record a held meeting. Without proof ('confirmed' or 'has_transcript') nothing is written
    and False is returned (M12 proof rule)."""
    if proof_status not in _VALID_PROOF_STATUSES:
        log.info(
            "meetings.confirm_skipped_no_proof",
            meeting_id=str(meeting_id),
            proof_status=proof_status,
        )
        return False

    async with pool.connection() as conn:
        await conn.execute(
            "SELECT app.confirm_meeting(%s, %s, %s, %s)",
            (
                str(meeting_id),
                held_at,
                proof_status,
                str(attribution_campaign_id) if attribution_campaign_id else None,
            ),
        )
    log.info("meetings.confirmed", meeting_id=str(meeting_id), proof_status=proof_status)
    return True


async def create_task(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    title: str,
    due_date: date | None,
    origin: str,
    owner_member_id: UUID | None = None,
) -> UUID:
    """Create a task. `origin` is one of manual, reply, meeting, next_step."""
    async with pool.connection() as conn, conn.cursor() as cur:
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
    assert row is not None
    task_id = UUID(str(row[0]))
    log.info("tasks.created", task_id=str(task_id), account_id=str(account_id), origin=origin)
    return task_id


async def suggest_next_step(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Write and store the next step for an account. Returns the text, or None when the account
    is not in the workspace."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT a.org_id, a.stage, a.owner_member_id, e.display_name, ws.next_step_mode
              FROM public.account a
              JOIN public.entity e ON e.id = a.entity_id
              JOIN public.workspace_settings ws ON ws.workspace_id = a.workspace_id
             WHERE a.id = %s AND a.workspace_id = %s
            """,
            (str(account_id), str(workspace_id)),
        )
        row = await cur.fetchone()
        if row is None:
            log.warning("next_step.account_not_found", account_id=str(account_id))
            return None
        org_id, stage, owner_member_id, account_name, mode = row

        await cur.execute(
            "SELECT kind, occurred_at FROM public.activity"
            " WHERE account_id = %s ORDER BY occurred_at DESC LIMIT 5",
            (str(account_id),),
        )
        activity_rows = await cur.fetchall()

    recent_activity = (
        "; ".join(f"{kind} on {occurred_at:%Y-%m-%d}" for kind, occurred_at in activity_rows)
        or "No activity yet"
    )

    result = await gateway.run(
        Capability.WRITE,
        load_prompt("next_step", 2),
        {
            "account_name": str(account_name or "unknown"),
            "stage": str(stage),
            "recent_activity": recent_activity,
        },
        NextStepOutput,
        ctx,
        max_tokens=300,
    )
    suggestion = result.value.next_step.strip()

    async with pool.connection() as conn:
        await conn.execute(
            "SELECT app.upsert_next_step(%s, %s, %s::public.next_step_mode)",
            (str(account_id), suggestion, str(mode)),
        )

    # Active mode: the step becomes a task for the account owner (M12).
    if str(mode) == "Active":
        await create_task(
            pool,
            UUID(str(org_id)),
            workspace_id,
            account_id,
            suggestion[:500],
            None,
            "next_step",
            UUID(str(owner_member_id)) if owner_member_id else None,
        )

    log.info("next_step.suggested", account_id=str(account_id), mode=str(mode))
    return suggestion
