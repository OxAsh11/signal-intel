"""M10 Inbox — reply classification and the reply list.

classify_reply       store a classification. app.classify_reply does it in one transaction: the
                     latest human mark wins, a human mark is recorded as feedback, an Unsubscribe
                     is suppressed immediately, and reply.classified is emitted.
auto_classify_reply  classify a new reply when no human has: the sequencer's own signal if the
                     event carried one, otherwise the AI gateway over the reply text.
get_inbox            replies for a workspace, with an overdue flag for unclassified ones.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

ReplyClass = Literal[
    "Interested", "Referral", "Not now", "Not interested", "Out of office", "Unsubscribe"
]
VALID_REPLY_CLASSES = frozenset(
    ["Interested", "Referral", "Not now", "Not interested", "Out of office", "Unsubscribe"]
)
GENUINE_REPLY_CLASSES = frozenset(["Interested", "Referral"])

# Two working days, approximated as two calendar days.
_OVERDUE_DAYS = 2
_MAX_REPLY_CHARS = 6000


class ReplyClassOutput(BaseModel):
    reply_class: ReplyClass


async def classify_reply(
    pool: AsyncConnectionPool,
    activity_id: UUID,
    reply_class: str,
    member_id: UUID | None,
    source: Literal["human", "ai", "sequencer"],
) -> bool:
    """Classify a reply. Returns False when a human mark already exists and the caller is not
    human (the human mark stands)."""
    if reply_class not in VALID_REPLY_CLASSES:
        raise ValueError(
            f"reply_class {reply_class!r} is not valid. Valid values: {sorted(VALID_REPLY_CLASSES)}"
        )
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.classify_reply(%s, %s::public.reply_class, %s, %s)",
            (str(activity_id), reply_class, source, str(member_id) if member_id else None),
        )
        row = await cur.fetchone()
    applied = bool(row[0]) if row else False
    log.info(
        "inbox.classified" if applied else "inbox.classify_skipped_human_wins",
        activity_id=str(activity_id),
        reply_class=reply_class,
        source=source,
    )
    return applied


async def auto_classify_reply(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    activity_id: UUID,
    ctx: CallContext,
) -> str | None:
    """Classify a reply that has no class yet. Returns the class applied, or None."""
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT payload FROM public.activity WHERE id = %s AND kind = 'reply_received'",
            (str(activity_id),),
        )
        row = await cur.fetchone()
    if row is None:
        log.warning("inbox.activity_not_found", activity_id=str(activity_id))
        return None

    payload: dict[str, Any] = json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
    if payload.get("reply_class"):
        return None

    sequencer_class = payload.get("sequencer_reply_class")
    if sequencer_class in VALID_REPLY_CLASSES:
        await classify_reply(pool, activity_id, str(sequencer_class), None, "sequencer")
        return str(sequencer_class)

    text = str(payload.get("text") or "").strip()
    if not text:
        return None

    result = await gateway.run(
        Capability.PREJUDGE,
        load_prompt("reply_classify", 1),
        {"reply_text": text[:_MAX_REPLY_CHARS]},
        ReplyClassOutput,
        ctx,
        max_tokens=100,
    )
    reply_class = result.value.reply_class
    applied = await classify_reply(pool, activity_id, reply_class, None, "ai")
    return reply_class if applied else None


async def get_inbox(
    pool: AsyncConnectionPool,
    workspace_id: UUID,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Replies for a workspace, newest first.

    filters: unclassified_only (bool), min_age_days (int), limit (int, default 500).
    Each row carries is_overdue: unclassified and older than two working days (review queue).
    """
    filters = filters or {}
    conditions = ["workspace_id = %s", "kind = 'reply_received'"]
    params: list[Any] = [str(workspace_id)]

    if filters.get("unclassified_only"):
        conditions.append("(payload ->> 'reply_class') IS NULL")
    if filters.get("min_age_days") is not None:
        conditions.append("occurred_at < now() - make_interval(days => %s)")
        params.append(int(filters["min_age_days"]))
    params.append(int(filters.get("limit", 500)))

    where_clause = " AND ".join(conditions)
    sql = (
        "SELECT id, org_id, workspace_id, account_id, person_entity_id, kind, occurred_at, payload"
        f" FROM public.activity WHERE {where_clause}"
        " ORDER BY occurred_at DESC LIMIT %s"
    )
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()

    cutoff = datetime.now(UTC) - timedelta(days=_OVERDUE_DAYS)
    result: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row[7]) if isinstance(row[7], str) else (row[7] or {})
        occurred_at: datetime = row[6]
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
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
                "is_overdue": not payload.get("reply_class") and occurred_at < cutoff,
            }
        )
    return result
