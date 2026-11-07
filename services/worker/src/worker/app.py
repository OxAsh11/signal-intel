"""Procrastinate app and the Phase 0 job types."""

from typing import Any

import procrastinate

from signal_core.events import DISPATCH_TASK
from signal_core.health import HealthStore
from signal_core.jobs import KillSwitch, job
from signal_core.logging import get_logger
from worker.health import HealthState

log = get_logger(__name__)

CANARY_CRON = "17 3 * * *"  # daily, 03:17 UTC


def build_app(
    connector: procrastinate.connector.BaseConnector,
    kill_switch: KillSwitch,
    state: HealthState,
    health: HealthStore,
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

    @job(app, type=DISPATCH_TASK, kill_switch=kill_switch, queue="events")
    async def dispatch(**event: Any) -> None:
        # Consumers (derivation S5, webhooks M13, notifications, indexer S9, health M17)
        # register here as their modules land. Each must be idempotent by event_id.
        log.info("outbox.dispatched", event_id=event.get("event_id"), event_type=event.get("event"))

    return app
