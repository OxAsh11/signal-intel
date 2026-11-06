"""Tiny HTTP health endpoint for Railway (GET /healthz). No framework needed."""

import asyncio
import json
import time
from dataclasses import dataclass, field

STARTUP_GRACE_S = 60.0
RELAY_STALE_S = 60.0


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


async def serve_health(state: HealthState, port: int) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode(
                errors="ignore"
            )
            path = request_line.split(" ")[1] if " " in request_line else ""
            if path.split("?")[0] == "/healthz":
                healthy, body = state.report()
                status = "200 OK" if healthy else "503 Service Unavailable"
            else:
                status, body = "404 Not Found", {"error": "not found"}
            payload = json.dumps(body).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host="0.0.0.0", port=port)
