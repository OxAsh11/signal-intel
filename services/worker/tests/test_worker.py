import asyncio
import json
from typing import Any

import procrastinate
from procrastinate.testing import InMemoryConnector

from signal_core.events import DISPATCH_TASK
from worker.app import build_app
from worker.health import STARTUP_GRACE_S, HealthState, serve_health


class AllEnabled:
    async def is_enabled(self, job_type: str) -> bool:
        return True


class FakeHealth:
    def __init__(self, canary_result: dict[str, Any] | None = None) -> None:
        self.records: list[tuple[str, str, str]] = []
        self.canary_runs = 0
        self.canary_result = canary_result or {"status": "ok", "verdict_id": "v1"}

    async def record(
        self, component: str, status: Any, detail: dict[str, Any], owner: str = "M17"
    ) -> None:
        self.records.append((component, status, owner))

    async def run_canary(self) -> dict[str, Any]:
        self.canary_runs += 1
        return self.canary_result


async def run_system_queue(app: procrastinate.App) -> None:
    await app.run_worker_async(queues=["system"], wait=False, install_signal_handlers=False)


def test_app_registers_phase0_jobs() -> None:
    app = build_app(InMemoryConnector(), AllEnabled(), HealthState(), FakeHealth())
    assert {"system.heartbeat", "system.canary", DISPATCH_TASK} <= set(app.tasks)
    assert isinstance(app, procrastinate.App)


async def test_heartbeat_job_updates_health() -> None:
    state, health = HealthState(), FakeHealth()
    app = build_app(InMemoryConnector(), AllEnabled(), state, health)
    async with app.open_async():
        await app.tasks["system.heartbeat"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert state.last_heartbeat_at is not None
    # The periodic scheduler may add its own run; every record must be a healthy heartbeat.
    assert health.records
    assert set(health.records) == {("system.heartbeat", "ok", "S1")}


async def test_canary_job_runs_the_pipeline_check() -> None:
    health = FakeHealth()
    app = build_app(InMemoryConnector(), AllEnabled(), HealthState(), health)
    async with app.open_async():
        await app.tasks["system.canary"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert health.canary_runs == 1


async def test_failed_canary_does_not_crash_the_worker() -> None:
    health = FakeHealth({"status": "incident", "error": "claim insert failed"})
    app = build_app(InMemoryConnector(), AllEnabled(), HealthState(), health)
    async with app.open_async():
        await app.tasks["system.canary"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert health.canary_runs == 1  # recorded as an incident in SQL; no retry storm


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
