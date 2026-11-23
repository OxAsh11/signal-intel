"""Tiny HTTP health endpoint for Railway (GET /healthz) and provider status.

`/providers/status` is the same token-protected contract as contact reveal: it gives the web app
`providers.status()` (which states only whether a key exists, never the key itself). The port stays
public only for /healthz; every other route needs the shared WORKER_STATUS_TOKEN.
"""

import asyncio
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from signal_core.providers import status as provider_status

STARTUP_GRACE_S = 60.0
RELAY_STALE_S = 60.0
PROVIDER_STATUS_PATH = "/providers/status"

StatusFn = Callable[[], list[Any]]


def _default_status() -> list[Any]:
    return provider_status()


def _json_row(row: Any) -> dict[str, Any]:
    """Timestamps leave as ISO strings; everything else stays exactly as the registry shaped it."""
    out = dict(row)
    at = out.get("last_error_at")
    if isinstance(at, datetime):
        out["last_error_at"] = at.isoformat()
    return out


def handle_provider_status(
    token: str | None,
    method: str,
    path: str,
    headers: dict[str, str],
    status: StatusFn = _default_status,
) -> tuple[int, dict[str, Any]]:
    """One request, without the socket, so the auth and redaction rules are easy to test."""
    if path.split("?")[0] != PROVIDER_STATUS_PATH:
        return 404, {"error": "not found"}
    if method != "GET":
        return 405, {"error": "GET required"}
    if not token:
        return 503, {"error": "provider status not configured"}
    given = headers.get("authorization", "")
    if not hmac.compare_digest(given, f"Bearer {token}"):
        return 401, {"error": "unauthorised"}
    return 200, {"providers": [_json_row(r) for r in status()]}


@dataclass
class HealthState:
    started_at: float = field(default_factory=time.monotonic)
    last_relay_at: float | None = None
    last_heartbeat_at: float | None = None

    def mark_relay(self) -> None:
        self.last_relay_at = time.monotonic()

    def mark_heartbeat(self) -> None:
        self.last_heartbeat_at = time.monotonic()

    def report(self, now: float | None = None) -> tuple[bool, dict[str, object]]:
        now = time.monotonic() if now is None else now
        relay_age = None if self.last_relay_at is None else now - self.last_relay_at
        in_grace = now - self.started_at < STARTUP_GRACE_S
        healthy = in_grace or (relay_age is not None and relay_age < RELAY_STALE_S)
        return healthy, {
            "status": "ok" if healthy else "stale",
            "uptime_s": round(now - self.started_at),
            "relay_age_s": None if relay_age is None else round(relay_age, 1),
            "heartbeat_age_s": None
            if self.last_heartbeat_at is None
            else round(now - self.last_heartbeat_at, 1),
        }


async def serve_health(
    state: HealthState,
    port: int,
    token: str | None = None,
    status: StatusFn | None = None,
) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(
                errors="ignore"
            )
            parts = request_line.split()
            method = parts[0] if parts else ""
            path = parts[1] if len(parts) > 1 else ""
            headers: dict[str, str] = {}
            while path.split("?")[0] == PROVIDER_STATUS_PATH:
                line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(
                    errors="ignore"
                )
                if line in ("\r\n", "\n", ""):
                    break
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
            if path.split("?")[0] == "/healthz":
                healthy, body = state.report()
                response = "200 OK" if healthy else "503 Service Unavailable"
            else:
                code, body = handle_provider_status(
                    token, method, path, headers, status or _default_status
                )
                response = {
                    200: "200 OK",
                    401: "401 Unauthorized",
                    404: "404 Not Found",
                    405: "405 Method Not Allowed",
                    503: "503 Service Unavailable",
                }[code]
            payload = json.dumps(body).encode()
            writer.write(
                f"HTTP/1.1 {response}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (TimeoutError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host="0.0.0.0", port=port)
