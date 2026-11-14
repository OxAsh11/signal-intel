import asyncio
import json
from typing import Any
from unittest.mock import MagicMock
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
    assert {"system.ai_status", "budget.resume", "budget.release_stale"} <= set(app.tasks)
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


async def test_resume_claims_each_approval_once() -> None:
    store = MemoryApprovalStore([approved()])
    first, second = await asyncio.gather(store.claim_approved(10), store.claim_approved(10))
    assert len(first) + len(second) == 1


async def test_resume_not_repeated_after_job_has_started() -> None:
    connector = InMemoryConnector()
    app = make_app(approvals=MemoryApprovalStore([approved()]), connector=connector)
    async with app.open_async():
        await tick(app)
        (j,) = work_jobs(connector)
        j["status"] = "doing"  # the worker picked it up: no longer 'todo', lock is free
        await tick(app)
    assert len(work_jobs(connector)) == 1


async def test_failed_defer_is_unclaimed_and_retried() -> None:
    connector = InMemoryConnector()
    app = make_app(approvals=MemoryApprovalStore([approved()]), connector=connector)
    real = connector.defer_jobs_all
    failed = {"done": False}

    async def flaky(jobs: Any) -> Any:
        if not failed["done"] and any(j.task_name == "test.work" for j in jobs):
            failed["done"] = True
            raise RuntimeError("db down")
        return await real(jobs)

    connector.defer_jobs_all = flaky  # type: ignore[method-assign]
    async with app.open_async():
        await tick(app)
        await tick(app)
    assert failed["done"]  # the first deferral really failed
    assert len(work_jobs(connector)) == 1  # then the retry deferred it exactly once


async def test_budget_resume_unknown_job_type_is_not_deferred() -> None:
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


async def test_release_stale_cron_is_registered_and_calls_the_store() -> None:
    store = MemoryApprovalStore()
    app = make_app(approvals=store)
    periodic = [t for t in app.periodic_registry.periodic_tasks.values()]
    assert any(t.task.name == "budget.release_stale" and t.cron == "*/5 * * * *" for t in periodic)
    assert app.tasks["budget.release_stale"].queue == "system"
    async with app.open_async():
        await app.tasks["budget.release_stale"].defer_async(timestamp=0)
        await run_system_queue(app)
    assert store.release_stale_calls >= 1


class FlakyUnclaimStore(MemoryApprovalStore):
    def __init__(self, approvals: list[ApprovedJob]) -> None:
        super().__init__(approvals)
        self.unclaim_attempts: list[UUID] = []

    async def unclaim(self, approval_id: UUID) -> None:
        self.unclaim_attempts.append(approval_id)
        raise RuntimeError("db down")


@pytest.mark.parametrize(
    "connector_type,expected_job",
    [
        ("news", "source.fetch_news"),
        ("website", "source.fetch_website"),
        ("csv_upload", "source.fetch_csv"),
        ("job_post", "source.fetch_job_post"),
    ],
)
async def test_dispatch_routes_radar_fetch_queued_to_correct_job(
    connector_type: str, expected_job: str
) -> None:
    connector = InMemoryConnector()
    app = make_app(connector=connector)
    radar_id = str(uuid4())
    async with app.open_async():
        await app.tasks[DISPATCH_TASK].defer_async(
            event="radar.fetch_queued",
            event_id=str(uuid4()),
            payload={
                "radar_id": radar_id,
                "org_id": str(uuid4()),
                "workspace_id": str(uuid4()),
                "brief_id": str(uuid4()),
                "connector_type": connector_type,
            },
        )
        await app.run_worker_async(queues=["events"], wait=False, install_signal_handlers=False)
    deferred_names = [j["task_name"] for j in connector.jobs.values()]
    assert expected_job in deferred_names, (
        f"Expected {expected_job!r} to be deferred for connector_type={connector_type!r}, "
        f"got: {deferred_names}"
    )


async def test_dispatch_brief_version_published_queues_backfill() -> None:
    """brief.version_published defers extract.backfill with workspace_id read from DB."""
    brief_id = str(uuid4())
    workspace_id = str(uuid4())
    version = 2
    version_id = str(uuid4())

    # Minimal fake pool: routes SQL to canned rows.
    class _FCursor:
        def __init__(self, rows: list) -> None:
            self._rows = rows

        async def execute(self, sql: str, args: Any = None) -> None:
            pass

        async def fetchone(self) -> tuple | None:
            return self._rows[0] if self._rows else None

        async def fetchall(self) -> list:
            return self._rows

        async def __aenter__(self) -> "_FCursor":
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    class _FConn:
        """Routes queries to canned rows based on SQL text."""

        def __init__(self, ws: str) -> None:
            self._ws = ws

        def cursor(self) -> _FCursor:
            # Called for the brief-table cursor query — returns workspace_id.
            return _FCursor([(self._ws,)])

        async def execute(self, sql: str, args: Any = None) -> _FCursor:
            # Called for the radar execute query — no active radars in this test.
            return _FCursor([])

        async def __aenter__(self) -> "_FConn":
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    class _FConnCtx:
        def __init__(self, conn: _FConn) -> None:
            self._conn = conn

        async def __aenter__(self) -> _FConn:
            return self._conn

        async def __aexit__(self, *_: Any) -> None:
            pass

    class _FPool:
        def connection(self) -> _FConnCtx:
            return _FConnCtx(_FConn(workspace_id))

    connector = InMemoryConnector()
    app = build_app(
        connector,
        AllEnabled(),
        HealthState(),
        FakeHealth(),
        ai_configured=lambda: {"chat": True, "embed": True},
        approvals=MemoryApprovalStore(),
        pool=_FPool(),  # type: ignore[arg-type]
    )
    async with app.open_async():
        await app.tasks[DISPATCH_TASK].defer_async(
            event="brief.version_published",
            event_id=str(uuid4()),
            payload={
                "brief_id": brief_id,
                "version": version,
                "brief_version_id": version_id,
            },
        )
        await app.run_worker_async(queues=["events"], wait=False, install_signal_handlers=False)

    deferred_names = [j["task_name"] for j in connector.jobs.values()]
    (rederive,) = [j for j in connector.jobs.values() if j["task_name"] == "brief.rederive"]
    assert rederive["args"] == {"brief_version_id": version_id}
    assert "extract.backfill" in deferred_names, (
        f"extract.backfill not deferred; got {deferred_names}"
    )
    (backfill_job,) = [j for j in connector.jobs.values() if j["task_name"] == "extract.backfill"]
    assert backfill_job["args"]["brief_id"] == brief_id
    assert backfill_job["args"]["workspace_id"] == workspace_id  # from DB, not payload
    assert backfill_job["queueing_lock"] == f"backfill:{brief_id}:{version}"


@pytest.fixture
def app_fixture():
    return make_app()


def test_build_app_registers_verdict_compute(app_fixture):
    assert "verdict.compute" in app_fixture.tasks


def test_build_app_registers_index_chunk_embed(app_fixture):
    assert "index.chunk_embed" in app_fixture.tasks


async def test_unclaim_failure_does_not_lose_the_batch() -> None:
    first, second = approved(), approved()
    store = FlakyUnclaimStore([first, second])
    connector = InMemoryConnector()
    app = make_app(approvals=store, connector=connector)
    real = connector.defer_jobs_all

    async def fail_first(jobs: Any) -> Any:
        if any(
            j.task_name == "test.work" and j.args.get("approval_id") == str(first.id) for j in jobs
        ):
            raise RuntimeError("db down")
        return await real(jobs)

    connector.defer_jobs_all = fail_first  # type: ignore[method-assign]
    async with app.open_async():
        await tick(app)
    assert store.unclaim_attempts == [first.id]  # the unclaim was tried and failed
    ids = [j["args"]["approval_id"] for j in work_jobs(connector)]
    assert ids == [str(second.id)]  # the rest of the batch still resumed


async def test_dispatch_brief_preview_requested_queues_the_preview() -> None:
    connector = InMemoryConnector()
    app = build_app(
        connector,
        AllEnabled(),
        HealthState(),
        FakeHealth(),
        ai_configured=lambda: {"chat": True, "embed": True},
        approvals=MemoryApprovalStore(),
    )
    preview_id = str(uuid4())
    async with app.open_async():
        await app.tasks[DISPATCH_TASK].defer_async(
            event="brief.preview_requested",
            event_id=str(uuid4()),
            payload={"brief_id": str(uuid4()), "preview_id": preview_id},
        )
        await app.run_worker_async(queues=["events"], wait=False, install_signal_handlers=False)

    (preview,) = [j for j in connector.jobs.values() if j["task_name"] == "brief.preview"]
    assert preview["args"] == {"preview_id": preview_id}
    assert {"brief.rederive", "brief.publish"} <= set(app.tasks)


async def test_dispatch_rederive_only_skips_collection() -> None:
    """Going live or resuming re-derives the version without new collection or backfill."""
    connector = InMemoryConnector()
    app = build_app(
        connector,
        AllEnabled(),
        HealthState(),
        FakeHealth(),
        ai_configured=lambda: {"chat": True, "embed": True},
        approvals=MemoryApprovalStore(),
        pool=MagicMock(),
    )
    version_id = str(uuid4())
    async with app.open_async():
        await app.tasks[DISPATCH_TASK].defer_async(
            event="brief.version_published",
            event_id=str(uuid4()),
            payload={
                "brief_id": str(uuid4()),
                "brief_version_id": version_id,
                "rederive_only": True,
            },
        )
        await app.run_worker_async(queues=["events"], wait=False, install_signal_handlers=False)

    names = [j["task_name"] for j in connector.jobs.values()]
    assert [j["status"] for j in connector.jobs.values() if j["task_name"] == DISPATCH_TASK] == [
        "succeeded"
    ]
    assert "brief.rederive" in names
    assert "extract.backfill" not in names
    assert "radar.generate_queries" not in names
