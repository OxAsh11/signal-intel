"""M9 Activation — campaigns and the push cycle.

A push cycle moves eligible people from ranked accounts into a campaign:
  * the campaign must be active and its push mode 'dry run' or 'live';
  * candidates are people at accounts scored High or Medium under the campaign Brief's current
    version, with a valid e-mail contact point verified in the last 30 days;
  * suppressed people are never pushed; a person already in another active campaign is held back;
  * 'dry run' records exactly what a live push would send (external_write_log, dry_run = true)
    and creates no members; 'live' adds the member, which emits campaign.member_added.

The dry run and the live push use the same candidate query, so they match.
No sequencer connector is built in yet: a live push is handed to the destination through the
campaign.member_added event (webhook subscriptions), and its external write stays 'pending'
until a destination connector confirms it.

All writes go through app.* RPCs (S8).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

VERIFICATION_MAX_AGE_DAYS = 30


class KillSwitchError(Exception):
    """Raised when campaign push_mode = 'off'."""


_CANDIDATES_SQL = """
    SELECT a.id, cp.entity_id, cp.id, s.band, v.value, v.quotes,
           EXISTS (
             SELECT 1 FROM public.suppression_list sl
              WHERE sl.org_id = a.org_id AND sl.person_entity_id = cp.entity_id
           ) AS suppressed,
           EXISTS (
             SELECT 1
               FROM public.campaign_member cm2
               JOIN public.campaign c2 ON c2.id = cm2.campaign_id
              WHERE cm2.person_entity_id = cp.entity_id
                AND c2.workspace_id = a.workspace_id
                AND c2.id <> %s
                AND c2.status = 'active'
           ) AS held
      FROM public.account a
      JOIN public.score s
        ON s.account_id = a.id AND s.brief_version_id = %s AND s.band IN ('High', 'Medium')
      JOIN public.contact_point cp
        ON cp.account_id = a.id AND cp.type = 'email' AND cp.status = 'valid'
       AND cp.verified_at > now() - make_interval(days => %s)
      LEFT JOIN public.verdict v
        ON v.entity_id = a.entity_id AND v.brief_version_id = s.brief_version_id
     WHERE a.workspace_id = %s
       AND NOT EXISTS (
         SELECT 1 FROM public.campaign_member cm
          WHERE cm.campaign_id = %s AND cm.person_entity_id = cp.entity_id
       )
     ORDER BY s.priority DESC NULLS LAST, a.id
     LIMIT %s
"""


async def create_campaign(
    pool: AsyncConnectionPool,
    ctx: CallContext,
    name: str,
    motion: str,
    brief_id: UUID,
    destination_integration_id: UUID | None = None,
) -> UUID:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.create_campaign(%s, %s, %s, %s, %s::public.motion, %s)",
            (
                str(ctx.org_id),
                str(ctx.workspace_id),
                str(brief_id),
                name,
                motion,
                str(destination_integration_id) if destination_integration_id else None,
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    campaign_id = UUID(str(row[0]))
    log.info("activation.campaign_created", campaign_id=str(campaign_id), motion=motion)
    return campaign_id


def _variables(band: str | None, verdict: str | None, quotes: Any) -> dict[str, Any]:
    """Personalisation variables recorded with the member (Part 8.1 capture)."""
    if isinstance(quotes, str):
        quotes = json.loads(quotes)
    first = quotes[0] if isinstance(quotes, list) and quotes else {}
    return {
        "band": band,
        "verdict": verdict,
        "quote": first.get("quote") if isinstance(first, dict) else None,
        "quote_claim_id": first.get("claim_id") if isinstance(first, dict) else None,
        "copy_variant": None,
    }


async def push_campaign_batch(
    pool: AsyncConnectionPool,
    campaign_id: UUID,
    ctx: CallContext,
    *,
    limit: int = 500,
    _bounce_interval: int = 100,
) -> dict[str, Any]:
    """Run one push cycle. Returns {"pushed", "would_push", "skipped", "paused"}.

    Raises KillSwitchError when push_mode is 'off' and ValueError when the campaign is missing.
    """
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT c.org_id, c.workspace_id, c.push_mode, c.status, c.destination_integration_id,"
            # A Brief in Pilot has no live version for activation (M1).
            "       CASE WHEN b.state = 'Active' THEN b.current_version_id END"
            "  FROM public.campaign c"
            "  LEFT JOIN public.brief b ON b.id = c.brief_id"
            " WHERE c.id = %s",
            (str(campaign_id),),
        )
        row = await cur.fetchone()
    if row is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    org_id, workspace_id, push_mode, status, destination_id, brief_version_id = row
    if push_mode == "off":
        raise KillSwitchError(f"Campaign {campaign_id}: push_mode is 'off'")

    result: dict[str, Any] = {"pushed": 0, "would_push": 0, "skipped": 0, "paused": False}
    if status != "active":
        result["paused"] = status == "paused"
        log.info("activation.not_active", campaign_id=str(campaign_id), status=status)
        return result
    if brief_version_id is None:
        log.info("activation.no_published_brief", campaign_id=str(campaign_id))
        return result

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            _CANDIDATES_SQL,
            (
                str(campaign_id),
                str(brief_version_id),
                VERIFICATION_MAX_AGE_DAYS,
                str(workspace_id),
                str(campaign_id),
                limit,
            ),
        )
        candidates = await cur.fetchall()

    for (
        account_id,
        person_id,
        contact_point_id,
        band,
        verdict,
        quotes,
        suppressed,
        held,
    ) in candidates:
        if suppressed or held:
            log.info(
                "activation.held_back",
                campaign_id=str(campaign_id),
                person_entity_id=str(person_id),
                reason="suppressed" if suppressed else "in_another_active_campaign",
            )
            result["skipped"] += 1
            continue

        dry_run = push_mode == "dry run"
        write_args = (
            str(org_id),
            str(workspace_id),
            str(destination_id) if destination_id else None,
            "enroll",
            "person",
            str(person_id),
            None,
            f"campaign:{campaign_id}:person:{person_id}:{'dry' if dry_run else 'live'}",
            dry_run,
            "ok" if dry_run else "pending",
        )
        async with pool.connection() as conn:
            if not dry_run:
                await conn.execute(
                    "SELECT app.add_campaign_member(%s, %s, %s, %s, %s::jsonb)",
                    (
                        str(campaign_id),
                        str(account_id),
                        str(person_id),
                        str(contact_point_id),
                        json.dumps(_variables(band, verdict, quotes)),
                    ),
                )
            await conn.execute(
                "SELECT app.record_external_write(%s, %s, %s, %s, %s, %s::uuid, %s, %s, %s, %s)",
                write_args,
            )

        if dry_run:
            result["would_push"] += 1
            continue

        result["pushed"] += 1
        if result["pushed"] % _bounce_interval == 0:
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("SELECT app.check_and_pause_if_bouncing(%s)", (str(campaign_id),))
                bounce_row = await cur.fetchone()
            if bounce_row and bounce_row[0]:
                result["paused"] = True
                log.info("activation.bounce_pause", campaign_id=str(campaign_id))
                break

    log.info("activation.push_done", campaign_id=str(campaign_id), push_mode=push_mode, **result)
    return result


async def campaigns_due(pool: AsyncConnectionPool) -> list[tuple[UUID, UUID, UUID]]:
    """Campaigns a push cycle should visit: (campaign_id, workspace_id, org_id)."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT id, workspace_id, org_id FROM public.campaign"
            " WHERE status = 'active' AND push_mode <> 'off'"
        )
        rows = await cur.fetchall()
    return [(UUID(str(r[0])), UUID(str(r[1])), UUID(str(r[2]))) for r in rows]
