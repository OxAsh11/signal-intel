"""Procrastinate app and the Phase 0 job types."""

from typing import Any

import procrastinate

from signal_core.events import DISPATCH_TASK
from signal_core.jobs import KillSwitch, job
from signal_core.logging import get_logger
from worker.health import HealthState

log = get_logger(__name__)


def build_app(
    connector: procrastinate.connector.BaseConnector, kill_switch: KillSwitch, state: HealthState
) -> procrastinate.App:
    app = procrastinate.App(connector=connector)

    @app.periodic(cron="* * * * *", periodic_id="system.heartbeat")
    @job(app, type="system.heartbeat", kill_switch=kill_switch, queue="system", retry=0)
    async def heartbeat(timestamp: int) -> None:
        # M17 canary seed: proves scheduling, queueing and execution work end to end.
        state.mark_heartbeat()
        log.info("system.heartbeat", scheduled_for=timestamp)

    @job(app, type=DISPATCH_TASK, kill_switch=kill_switch, queue="events")
    async def dispatch(**event: Any) -> None:
        # Consumers (derivation S5, webhooks M13, notifications, indexer S9, health M17)
        # register here as their modules land. Each must be idempotent by event_id.
        log.info("outbox.dispatched", event_id=event.get("event_id"), event_type=event.get("event"))

    return app
