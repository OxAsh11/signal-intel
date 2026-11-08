"""Procrastinate app and the Phase 0 job types."""

import contextlib
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.events import DISPATCH_TASK
from signal_core.health import HealthStore
from signal_core.jobs import KillSwitch, job
from signal_core.logging import get_logger
from signal_core.metering import ApprovalStore
from signal_core.modules.m01_brief import BriefIntakeInput, run_brief_intake
from worker.health import HealthState

log = get_logger(__name__)

CANARY_CRON = "17 3 * * *"  # daily, 03:17 UTC
AI_STATUS_CRON = "*/10 * * * *"
RELEASE_STALE_CRON = "*/5 * * * *"
RESUME_BATCH = 50


def build_app(
    connector: procrastinate.connector.BaseConnector,
    kill_switch: KillSwitch,
    state: HealthState,
    health: HealthStore,
    *,
    ai_configured: Callable[[], dict[str, bool]],
    approvals: ApprovalStore,
    gateway: AiGateway,
    pool: AsyncConnectionPool,
) -> procrastinate.App:
    app = procrastinate.App(connector=connector)

    @app.periodic(cron="* * * * *", periodic_id="system.heartbeat")
    @job(app, type="system.heartbeat", kill_switch=kill_switch, queue="system", retry=0)
    async def heartbeat(timestamp: int) -> None:
        # Proves scheduling, queueing and execution work; M17 alerts if it goes silent.
        state.mark_heartbeat()
        await health.record("system.heartbeat", "ok", {"scheduled_for": timestamp}, owner="S1")
        log.info("system.heartbeat", scheduled_for=timestamp)

    @app.periodic(cron=CANARY_CRON, periodic_id="system.canary")
    @job(app, type="system.canary", kill_switch=kill_switch, queue="system", retry=2)
    async def canary(timestamp: int) -> dict[str, Any]:
        # M17: a synthetic record through source → mention → entity → claim → verdict, daily.
        result = await health.run_canary()
        if result.get("status") == "ok":
            log.info("system.canary.ok", verdict_id=result.get("verdict_id"))
        else:
            log.error("system.canary.failed", error=result.get("error"))
        return result

    @app.periodic(cron=AI_STATUS_CRON, periodic_id="system.ai_status")
    @job(app, type="system.ai_status", kill_switch=kill_switch, queue="system", retry=0)
    async def ai_status(timestamp: int) -> dict[str, bool]:
        # Settings shows "not set up" / "degraded" from this row; keys themselves are never read.
        flags = ai_configured()
        detail = {"chat": bool(flags.get("chat")), "embed": bool(flags.get("embed"))}
        status: Literal["ok", "degraded"] = "ok" if all(detail.values()) else "degraded"
        await health.record("ai_gateway", status, detail, owner="S3")
        log.info("system.ai_status", status=status, **detail)
        return detail

    @app.periodic(cron="* * * * *", periodic_id="budget.resume")
    @job(app, type="budget.resume", kill_switch=kill_switch, queue="system", retry=0)
    async def budget_resume(timestamp: int) -> int:
        # Re-queue work paused for budget once its approval is granted. Each approval is claimed
        # atomically (resumed_at set) before deferral, so it is resumed at most once. If the
        # deferral fails we unclaim so the next tick retries; a crash between claim and deferral
        # loses that one job rather than risking a double spend.
        resumed = 0
        for approved in await approvals.claim_approved(RESUME_BATCH):
            if approved.job_type not in app.tasks:
                log.error(
                    "budget.resume_unknown_job",
                    approval_id=str(approved.id),
                    job_type=approved.job_type,
                )
                continue
            args = {**approved.job_args, "approval_id": str(approved.id)}
            try:
                with contextlib.suppress(AlreadyEnqueued):  # already queued: same as success
                    await app.configure_task(
                        name=approved.job_type, queueing_lock=str(approved.id)
                    ).defer_async(**args)
            except Exception:
                try:
                    await approvals.unclaim(approved.id)
                except Exception:
                    # One failed unclaim must not lose the rest of the batch (ids only in the log).
                    log.exception("budget.unclaim_failed", approval_id=str(approved.id))
                log.exception(
                    "budget.resume_failed",
                    approval_id=str(approved.id),
                    job_type=approved.job_type,
                )
                continue
            log.info("budget.resumed", approval_id=str(approved.id), job_type=approved.job_type)
            resumed += 1
        return resumed

    @app.periodic(cron=RELEASE_STALE_CRON, periodic_id="budget.release_stale")
    @job(app, type="budget.release_stale", kill_switch=kill_switch, queue="system", retry=0)
    async def budget_release_stale(timestamp: int) -> dict[str, int]:
        # A crash between reserve and settle leaves a reservation counting as spend; release those
        # older than 30 minutes and expire approvals pending over 7 days. Counts only in the log.
        counts = await approvals.release_stale()
        log.info("budget.release_stale", **counts)
        return counts

    @job(app, type="brief.intake", kill_switch=kill_switch, queue="briefs", retry=1)
    async def brief_intake(
        brief_id: str, description: str, org_id: str, workspace_id: str, **_: Any
    ) -> None:
        inp = BriefIntakeInput(
            brief_id=UUID(brief_id),
            description=description,
            org_id=UUID(org_id),
            workspace_id=UUID(workspace_id),
        )
        await run_brief_intake(inp, gateway, pool)
        log.info("brief.intake.done", brief_id=brief_id)

    @job(app, type=DISPATCH_TASK, kill_switch=kill_switch, queue="events")
    async def dispatch(**event: Any) -> None:
        # Consumers (derivation S5, webhooks M13, notifications, indexer S9, health M17)
        # register here as their modules land. Each must be idempotent by event_id.
        evt = event.get("event", "")
        payload = event.get("payload") or {}
        if evt == "brief.intake_requested":
            await brief_intake.configure(queueing_lock=payload["brief_id"]).defer_async(
                brief_id=payload["brief_id"],
                description=payload.get("description", payload.get("name", "")),
                org_id=payload["org_id"],
                workspace_id=payload["workspace_id"],
            )
        log.info("outbox.dispatched", event_id=event.get("event_id"), event_type=evt)

    return app
