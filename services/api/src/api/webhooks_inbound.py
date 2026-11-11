"""Inbound webhook ingestion — POST /v1/webhooks/{integration_id}.

A connected sequencer (or the customer's automation) posts engagement events here. Each request
is signed with the integration's secret (HMAC-SHA256 over the raw body, header
X-Signal-Signature: sha256=<hex>). The event becomes one activity row through app.create_activity,
which de-duplicates on (integration, external_id) and applies the side effects: bounce counters
and the bounce guard, suppression on unsubscribe, reply.received for replies.

Event body:
  external_id        required; the sender's id for this event (replays are recorded once)
  type               email_sent | email_replied | email_bounced | email_unsubscribed |
                     email_auto_reply | meeting_booked | inbound_contact
  occurred_at        ISO-8601; defaults to now
  account_id         the account id passed to the sequencer as a custom field
  person_entity_id   the person id passed to the sequencer as a custom field
  campaign_id        the campaign id passed to the sequencer as a custom field
  text, subject      reply content (used to classify the reply)
  reply_class        the sequencer's own classification, if it has one
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, HTTPException, Request

from signal_core.crypto import SecretsNotConfigured, decrypt_secret
from signal_core.metering import CallContext
from signal_core.modules.m11_stage import advance_stage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_EVENT_KIND_MAP: dict[str, str] = {
    "email_sent": "message_sent",
    "email_replied": "reply_received",
    "email_bounced": "message_bounced",
    "email_unsubscribed": "unsubscribed",
    "email_auto_reply": "auto_reply",
    "meeting_booked": "meeting_scheduled",
    "inbound_contact": "inbound_contact",
}
# Activity kinds that move the stage as soon as they are logged (M11). A reply moves it only
# once classified as genuine, and a meeting only once held with proof.
_STAGE_KINDS = frozenset({"message_sent", "inbound_contact"})
_MAX_TEXT_CHARS = 20_000
# SQLSTATEs the write path raises for a bad reference in the event (not our fault: 422).
_REJECTED_STATES = frozenset({"42501", "22023", "22P02", "23503"})


def _uuid_or_none(value: Any, field: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{field} is not a valid id") from exc


def _occurred_at(value: Any) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="occurred_at is not ISO-8601") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@router.post("/{integration_id}")
async def ingest_webhook(integration_id: UUID, request: Request) -> dict[str, Any]:
    body_bytes = await request.body()
    pool = request.app.state.pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Service unavailable")

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT org_id, workspace_id, credentials_vault_key, enabled"
            " FROM public.integration WHERE id = %s",
            (str(integration_id),),
        )
        integration = await cur.fetchone()
    if integration is None or not integration[3]:
        raise HTTPException(status_code=404, detail="Integration not found")
    org_id, workspace_id, stored_secret = integration[0], integration[1], integration[2]
    if not stored_secret:
        raise HTTPException(status_code=409, detail="Integration has no signing secret")

    sig_header = request.headers.get("X-Signal-Signature", "")
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")
    try:
        secret = decrypt_secret(str(stored_secret))
    except (SecretsNotConfigured, ValueError) as exc:
        logger.error("inbound webhook secret unavailable for integration %s", integration_id)
        raise HTTPException(status_code=503, detail="Service unavailable") from exc
    expected = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header[len("sha256=") :]):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        event = json.loads(body_bytes)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")

    external_id = event.get("external_id")
    if not isinstance(external_id, str) or not external_id:
        raise HTTPException(status_code=400, detail="external_id is required")

    kind = _EVENT_KIND_MAP.get(str(event.get("type", "")))
    if kind is None:
        logger.warning(
            "ignored webhook event type %r for integration %s", event.get("type"), integration_id
        )
        return {"ok": True, "ignored": True}

    account_id = _uuid_or_none(event.get("account_id"), "account_id")
    payload = {
        key: value
        for key, value in {
            "subject": event.get("subject"),
            "text": str(event["text"])[:_MAX_TEXT_CHARS] if event.get("text") else None,
            "sequencer_reply_class": event.get("reply_class"),
        }.items()
        if value is not None
    }

    try:
        async with pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "SELECT app.create_activity(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    str(org_id),
                    str(workspace_id),
                    account_id,
                    kind,
                    _occurred_at(event.get("occurred_at")),
                    str(integration_id),
                    external_id,
                    _uuid_or_none(event.get("person_entity_id"), "person_entity_id"),
                    _uuid_or_none(event.get("campaign_id"), "campaign_id"),
                    json.dumps(payload),
                    "email" if kind != "meeting_scheduled" else "meeting",
                ),
            )
            row = await cur.fetchone()
    except psycopg.Error as exc:
        if exc.sqlstate in _REJECTED_STATES:
            raise HTTPException(
                status_code=422, detail="Event references an unknown record"
            ) from exc
        raise
    activity_id = str(row[0]) if row else None

    if account_id and activity_id and kind in _STAGE_KINDS:
        ctx = CallContext(UUID(str(org_id)), UUID(str(workspace_id)), None, None, "api.webhook", {})
        await advance_stage(
            pool, UUID(account_id), UUID(activity_id), kind, UUID(str(workspace_id)), ctx
        )

    return {"ok": True, "activity_id": activity_id}
