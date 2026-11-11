"""Inbound webhook ingestion — POST /v1/webhooks/{integration_id}.

Validates HMAC-SHA256 signature, deduplicates by (external_id, integration_id),
maps event type to activity kind, and writes via app.create_activity RPC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Map inbound sequencer event types to activity.kind values.
_EVENT_KIND_MAP: dict[str, str] = {
    "email_replied": "reply_received",
    "email_bounced": "message_bounced",
    "email_unsubscribed": "unsubscribed",
}


@router.post("/{integration_id}")
async def ingest_webhook(integration_id: UUID, request: Request) -> dict[str, Any]:
    """Ingest an inbound webhook event from an external integration.

    Steps:
    1. Read raw body bytes.
    2. Load integration row (org_id, workspace_id, credentials_vault_key).
    3. Validate X-Signal-Signature header with HMAC-SHA256.
    4. Deduplicate by (external_id, integration_id); return 200 if duplicate.
    5. Map event type → activity kind.
    6. Look up account_id via account.external_id.
    7. Write activity via app.create_activity RPC.
    """
    # Step 1: read raw body bytes BEFORE entering pool context
    body_bytes = await request.body()

    pool = request.app.state.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # Step 2: load integration row
            await cur.execute(
                "SELECT org_id, workspace_id, credentials_vault_key"
                " FROM public.integration WHERE id = %s",
                (str(integration_id),),
            )
            integration_row = await cur.fetchone()
            if integration_row is None:
                raise HTTPException(status_code=404, detail="Integration not found")

            org_id, workspace_id, secret = integration_row

            # Step 3: validate HMAC-SHA256 signature
            sig_header = request.headers.get("X-Signal-Signature", "")
            if not sig_header.startswith("sha256="):
                raise HTTPException(status_code=401, detail="Missing or malformed signature")

            provided_sig = sig_header[len("sha256="):]
            expected_sig = hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected_sig, provided_sig):
                raise HTTPException(status_code=401, detail="Invalid signature")

            # Step 4: parse body as JSON
            try:
                event: dict[str, Any] = json.loads(body_bytes)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

            external_id: str | None = event.get("external_id")
            if not external_id:
                raise HTTPException(status_code=400, detail="external_id is required")

            # Step 5: deduplication — return 200 immediately if already stored
            await cur.execute(
                "SELECT id FROM public.activity"
                " WHERE external_id = %s AND integration_id = %s",
                (external_id, str(integration_id)),
            )
            existing = await cur.fetchone()
            if existing is not None:
                return {"ok": True}

            # Step 6: map event type to activity kind
            event_type: str = event.get("type", "")
            kind = _EVENT_KIND_MAP.get(event_type)
            if kind is None:
                logger.warning(
                    "Unknown webhook event type %r for integration %s — storing as note_added",
                    event_type,
                    integration_id,
                )
                kind = "note_added"

            # Resolve occurred_at: use event value or current UTC timestamp
            occurred_at: str | None = event.get("occurred_at")
            if not occurred_at:
                occurred_at = datetime.now(timezone.utc).isoformat()

            # Step 7: look up account_id by external_id
            account_external_id: str | None = event.get("account_external_id")
            account_id: str | None = None
            if account_external_id:
                await cur.execute(
                    "SELECT id FROM public.account"
                    " WHERE external_id = %s AND org_id = %s",
                    (account_external_id, str(org_id)),
                )
                account_row = await cur.fetchone()
                if account_row is not None:
                    account_id = str(account_row[0])

            # Step 8: write activity via SECURITY DEFINER RPC
            await cur.execute(
                "SELECT app.create_activity(%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(org_id),
                    str(workspace_id),
                    account_id,
                    kind,
                    occurred_at,
                    str(integration_id),
                    external_id,
                ),
            )

    return {"ok": True}
