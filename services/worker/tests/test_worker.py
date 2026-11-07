import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import procrastinate
import pytest
from procrastinate.testing import InMemoryConnector

from signal_core.events import DISPATCH_TASK
from signal_core.metering import ApprovedJob, MemoryApprovalStore
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


def make_app(
    health: FakeHealth | None = None,
    configured: dict[str, bool] | None = None,
    approvals: Any = None,
    connector: InMemoryConnector | None = None,
) -> procrastinate.App:
    flags = configured if configured is not None else {"chat": True, "embed": True}
    app = build_app(
        connector or InMemoryConnector(),
        AllEnabled(),
        HealthState(),
        health or FakeHealth(),
        ai_configured=lambda: flags,
        approvals=approvals or MemoryApprovalStore(),
    )

    @app.task(name="test.work", queue="default")
    async def work(**kwargs: Any) -> None:
        return None

    return app


def test_app_registers_phase0_jobs() -> None:
    app = make_app()
    assert {"system.heartbeat", "system.canary", DISPATCH_TASK} <= set(app.tasks)
    assert {"system.ai_status", "budget.resume"} <= set(app.tasks)
    assert isinstance(app, procrastinate.App)


async def test_heartbeat_job_updates_health() -> None:
    health = FakeHealth()
    state = HealthState()
    app = build_app(
        InMemoryConnector(),
        AllEnabled(),
        state,
        health,
        ai_configured=lambda: {"chat": True, "embed": True},
        approvals=MemoryApprovalStore(),
    )
    async with app.open_async():
        await app.tasks["system.heartbeat"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert state.last_heartbeat_at is not None
    # The periodic scheduler may add its own run; every record must be a healthy heartbeat.
    beats = [r for r in health.records if r[0] == "system.heartbeat"]
    assert beats
    assert set(beats) == {("system.heartbeat", "ok", "S1")}


async def test_canary_job_runs_the_pipeline_check() -> None:
    health = FakeHealth()
    app = make_app(health)
    async with app.open_async():
        await app.tasks["system.canary"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert health.canary_runs == 1


async def test_failed_canary_does_not_crash_the_worker() -> None:
    health = FakeHealth({"status": "incident", "error": "claim insert failed"})
    app = make_app(health)
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


class DetailHealth(FakeHealth):
    def __init__(self) -> None:
        super().__init__()
        self.details: list[tuple[str, str, dict[str, Any]]] = []

    async def record(
        self, component: str, status: Any, detail: dict[str, Any], owner: str = "M17"
    ) -> None:
        self.details.append((component, status, detail))


async def test_ai_status_degraded_without_keys() -> None:
    health = DetailHealth()
    app = make_app(health, configured={"chat": False, "embed": True})
    async with app.open_async():
        await app.tasks["system.ai_status"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert ("ai_gateway", "degraded", {"chat": False, "embed": True}) in health.details


async def test_ai_status_ok_with_both_keys() -> None:
    health = DetailHealth()
    app = make_app(health)
    async with app.open_async():
        await app.tasks["system.ai_status"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert ("ai_gateway", "ok", {"chat": True, "embed": True}) in health.details


def approved(job_type: str = "test.work") -> ApprovedJob:
    return ApprovedJob(id=uuid4(), job_type=job_type, job_args={"x": 1})


async def tick(app: procrastinate.App) -> None:
    await app.tasks["budget.resume"].defer_async(timestamp=0)
    await run_system_queue(app)


def work_jobs(connector: InMemoryConnector) -> list[dict[str, Any]]:
    return [j for j in connector.jobs.values() if j["task_name"] == "test.work"]


async def test_budget_resume_defers_once() -> None:
    job_ = approved()
    store = MemoryApprovalStore([job_])
    connector = InMemoryConnector()
    app = make_app(approvals=store, connector=connector)
    async with app.open_async():
        await tick(app)
        await tick(app)
    jobs = work_jobs(connector)
    assert len(jobs) == 1
    assert jobs[0]["args"] == {"x": 1, "approval_id": str(job_.id)}
    assert jobs[0]["queueing_lock"] == str(job_.id)
    assert store.resumed == {job_.id}


class ForgetfulStore(MemoryApprovalStore):
    async def mark_resumed(self, approval_id: UUID) -> None:
        return None  # simulates a crash between deferral and marking


async def test_budget_resume_survives_missed_mark() -> None:
    connector = InMemoryConnector()
    app = make_app(approvals=ForgetfulStore([approved()]), connector=connector)
    async with app.open_async():
        await tick(app)
        await tick(app)
    assert len(work_jobs(connector)) == 1  # queueing lock stops a second deferral


async def test_budget_resume_unknown_job_type_is_marked_resumed() -> None:
    job_ = approved("no.such.task")
    store = MemoryApprovalStore([job_])
    connector = InMemoryConnector()
    app = make_app(approvals=store, connector=connector)
    async with app.open_async():
        await tick(app)
    assert store.resumed == {job_.id}
    assert not [j for j in connector.jobs.values() if j["task_name"] == "no.such.task"]


async def test_approved_job_not_paused_again() -> None:
    from decimal import Decimal

    from signal_core.metering import BudgetPaused, CallContext, MemoryBudgetGuard

    ws = uuid4()
    guard = MemoryBudgetGuard()
    guard.set_cap(ws, None, "day", Decimal("1"))
    ctx = CallContext(uuid4(), ws, None, "j", "test.work", {})
    with pytest.raises(BudgetPaused) as paused:
        await guard.reserve(ctx, Decimal("2"))
    guard.approve(paused.value.approval_id)
    resumed = CallContext(
        uuid4(), ws, None, "j", "test.work", {"approval_id": str(paused.value.approval_id)}
    )
    assert await guard.reserve(resumed, Decimal("2"))
