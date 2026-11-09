"""Procrastinate app and the Phase 0 job types."""

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from typing import Any, Literal
from uuid import UUID

import aiohttp
import procrastinate
from procrastinate.exceptions import AlreadyEnqueued
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.config import get_settings
from signal_core.connectors.base import FetchGuard
from signal_core.connectors.csv_upload import CsvConnector
from signal_core.connectors.job_posts import JobPostConnector
from signal_core.connectors.news import NewsConnector
from signal_core.connectors.website import WebsiteConnector
from signal_core.events import DISPATCH_TASK
from signal_core.health import HealthStore
from signal_core.jobs import KillSwitch, job
from signal_core.logging import get_logger
from signal_core.metering import ApprovalStore, CallContext
from signal_core.modules.m01_brief import BriefIntakeInput, run_brief_intake
from signal_core.modules.m02_radar import create_source_records, upsert_radar_queries
from signal_core.modules.m02_radar_ai import generate_radar_queries
from signal_core.modules.m03_extraction import extract_structured, judge_relevance
from signal_core.modules.m04_identity import duplicate_sweep, resolve_mention
import signal_core.modules.m04_identity as _m04_identity
from worker.health import HealthState

log = get_logger(__name__)

CANARY_CRON = "17 3 * * *"  # daily, 03:17 UTC
AI_STATUS_CRON = "*/10 * * * *"
RELEASE_STALE_CRON = "*/5 * * * *"
RADAR_HEALTH_CRON = "30 * * * *"  # every hour at :30
IDENTITY_SWEEP_CRON = "0 2 * * *"  # daily, 02:00 UTC
RESUME_BATCH = 50


# ---------------------------------------------------------------------------
# Private DB helpers (require a live pool — None in unit tests)
# ---------------------------------------------------------------------------


async def _load_radar(pool: AsyncConnectionPool, radar_id: UUID) -> dict:
    """Load radar row from DB; raises ValueError if not found."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id, org_id, workspace_id, brief_id, connector_type, config, cursor_state"
                " from public.radar where id = %s",
                (str(radar_id),),
            )
            row = await cur.fetchone()
    if row is None:
        raise ValueError(f"Radar {radar_id} not found")
    return {
        "id": row[0],
        "org_id": row[1],
        "workspace_id": row[2],
        "brief_id": row[3],
        "connector_type": row[4],
        "config": row[5] or {},
        "cursor_state": row[6] or {},
    }


async def _load_radar_queries(pool: AsyncConnectionPool, radar_id: UUID) -> list[dict]:
    """Return list of query dicts for *radar_id*."""
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select query_text, language, source from public.radar_query where radar_id = %s",
                (str(radar_id),),
            )
            rows = await cur.fetchall()
    return [{"query_text": r[0], "language": r[1], "source": r[2]} for r in rows]


async def _update_cursor(
    pool: AsyncConnectionPool,
    radar_id: UUID,
    cursor_state: dict,
    fetched: int,
    yielded: int,
) -> None:
    """Call app.update_radar_cursor RPC."""
    async with pool.connection() as conn:
        await conn.execute(
            "select app.update_radar_cursor(%s, %s, %s, %s)",
            (str(radar_id), json.dumps(cursor_state), fetched, yielded),
        )


def build_app(
    connector: procrastinate.connector.BaseConnector,
    kill_switch: KillSwitch,
    state: HealthState,
    health: HealthStore,
    *,
    ai_configured: Callable[[], dict[str, bool]],
    approvals: ApprovalStore,
    gateway: AiGateway | None = None,
    pool: AsyncConnectionPool | None = None,
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

    # ------------------------------------------------------------------
    # Source fetch jobs (Phase 1c)
    # ------------------------------------------------------------------

    @job(app, type="source.fetch_csv", kill_switch=kill_switch, queue="fetch", retry=3)
    async def fetch_csv(
        radar_id: str, org_id: str, workspace_id: str, brief_id: str, **_: Any
    ) -> dict:
        assert pool is not None, "pool required for source.fetch_csv"
        r = await _load_radar(pool, UUID(radar_id))
        queries = await _load_radar_queries(pool, UUID(radar_id))

        # Resolve the CSV path: may be a Supabase Storage path ("uploads/...") or local.
        config = dict(r["config"])
        storage_path: str = config.get("storage_path", "")
        local_path: str | None = None
        if storage_path.startswith("uploads/"):
            settings = get_settings()
            if not settings.supabase_url or not settings.supabase_service_role_key:
                raise RuntimeError(
                    "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for csv_upload radars"
                )
            url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/signal-uploads/{storage_path}"
            svc_key = settings.supabase_service_role_key.get_secret_value()
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {svc_key}"}) as resp:
                    resp.raise_for_status()
                    content = await resp.read()
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
            tmp.write(content)
            tmp.close()
            local_path = tmp.name
            config = {**config, "storage_path": local_path}

        conn_obj = CsvConnector()
        guard = FetchGuard()
        try:
            payloads, next_cursor = await conn_obj.fetch(
                config=config,
                queries=[q["query_text"] for q in queries],
                cursor=r["cursor_state"],
                guard=guard,
                limit=500,
            )
        finally:
            if local_path:
                os.unlink(local_path)
        created = await create_source_records(
            pool, UUID(org_id), UUID(workspace_id), f"radar:{radar_id}", conn_obj.source_type, payloads
        )
        await _update_cursor(pool, UUID(radar_id), next_cursor, len(payloads), created)
        return {"fetched": len(payloads), "created": created}

    @job(app, type="source.fetch_website", kill_switch=kill_switch, queue="fetch", retry=3)
    async def fetch_website(
        radar_id: str, org_id: str, workspace_id: str, brief_id: str, **_: Any
    ) -> dict:
        assert pool is not None, "pool required for source.fetch_website"
        r = await _load_radar(pool, UUID(radar_id))
        queries = await _load_radar_queries(pool, UUID(radar_id))
        conn_obj = WebsiteConnector()
        guard = FetchGuard()
        payloads, next_cursor = await conn_obj.fetch(
            config=r["config"],
            queries=[q["query_text"] for q in queries],
            cursor=r["cursor_state"],
            guard=guard,
            limit=500,
        )
        created = await create_source_records(
            pool, UUID(org_id), UUID(workspace_id), f"radar:{radar_id}", conn_obj.source_type, payloads
        )
        await _update_cursor(pool, UUID(radar_id), next_cursor, len(payloads), created)
        return {"fetched": len(payloads), "created": created}

    @job(app, type="source.fetch_news", kill_switch=kill_switch, queue="fetch", retry=3)
    async def fetch_news(
        radar_id: str, org_id: str, workspace_id: str, brief_id: str, **_: Any
    ) -> dict:
        assert pool is not None, "pool required for source.fetch_news"
        r = await _load_radar(pool, UUID(radar_id))
        queries = await _load_radar_queries(pool, UUID(radar_id))
        conn_obj = NewsConnector()
        guard = FetchGuard()
        payloads, next_cursor = await conn_obj.fetch(
            config=r["config"],
            queries=[q["query_text"] for q in queries],
            cursor=r["cursor_state"],
            guard=guard,
            limit=500,
        )
        created = await create_source_records(
            pool, UUID(org_id), UUID(workspace_id), f"radar:{radar_id}", conn_obj.source_type, payloads
        )
        await _update_cursor(pool, UUID(radar_id), next_cursor, len(payloads), created)
        return {"fetched": len(payloads), "created": created}

    @job(app, type="source.fetch_job_post", kill_switch=kill_switch, queue="fetch", retry=3)
    async def fetch_job_post(
        radar_id: str, org_id: str, workspace_id: str, brief_id: str, **_: Any
    ) -> dict:
        assert pool is not None, "pool required for source.fetch_job_post"
        r = await _load_radar(pool, UUID(radar_id))
        queries = await _load_radar_queries(pool, UUID(radar_id))
        conn_obj = JobPostConnector()
        guard = FetchGuard()
        payloads, next_cursor = await conn_obj.fetch(
            config=r["config"],
            queries=[q["query_text"] for q in queries],
            cursor=r["cursor_state"],
            guard=guard,
            limit=500,
        )
        created = await create_source_records(
            pool, UUID(org_id), UUID(workspace_id), f"radar:{radar_id}", conn_obj.source_type, payloads
        )
        await _update_cursor(pool, UUID(radar_id), next_cursor, len(payloads), created)
        return {"fetched": len(payloads), "created": created}

    # ------------------------------------------------------------------
    # Radar query generation (Phase 1c)
    # ------------------------------------------------------------------

    @job(app, type="radar.generate_queries", kill_switch=kill_switch, queue="fetch", retry=1)
    async def generate_queries_job(
        radar_id: str, org_id: str, workspace_id: str, **_: Any
    ) -> dict:
        assert pool is not None and gateway is not None, "pool and gateway required"
        r = await _load_radar(pool, UUID(radar_id))
        # Load hypothesis and offer from the brief's draft_config jsonb.
        async with pool.connection() as db_conn:
            async with db_conn.cursor() as cur:
                await cur.execute(
                    "select draft_config->>'hypothesis', draft_config->>'offer' from public.brief where id = %s",
                    (str(r["brief_id"]),),
                )
                brief_row = await cur.fetchone()
        raw_hypothesis = (brief_row[0] or "") if brief_row else ""
        hypotheses: list[str] = [raw_hypothesis] if raw_hypothesis else []
        icp_summary: str = (brief_row[1] or "") if brief_row else ""
        ctx = CallContext(UUID(org_id), UUID(workspace_id), None, "worker", "radar.generate_queries", {})
        query_set = await generate_radar_queries(
            gateway, hypotheses, icp_summary, r["connector_type"], ctx
        )
        await upsert_radar_queries(pool, UUID(radar_id), query_set)
        return {"queries": len(query_set.queries)}

    # ------------------------------------------------------------------
    # Radar health cron (Phase 1c)
    # ------------------------------------------------------------------

    @app.periodic(cron=RADAR_HEALTH_CRON, periodic_id="radar.health_check")
    @job(app, type="radar.health_check", kill_switch=kill_switch, queue="system", retry=0)
    async def radar_health_check(timestamp: int) -> dict:
        if pool is None:
            return {"silent": 0}
        async with pool.connection() as db_conn:
            cur = await db_conn.execute(
                """select id from public.radar
                   where status = 'active'
                   and (last_fetched_at is null or last_fetched_at < now() - interval '25 hours')"""
            )
            stale = [row[0] for row in await cur.fetchall()]
        for radar_id in stale:
            async with pool.connection() as db_conn:
                await db_conn.execute("select app.mark_radar_silent(%s)", (radar_id,))
            log.warning("radar.silent", radar_id=str(radar_id))
        return {"silent": len(stale)}

    # ------------------------------------------------------------------
    # Extraction jobs (Phase 1d)
    # ------------------------------------------------------------------

    @job(app, type="extract.relevance_judge", kill_switch=kill_switch, queue="extract", retry=2)
    async def extract_relevance_judge_job(*, source_record_id: str, **_: Any) -> None:
        assert pool is not None and gateway is not None, "pool and gateway required"
        from uuid import UUID as _UUID

        ctx = CallContext(
            org_id=_UUID(int=0),  # loaded from source_record inside judge_relevance
            workspace_id=_UUID(int=0),
            brief_id=None,
            job_id=None,
            job_type="extract.relevance_judge",
            job_args={"source_record_id": source_record_id},
        )
        # Load org/workspace from DB for the real context
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT org_id, workspace_id FROM public.source_record WHERE id = %s",
                    (source_record_id,),
                )
                row = await cur.fetchone()
        if row is None:
            log.warning("extract.source_record_not_found", source_record_id=source_record_id)
            return
        ctx = CallContext(
            org_id=_UUID(str(row[0])),
            workspace_id=_UUID(str(row[1])),
            brief_id=None,
            job_id=None,
            job_type="extract.relevance_judge",
            job_args={"source_record_id": source_record_id},
        )
        relevant = await judge_relevance(pool, gateway, _UUID(source_record_id), ctx)
        if relevant:
            with contextlib.suppress(AlreadyEnqueued):
                await app.tasks["extract.structured"].configure(
                    queueing_lock=source_record_id
                ).defer_async(source_record_id=source_record_id)

    @job(app, type="extract.structured", kill_switch=kill_switch, queue="extract", retry=2)
    async def extract_structured_job(*, source_record_id: str, **_: Any) -> None:
        assert pool is not None and gateway is not None, "pool and gateway required"
        from uuid import UUID as _UUID

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT org_id, workspace_id FROM public.source_record WHERE id = %s",
                    (source_record_id,),
                )
                row = await cur.fetchone()
        if row is None:
            log.warning("extract.source_record_not_found", source_record_id=source_record_id)
            return
        ctx = CallContext(
            org_id=_UUID(str(row[0])),
            workspace_id=_UUID(str(row[1])),
            brief_id=None,
            job_id=None,
            job_type="extract.structured",
            job_args={"source_record_id": source_record_id},
        )
        await extract_structured(pool, gateway, _UUID(source_record_id), ctx)

    # ------------------------------------------------------------------
    # Identity resolution jobs (Phase 1d)
    # ------------------------------------------------------------------

    @job(app, type="identity.resolve", kill_switch=kill_switch, queue="identity", retry=2)
    async def identity_resolve_task(*, mention_id: str, workspace_id: str, **_: Any) -> str:
        assert pool is not None and gateway is not None, "pool and gateway required"
        from uuid import UUID as _UUID

        ctx = CallContext(
            org_id=_UUID(int=0),
            workspace_id=_UUID(workspace_id),
            brief_id=None,
            job_id=None,
            job_type="identity.resolve",
            job_args={"mention_id": mention_id, "workspace_id": workspace_id},
        )
        return await resolve_mention(pool, gateway, _UUID(mention_id), _UUID(workspace_id), ctx)

    # Wire the deferrable handle so m04_identity.identity_resolve_job.defer_async() works.
    _m04_identity.identity_resolve_job = identity_resolve_task

    @app.periodic(cron=IDENTITY_SWEEP_CRON, periodic_id="identity.duplicate_sweep")
    @job(app, type="identity.duplicate_sweep", kill_switch=kill_switch, queue="identity", retry=1)
    async def identity_duplicate_sweep_task(timestamp: int) -> int:
        """Sweep all orgs with unresolved mentions daily at 02:00 UTC."""
        if pool is None or gateway is None:
            return 0
        from uuid import UUID as _UUID

        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT DISTINCT org_id FROM public.mention WHERE entity_id IS NULL"
                )
                org_rows = await cur.fetchall()

        total = 0
        for row in org_rows:
            org_id = _UUID(str(row[0]))
            ctx = CallContext(
                org_id=org_id,
                workspace_id=_UUID(int=0),
                brief_id=None,
                job_id=None,
                job_type="identity.duplicate_sweep",
                job_args={},
            )
            total += await duplicate_sweep(pool, gateway, org_id, ctx)
        return total

    # Wire the deferrable handle.
    _m04_identity.identity_duplicate_sweep_job = identity_duplicate_sweep_task

    # ------------------------------------------------------------------
    # Dispatch router (updated for Phase 1c events)
    # ------------------------------------------------------------------

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
        elif evt == "radar.fetch_queued":
            # csv_upload → source.fetch_csv; job_post → source.fetch_job_post; etc.
            ctype = payload.get("connector_type", "")
            job_name = f"source.fetch_{ctype.replace('_upload', '')}"
            if job_name in app.tasks:
                with contextlib.suppress(AlreadyEnqueued):
                    await app.configure_task(
                        name=job_name, queueing_lock=payload["radar_id"]
                    ).defer_async(
                        radar_id=payload["radar_id"],
                        org_id=payload["org_id"],
                        workspace_id=payload["workspace_id"],
                        brief_id=payload["brief_id"],
                    )
        elif evt == "brief.version_published" and pool is not None:
            brief_id = payload.get("brief_id")
            if brief_id:
                async with pool.connection() as db_conn:
                    cur = await db_conn.execute(
                        "select id, org_id, workspace_id from public.radar"
                        " where brief_id = %s and status = 'active'",
                        (brief_id,),
                    )
                    radars = await cur.fetchall()
                for r in radars:
                    with contextlib.suppress(AlreadyEnqueued):
                        await app.tasks["radar.generate_queries"].configure(
                            queueing_lock=f"gq:{r[0]}"
                        ).defer_async(
                            radar_id=str(r[0]),
                            org_id=str(r[1]),
                            workspace_id=str(r[2]),
                        )
        log.info("outbox.dispatched", event_id=event.get("event_id"), event_type=evt)

    return app
