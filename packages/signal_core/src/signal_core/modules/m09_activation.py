"""M9 Activation — campaign creation and push with bounce guard.

Flow for create_campaign:
  1. Call app.create_campaign RPC with org/workspace context from CallContext.
  2. Return the new campaign UUID.

Flow for push_campaign_batch:
  1. Load campaign row (SELECT … FOR UPDATE) to prevent concurrent double-push.
  2. If push_mode = 'off' raise KillSwitchError (kill switch active).
  3. Load all accounts in the workspace that have at least one valid contact_point.
  4. For each candidate:
       a. Skip if person_entity_id is in suppression_list (count as skipped).
       b. Skip if person already has an active/pending campaign_member row in this
          workspace for a *different* campaign (one-active-campaign-per-person rule).
       c. In 'live' mode: call app.add_campaign_member + app.record_external_write.
       d. In 'dry run' mode: call app.record_external_write with dry_run=True ONLY —
          no campaign_member row is created.
  5. After every _bounce_interval pushes (live mode only) call
     app.check_and_pause_if_bouncing; stop loop if it returns True.
  6. Return {"pushed": int, "skipped": int, "paused": bool}.

Security note (S8): all writes go through app.* SECURITY DEFINER RPCs.
No direct INSERT/UPDATE/DELETE from Python.
"""

from __future__ import annotations

from uuid import UUID

from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class KillSwitchError(Exception):
    """Raised when campaign push_mode = 'off' (kill switch is active)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_campaign(
    pool: object,
    ctx: CallContext,
    name: str,
    motion: str,
    brief_id: UUID,
    destination_integration_id: UUID | None = None,
) -> UUID:
    """Create a campaign via the app.create_campaign SECURITY DEFINER RPC.

    Returns the new campaign UUID.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
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

    campaign_id = UUID(str(row[0]))
    log.info(
        "activation.campaign_created",
        campaign_id=str(campaign_id),
        name=name,
        motion=motion,
        workspace_id=str(ctx.workspace_id),
    )
    return campaign_id


async def push_campaign_batch(
    pool: object,
    gateway: object,
    campaign_id: UUID,
    ctx: CallContext,
    *,
    _bounce_interval: int = 100,
) -> dict:
    """Push eligible persons into a campaign.

    Returns {"pushed": int, "skipped": int, "paused": bool}.

    Raises:
        KillSwitchError: if campaign push_mode = 'off'.
        ValueError: if the campaign row is not found.

    Parameters:
        _bounce_interval: check bounce rate after this many live pushes (default 100;
                          injectable for testing).
    """
    # ------------------------------------------------------------------
    # 1. Load campaign row with FOR UPDATE (prevent concurrent double-push)
    # ------------------------------------------------------------------
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, workspace_id, brief_id, push_mode,"
                " destination_integration_id"
                " FROM public.campaign"
                " WHERE id = %s"
                " FOR UPDATE",
                (str(campaign_id),),
            )
            campaign_row = await cur.fetchone()

    if campaign_row is None:
        raise ValueError(f"Campaign {campaign_id} not found")

    _cid, org_id_raw, ws_id_raw, _brief_id_raw, push_mode, dest_int_id_raw = campaign_row
    org_id = UUID(str(org_id_raw))
    workspace_id = UUID(str(ws_id_raw))
    destination_integration_id = UUID(str(dest_int_id_raw)) if dest_int_id_raw else None

    # ------------------------------------------------------------------
    # 2. Kill-switch check
    # ------------------------------------------------------------------
    if push_mode == "off":
        raise KillSwitchError(
            f"Campaign {campaign_id}: push_mode is 'off' (kill switch active)"
        )

    # ------------------------------------------------------------------
    # 3. Load candidates — accounts in this workspace with a valid contact_point
    # ------------------------------------------------------------------
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT a.id, cp.person_entity_id, cp.id"
                " FROM public.account a"
                " JOIN public.contact_point cp"
                "   ON cp.account_id = a.id AND cp.status = 'valid'"
                " WHERE a.workspace_id = %s",
                (str(workspace_id),),
            )
            candidates = await cur.fetchall()

    pushed = 0
    skipped = 0
    paused = False

    for (account_id_raw, person_entity_id_raw, cp_id_raw) in candidates:
        account_id = UUID(str(account_id_raw))
        person_entity_id = UUID(str(person_entity_id_raw))
        contact_point_id = UUID(str(cp_id_raw))

        # ------------------------------------------------------------------
        # 4a. Suppression check
        # ------------------------------------------------------------------
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM public.suppression_list sl"
                    " WHERE sl.person_entity_id = %s AND sl.org_id = %s"
                    " LIMIT 1",
                    (str(person_entity_id), str(org_id)),
                )
                supp_row = await cur.fetchone()

        if supp_row is not None:
            log.debug(
                "activation.suppressed",
                person_entity_id=str(person_entity_id),
                campaign_id=str(campaign_id),
            )
            skipped += 1
            continue

        # ------------------------------------------------------------------
        # 4b. One-active-campaign-per-person check
        # ------------------------------------------------------------------
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1"
                    " FROM public.campaign_member cm"
                    " JOIN public.campaign c2 ON c2.id = cm.campaign_id"
                    " WHERE cm.person_entity_id = %s"
                    "   AND c2.workspace_id = %s"
                    "   AND c2.id != %s"
                    "   AND cm.status IN ('pending', 'active')"
                    " LIMIT 1",
                    (str(person_entity_id), str(workspace_id), str(campaign_id)),
                )
                existing_row = await cur.fetchone()

        if existing_row is not None:
            log.debug(
                "activation.already_in_campaign",
                person_entity_id=str(person_entity_id),
                campaign_id=str(campaign_id),
            )
            skipped += 1
            continue

        idempotency_key = f"campaign:{campaign_id}:person:{person_entity_id}"

        # ------------------------------------------------------------------
        # 4c/d. Push or dry-run record
        # ------------------------------------------------------------------
        if push_mode == "dry run":
            # Record the write with dry_run=True; do NOT create a campaign_member row.
            async with pool.connection() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    "SELECT app.record_external_write(%s, %s, %s, %s, %s, %s::uuid,"
                    " %s, %s, %s, %s)",
                    (
                        str(org_id),
                        str(workspace_id),
                        str(destination_integration_id) if destination_integration_id else None,
                        "enroll",
                        "person",
                        str(person_entity_id),
                        None,            # external_id (not known until live send)
                        idempotency_key,
                        True,            # dry_run = True
                        "ok",
                    ),
                )
            # pushed intentionally stays 0 in dry run

        elif push_mode == "live":
            # Add the campaign member via RPC.
            async with pool.connection() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    "SELECT app.add_campaign_member(%s, %s, %s, %s, %s::jsonb)",
                    (
                        str(campaign_id),
                        str(account_id),
                        str(person_entity_id),
                        str(contact_point_id),
                        "{}",
                    ),
                )

            # Record the external write (live).
            async with pool.connection() as conn:  # type: ignore[union-attr]
                await conn.execute(
                    "SELECT app.record_external_write(%s, %s, %s, %s, %s, %s::uuid,"
                    " %s, %s, %s, %s)",
                    (
                        str(org_id),
                        str(workspace_id),
                        str(destination_integration_id) if destination_integration_id else None,
                        "enroll",
                        "person",
                        str(person_entity_id),
                        None,            # external_id
                        idempotency_key,
                        False,           # dry_run = False
                        "ok",
                    ),
                )

            pushed += 1

            # ------------------------------------------------------------------
            # 5. Bounce guard — check after every _bounce_interval live pushes
            # ------------------------------------------------------------------
            if pushed % _bounce_interval == 0:
                async with pool.connection() as conn:  # type: ignore[union-attr]
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT app.check_and_pause_if_bouncing(%s)",
                            (str(campaign_id),),
                        )
                        bounce_row = await cur.fetchone()

                if bounce_row and bounce_row[0]:
                    paused = True
                    log.info(
                        "activation.bounce_pause",
                        campaign_id=str(campaign_id),
                        pushed=pushed,
                    )
                    break

    log.info(
        "activation.push_done",
        campaign_id=str(campaign_id),
        push_mode=push_mode,
        pushed=pushed,
        skipped=skipped,
        paused=paused,
    )
    return {"pushed": pushed, "skipped": skipped, "paused": paused}
