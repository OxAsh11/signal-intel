"""M10 Inbox — reply classification with human-wins rule and unsubscribe handling.

Flow for classify_reply:
  1. Load the activity row from public.activity.
  2. If the row's payload["reply_class_source"] == "human" and source != "human",
     return immediately (human mark wins; AI/sequencer cannot override).
  3. Call app.patch_activity_reply_class RPC — SECURITY DEFINER, gates the
     activity_append_only trigger via a transaction-local set_config flag.
  4. If source == "human" and member_id is provided, insert a feedback row.
  5. If reply_class == "Unsubscribe", insert person_entity_id into suppression_list.
  6. Emit reply.classified via app.emit.

Flow for get_inbox:
  1. SELECT from public.activity WHERE workspace_id and kind = 'reply_received'.
  2. Apply optional filters (unclassified_only, min_age_days).
  3. Compute is_overdue = occurred_at < now() - 2 working days.
  4. Return list of dicts.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from signal_core.logging import get_logger

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_REPLY_CLASSES = frozenset(
    ["Interested", "Referral", "Not now", "Not interested", "Out of office", "Unsubscribe"]
)

# 2 working days ≈ 2 calendar days for simplicity; production can refine
# with a proper business-day calendar.
_OVERDUE_DAYS = 2


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def classify_reply(
    pool: object,
    activity_id: UUID,
    reply_class: str,
    member_id: UUID | None,
    source: Literal["human", "ai", "sequencer"],
) -> None:
    """Classify an inbound reply and persist the result.

    Parameters
    ----------
    pool:        async connection pool (psycopg3 AsyncConnectionPool).
    activity_id: UUID of the public.activity row (kind = 'reply_received').
    reply_class: one of the public.reply_class enum values.
    member_id:   required when source == "human"; identifies the team member.
    source:      "human" | "ai" | "sequencer".
    """
    if reply_class not in VALID_REPLY_CLASSES:
        raise ValueError(
            f"reply_class {reply_class!r} is not a valid public.reply_class value. "
            f"Valid values: {sorted(VALID_REPLY_CLASSES)}"
        )

    # 1. Load the activity row.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, org_id, workspace_id, person_entity_id, payload"
                " FROM public.activity"
                " WHERE id = %s",
                (str(activity_id),),
            )
            row = await cur.fetchone()

    if row is None:
        log.warning("inbox.activity_not_found", activity_id=str(activity_id))
        return

    _act_id, org_id_raw, workspace_id_raw, person_entity_id_raw, payload = row
    org_id: UUID = UUID(str(org_id_raw))
    workspace_id: UUID = UUID(str(workspace_id_raw))
    person_entity_id: UUID | None = (
        UUID(str(person_entity_id_raw)) if person_entity_id_raw is not None else None
    )

    # Normalise payload: psycopg3 may return a dict or a JSON string.
    if isinstance(payload, str):
        payload = json.loads(payload)

    # 2. Human-wins guard: if already classified by human, skip for non-human callers.
    if payload.get("reply_class_source") == "human" and source != "human":
        log.info(
            "inbox.classify_skipped_human_wins",
            activity_id=str(activity_id),
            source=source,
            existing_class=payload.get("reply_class"),
        )
        return

    # 3. Patch activity.payload via SECURITY DEFINER RPC.
    #    app.patch_activity_reply_class gates the activity_append_only trigger
    #    using a transaction-local set_config flag (migration 0024).
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "SELECT app.patch_activity_reply_class(%s, %s::public.reply_class, %s)",
            (str(activity_id), reply_class, source),
        )

    log.info(
        "inbox.classified",
        activity_id=str(activity_id),
        reply_class=reply_class,
        source=source,
    )

    # 4. If source == "human", insert a feedback row via SECURITY DEFINER RPC (S8).
    if source == "human" and member_id is not None:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "SELECT app.record_feedback(%s, %s, %s, %s, %s, %s, %s::jsonb)",
                (
                    str(org_id),
                    str(workspace_id),
                    str(member_id),
                    "reply_class",
                    str(activity_id),
                    "correct",
                    json.dumps(reply_class),
                ),
            )

    # 5. Unsubscribe: add to suppression_list via SECURITY DEFINER RPC (S8).
    if reply_class == "Unsubscribe" and person_entity_id is not None:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            await conn.execute(
                "SELECT app.add_to_suppression_list(%s, %s, %s)",
                (str(org_id), str(person_entity_id), "unsubscribe"),
            )
        log.info(
            "inbox.suppressed",
            org_id=str(org_id),
            person_entity_id=str(person_entity_id),
        )

    # 6. Emit reply.classified event via outbox.
    async with pool.connection() as conn:  # type: ignore[union-attr]
        await conn.execute(
            "SELECT app.emit(%s::public.event_type, %s, %s, 'activity', %s, %s::jsonb)",
            (
                "reply.classified",
                str(org_id),
                str(workspace_id),
                str(activity_id),
                json.dumps(
                    {
                        "reply_class": reply_class,
                        "reply_class_source": source,
                    }
                ),
            ),
        )


async def get_inbox(
    pool: object,
    workspace_id: UUID,
    filters: dict,
) -> list[dict]:
    """Return activity rows of kind 'reply_received' for the given workspace.

    Parameters
    ----------
    pool:         async connection pool.
    workspace_id: scope the query to this workspace.
    filters:      optional dict with keys:
                    - unclassified_only (bool): skip rows that already have reply_class
                    - min_age_days (int):       only rows older than N days

    Returns a list of dicts with an extra ``is_overdue`` field (True when
    occurred_at is more than 2 working days old without classification).
    """
    unclassified_only: bool = filters.get("unclassified_only", False)
    min_age_days: int | None = filters.get("min_age_days")

    conditions = ["workspace_id = %s", "kind = 'reply_received'"]
    params: list = [str(workspace_id)]

    if unclassified_only:
        conditions.append("(payload ->> 'reply_class') IS NULL")

    if min_age_days is not None:
        conditions.append("occurred_at < now() - %s * interval '1 day'")
        params.append(min_age_days)

    where_clause = " AND ".join(conditions)
    sql = (
        "SELECT id, org_id, workspace_id, account_id, person_entity_id,"
        "       kind, occurred_at, payload"
        f" FROM public.activity"
        f" WHERE {where_clause}"
        f" ORDER BY occurred_at DESC"
    )

    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    cutoff = datetime.now(timezone.utc) - timedelta(days=_OVERDUE_DAYS)
    result: list[dict] = []

    for row in rows:
        payload = row[7]
        if isinstance(payload, str):
            payload = json.loads(payload)

        occurred_at: datetime = row[6]
        # Make timezone-aware if needed.
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)

        is_classified = bool(payload.get("reply_class"))
        is_overdue = (not is_classified) and (occurred_at < cutoff)

        result.append(
            {
                "id": row[0],
                "org_id": row[1],
                "workspace_id": row[2],
                "account_id": row[3],
                "person_entity_id": row[4],
                "kind": row[5],
                "occurred_at": occurred_at,
                "payload": payload,
                "is_overdue": is_overdue,
            }
        )

    return result
