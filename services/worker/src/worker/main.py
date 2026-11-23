"""Worker entrypoint: Procrastinate worker, outbox relay and health server."""

import asyncio
import contextlib
import signal

from procrastinate import PsycopgConnector
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.factory import build_gateway
from signal_core.config import Settings, get_settings
from signal_core.db import configure_worker_connection, make_pool, run_async
from signal_core.events import (
    DISPATCH_TASK,
    Deferrer,
    PgOutboxStore,
    procrastinate_deferrer,
    relay_once,
)
from signal_core.health import PgHealthStore
from signal_core.jobs import DbKillSwitch
from signal_core.logging import configure_logging, get_logger
from signal_core.metering import PgApprovalStore
from worker.app import build_app
from worker.health import HealthState, serve_health
from worker.reveal import pool_lookup, serve_reveal

log = get_logger("worker")


async def relay_loop(
    settings: Settings,
    conninfo: str,
    store: PgOutboxStore,
    defer: Deferrer,
    state: HealthState,
    stop: asyncio.Event,
) -> None:
    """Relay on NOTIFY, with a poll fallback so a missed notification never stalls delivery."""
    async with await AsyncConnection.connect(
        conninfo, autocommit=True, prepare_threshold=None
    ) as listener:
        await listener.execute("listen outbox")
        while not stop.is_set():
            try:
                n = await relay_once(store, defer, batch_size=settings.outbox_batch_size)
                state.mark_relay()
                if n >= settings.outbox_batch_size:
                    continue  # backlog: drain without waiting
            except Exception:
                log.exception("outbox.relay_failed")
            async for _ in listener.notifies(timeout=settings.outbox_poll_seconds, stop_after=1):
                pass


EXTRACT_MODEL_SQL = (
    "insert into public.ai_capability_model (capability, model) values ('extract', %s)"
    " on conflict (capability) do update set model = excluded.model, updated_at = now()"
    " where public.ai_capability_model.model is distinct from excluded.model"
)


EXTRACT_PRICES_SQL = (
    "select count(*) from public.model_price"
    " where model = %s and unit in ('ai_input_tokens', 'ai_output_tokens')"
)


async def record_ai_models(pool: AsyncConnectionPool, settings: Settings) -> None:
    """Column run estimates are priced in SQL: tell it which model extraction uses, and warn when
    that model has no price (every extract estimate would then fail). Never stops the worker."""
    model = settings.ai_model_extract
    try:
        async with pool.connection() as conn:
            await conn.execute(EXTRACT_MODEL_SQL, (model,))
            cur = await conn.execute(EXTRACT_PRICES_SQL, (model,))
            row = await cur.fetchone()
    except Exception:  # an estimate falls back to the seeded model; the worker still starts
        log.exception("worker.ai_models_not_recorded")
        return
    if not row or int(row[0]) < 2:
        log.warning(
            "worker.extract_model_unpriced",
            model=model,
            detail="add input and output token prices to public.model_price;"
            " until then extract column estimates and requests fail",
        )


async def amain() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.app_env != "local")
    if settings.database_url_session is None:
        raise SystemExit("DATABASE_URL_SESSION is not set. See .env.example.")
    conninfo = settings.database_url_session.get_secret_value()

    state = HealthState()
    # The web app reveals a full contact value and reads provider status only through small,
    # token-protected ports. Without the token both stay closed.
    reveal_token = (
        settings.worker_status_token.get_secret_value() if settings.worker_status_token else None
    )
    health = await serve_health(state, settings.port, token=reveal_token)
    pool = make_pool(conninfo)
    await pool.open()
    reveal = (
        await serve_reveal(pool_lookup(pool), reveal_token, settings.worker_reveal_port)
        if reveal_token
        else None
    )
    if reveal is None:
        log.warning("worker.reveal_disabled", detail="WORKER_STATUS_TOKEN is not set")
    connector = PsycopgConnector(
        conninfo=conninfo, configure=configure_worker_connection, kwargs={"prepare_threshold": None}
    )
    gateway = build_gateway(settings, pool)
    await record_ai_models(pool, settings)
    app = build_app(
        connector,
        DbKillSwitch(pool),
        state,
        PgHealthStore(pool),
        ai_configured=gateway.configured,
        approvals=PgApprovalStore(pool),
        gateway=gateway,
        pool=pool,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    log.info("worker.starting", env=settings.app_env, port=settings.port)
    async with app.open_async():
        dispatch_task = app.tasks[DISPATCH_TASK]
        # Report AI status right away so Settings is not blank for the first ten minutes.
        await app.tasks["system.ai_status"].defer_async(timestamp=0)
        relay = asyncio.create_task(
            relay_loop(
                settings,
                conninfo,
                PgOutboxStore(pool),
                procrastinate_deferrer(dispatch_task),
                state,
                stop,
            )
        )
        worker = asyncio.create_task(
            app.run_worker_async(
                queues=[
                    "system",
                    "events",
                    "briefs",
                    "fetch",
                    "extract",
                    "identity",
                    "derive",
                    "compute",
                    "health",
                    "default",
                ],
                install_signal_handlers=False,
            )
        )
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({relay, worker, waiter}, return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        log.info("worker.stopping")
        for t in (relay, worker):
            t.cancel()
        await asyncio.gather(relay, worker, return_exceptions=True)
        for name, t in (("relay", relay), ("worker", worker)):
            if t in done and not t.cancelled() and t.exception() is not None:
                log.error("worker.crashed", component=name, error=str(t.exception()))
    health.close()
    if reveal is not None:
        reveal.close()
    await pool.close()
    log.info("worker.stopped")


def run() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        run_async(amain())


if __name__ == "__main__":
    run()
