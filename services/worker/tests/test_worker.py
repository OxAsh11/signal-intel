import asyncio
import json

import procrastinate
from procrastinate.testing import InMemoryConnector

from signal_core.events import DISPATCH_TASK
from worker.app import build_app
from worker.health import STARTUP_GRACE_S, HealthState, serve_health


class AllEnabled:
    async def is_enabled(self, job_type: str) -> bool:
        return True


def test_app_registers_phase0_jobs() -> None:
    app = build_app(InMemoryConnector(), AllEnabled(), HealthState())
    assert {"system.heartbeat", DISPATCH_TASK} <= set(app.tasks)
    assert isinstance(app, procrastinate.App)


async def test_heartbeat_job_updates_health() -> None:
    state = HealthState()
    app = build_app(InMemoryConnector(), AllEnabled(), state)
    async with app.open_async():
        await app.tasks["system.heartbeat"].defer_async(timestamp=0)
        await app.run_worker_async(queues=["system"], wait=False, install_signal_handlers=False)
    assert state.last_heartbeat_at is not None


def test_health_goes_stale_without_relay() -> None:
    state = HealthState()
    healthy, _ = state.report(now=state.started_at + 1)
    assert healthy  # within startup grace
    healthy, body = state.report(now=state.started_at + STARTUP_GRACE_S + 5)
    assert not healthy and body["status"] == "stale"
    state.last_relay_at = state.started_at + STARTUP_GRACE_S + 4
    healthy, _ = state.report(now=state.started_at + STARTUP_GRACE_S + 5)
    assert healthy


async def test_healthz_endpoint() -> None:
    server = await serve_health(HealthState(), port=0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /healthz HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    raw = await reader.read()
    writer.close()
    server.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    assert json.loads(body)["status"] == "ok"
