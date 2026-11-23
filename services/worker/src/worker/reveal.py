"""Small HTTP endpoint that reveals one contact value on behalf of the web app.

The database deliberately stores only Fernet ciphertext and a masked hint, and the Fernet key
lives in the worker environment. `public.person_email` (migration 0044) returns the contact-point
id and mask to Editor+; the web app then asks this endpoint for the full value. The shared token
is the same one Task 7 uses for provider status, so the port stays closed until it is configured.
No email appears in logs: this module only moves one value from the database to the caller.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg_pool import AsyncConnectionPool

from signal_core.crypto import SecretsNotConfigured, decrypt_secret
from signal_core.logging import get_logger
from worker.health import PROVIDER_STATUS_PATH, handle_provider_status

log = get_logger("worker.reveal")

REVEAL_PATH = "/contacts/reveal"
Lookup = Callable[[str, str], Awaitable[str | None]]


def pool_lookup(pool: AsyncConnectionPool) -> Lookup:
    """Look up one email by contact-point id, scoped to the workspace that asked for it."""

    async def lookup(workspace_id: str, contact_id: str) -> str | None:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select value_encrypted from public.contact_point"
                " where id = %s and workspace_id = %s and type = 'email'",
                (contact_id, workspace_id),
            )
            row = await cur.fetchone()
        if not row or not row[0]:
            return None
        try:
            return decrypt_secret(str(row[0]))
        except SecretsNotConfigured:
            log.warning("reveal.no_key")
            return None

    return lookup


async def handle_reveal(
    lookup: Lookup,
    token: str | None,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
) -> tuple[int, dict[str, Any]]:
    """One request, without the socket. Kept separate so the rules are easy to test."""
    route = path.split("?")[0]
    # The web uses one worker URL for both small endpoints; status lives on the health server and
    # is mirrored here behind the same token so a single Railway hostname is enough.
    if route == PROVIDER_STATUS_PATH:
        return handle_provider_status(token, method, path, headers)
    if route != REVEAL_PATH:
        return 404, {"error": "not found"}
    if method != "POST":
        return 405, {"error": "POST required"}
    if not token:
        return 503, {"error": "reveal not configured"}
    given = headers.get("authorization", "")
    if not hmac.compare_digest(given, f"Bearer {token}"):
        return 401, {"error": "unauthorised"}
    try:
        payload = json.loads(body or b"{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {"error": "invalid request"}
    if not isinstance(payload, dict):
        return 400, {"error": "invalid request"}
    workspace_id = payload.get("workspace_id")
    contact_id = payload.get("contact_point_id")
    if not all(isinstance(v, str) for v in (workspace_id, contact_id)):
        return 400, {"error": "invalid request"}
    try:
        uuid.UUID(str(workspace_id))
        uuid.UUID(str(contact_id))
    except ValueError:
        return 400, {"error": "invalid request"}
    email = await lookup(str(workspace_id), str(contact_id))
    if not email:
        return 404, {"error": "contact not found"}
    return 200, {"email": email}


async def serve_reveal(lookup: Lookup, token: str | None, port: int) -> asyncio.Server:
    """Serve POST /contacts/reveal on its own port, beside the health endpoint."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(errors="ignore")
            parts = request.split()
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else ""
            headers: dict[str, str] = {}
            while True:
                line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(
                    errors="ignore"
                )
                if line in ("\r\n", "\n", ""):
                    break
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            length = int(headers.get("content-length", "0") or 0)
            if length < 0 or length > 4096:
                status, payload = 413, {"error": "request too large"}
            else:
                body = (
                    await asyncio.wait_for(reader.readexactly(length), timeout=5) if length else b""
                )
                status, payload = await handle_reveal(lookup, token, method, path, headers, body)
            raw = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode()
                + raw
            )
            await writer.drain()
        except (TimeoutError, ConnectionError, ValueError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host="127.0.0.1", port=port)
