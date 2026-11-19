"""The whole pipeline against the real schema, with a fake AI.

Starts scripts/pg-test-server.mjs (every migration applied to an in-memory Postgres) and drives
one account from a fetched page to a held meeting:

  fetch → extract → resolve → verdict → score → labels → research → card
  → contact → campaign dry run → live → sent / replied / bounced (inbound webhooks)
  → reply classified → stage → meeting held → metrics, API, MCP, outbound webhook

The worker app is the real one (same jobs, same dispatcher) on Procrastinate's in-memory
connector, and every statement runs as service_role, so wrong columns, wrong RPC signatures,
wrong parameter types and missing grants all fail here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import socket
import subprocess
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from procrastinate.testing import InMemoryConnector
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

import api.mcp_server as mcp_server
import signal_core.modules.m04_identity as m04
import signal_core.modules.m05_verdict as m05
import signal_core.modules.m13_integrations as m13
from api.main import build_app as build_api
from signal_core.ai_gateway.gateway import Result
from signal_core.ai_gateway.prompts import render
from signal_core.config import get_settings
from signal_core.connectors import SourcePayload, brave
from signal_core.connectors.base import FetchGuard
from signal_core.events import DISPATCH_TASK, PgOutboxStore, procrastinate_deferrer, relay_once
from signal_core.health import PgHealthStore
from signal_core.jobs import DbKillSwitch
from signal_core.metering import CallContext, PgApprovalStore
from signal_core.modules.m02_radar import create_source_records
from signal_core.modules.m03_extraction import BACKFILL_SCOPE_SQL, backfill_record
from signal_core.modules.m09_activation import push_campaign_batch
from signal_core.modules.m11_stage import mark_dormant_accounts
from signal_core.modules.m12_meetings import confirm_meeting, sync_meeting
from signal_core.modules.m16_reporting import compute_metric, get_funnel
from worker.app import build_app
from worker.health import HealthState

REPO = Path(__file__).resolve().parents[3]
CANARY_EXTRACTOR_VERSION = UUID("00000000-0000-4000-a000-0000000000c6")
QUEUES = ["events", "briefs", "fetch", "extract", "identity", "derive", "compute", "default"]

USER = UUID("00000000-0000-4000-8000-0000000e2e01")
ORG = UUID("00000000-0000-4000-8000-0000000e2e02")
WS = UUID("00000000-0000-4000-8000-0000000e2e03")
MEMBER = UUID("00000000-0000-4000-8000-0000000e2e04")
BRIEF = UUID("00000000-0000-4000-8000-0000000e2e05")

PAGE = (
    "Acme Engineering (acme-eng.example) designs every project in Revit and is hiring ten BIM "
    "coordinators for its twelve sites in the Netherlands."
)
# A later page names the same company without a domain: it must attach, not go to Review.
PAGE_2 = "Acme Engineering designs every project in Revit and has opened a new studio in Rotterdam."

# The Brief-columns scenario (P2) runs in its own tenant.
DRONE_MARK = "drone inspection programme"
DRONE_PAGE = (
    "Volt Grid (voltgrid.example), a Dutch energy utility, runs a drone inspection programme "
    "across its 40 substations."
)
ORG_P2 = UUID("00000000-0000-4000-8000-0000000e2f02")
WS_P2 = UUID("00000000-0000-4000-8000-0000000e2f03")
BRIEF_P2 = UUID("00000000-0000-4000-8000-0000000e2f05")
OTHER_BRIEF_P2 = UUID("00000000-0000-4000-8000-0000000e2f06")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def conninfo() -> Iterator[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    port = _free_port()
    proc = subprocess.Popen(
        [node, str(REPO / "scripts" / "pg-test-server.mjs"), str(port)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=REPO,
    )
    ready = threading.Event()
    output: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            output.append(line)
            if line.startswith("READY"):
                ready.set()

    threading.Thread(target=pump, daemon=True).start()
    if not ready.wait(timeout=180):
        proc.kill()
        pytest.fail("pg-test-server did not start:\n" + "".join(output))
    yield f"host=127.0.0.1 port={port} user=postgres password=postgres dbname=postgres sslmode=disable"
    proc.kill()
    proc.wait(timeout=10)


class FakeGateway:
    """Answers each prompt with a fixed, valid output. Rendering the prompt first proves the
    caller supplied every variable the released template needs."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        # (version, Brief hypothesis) of every verdict call.
        self.verdicts: list[tuple[int, str]] = []

    def configured(self) -> dict[str, bool]:
        return {"chat": True, "embed": True}

    def model_for(self, capability: Any) -> str:
        return "fake-model"

    async def run(
        self,
        capability: Any,
        prompt: Any,
        variables: dict[str, str],
        output: Any,
        ctx: CallContext,
        *,
        max_tokens: int = 2048,
    ) -> Any:
        render(prompt, variables)
        self.calls.append(prompt.name)
        if prompt.name == "verdict":
            self.verdicts.append((prompt.version, variables.get("hypothesis", "")))
        return Result(
            value=output.model_validate(self._answer(prompt.name, variables)),
            extractor_version_id=CANARY_EXTRACTOR_VERSION,
            cost_usd=Decimal(0),
        )

    async def embed(self, texts: list[str], ctx: CallContext, **_: Any) -> list[list[float]]:
        return [[0.01] * 1024 for _ in texts]

    @staticmethod
    def _answer(name: str, variables: dict[str, str]) -> dict[str, Any]:
        if DRONE_MARK in variables.get("content", ""):
            return FakeGateway._drone_answer(name, variables)
        if name == "relevance_judge":
            assert variables.get("hypothesis", "").strip(), "relevance needs the Brief hypothesis"
            if "fake tender" in variables["content"].lower():
                return {"relevant": False, "reason": "fixture"}
            assert "designs every project in Revit" in variables["content"], (
                "extraction must see the page text"
            )
            return {"relevant": True, "reason": "names a company and a tool"}
        if name == "extraction" and "Rotterdam" in variables["content"]:
            return {
                "mentions": [
                    {
                        "entity_type": "company",
                        "raw_name": "Acme Engineering",
                        "raw_country": "NL",
                        "raw_attributes": {},
                    }
                ],
                "claims": [
                    {
                        "mention_index": 0,
                        "predicate_key": "uses_revit",
                        "value": True,
                        "quote": "designs every project in Revit",
                        "confidence": 90,
                    },
                    {
                        "mention_index": 0,
                        "predicate_key": "vertical",
                        "value": "engineering",
                        "confidence": 75,
                    },
                ],
            }
        if name == "extraction":
            return {
                "mentions": [
                    {
                        "entity_type": "company",
                        "raw_name": "Acme Engineering",
                        "raw_domain": "acme-eng.example",
                        "raw_country": "NL",
                        "raw_attributes": {},
                    }
                ],
                "claims": [
                    {
                        "mention_index": 0,
                        "predicate_key": "uses_revit",
                        "value": True,
                        "quote": "designs every project in Revit",
                        "confidence": 92,
                    },
                    {
                        "mention_index": 0,
                        "predicate_key": "vertical",
                        "value": "engineering",
                        "confidence": 80,
                    },
                    {"mention_index": 0, "predicate_key": "sites", "value": 12, "confidence": 70},
                ],
            }
        if name == "verdict":
            return {
                "value": "Confirmed",
                "reasoning": "quoted",
                "supporting_claim_indices": [0, 1, 2],
            }
        if name == "account_card":
            return {"bullets": ["Designs in Revit"], "next_step": "Send the case study"}
        if name == "research":
            source_id = re.search(r"\[SOURCE:([0-9a-f-]{36})\]", variables["sources"])
            assert source_id, "research must be given stored sources"
            return {
                "sections": [
                    {
                        "title": "Evidence",
                        "content": f"Designs in Revit [SOURCE:{source_id.group(1)}].",
                    },
                    {"title": "Made up", "content": f"Unsupported [SOURCE:{uuid4()}]."},
                ]
            }
        if name == "next_step":
            return {"next_step": "Follow up with the BIM lead."}
        if name == "reply_classify":
            return {"reply_class": "Interested"}
        if name == "radar_queries":
            assert variables["geographies"] == "Germany"
            return {
                "queries": [{"query_text": "hotel fit-out tender", "source": "hypothesis"}],
                "lexicon": ["fake tender"],
            }
        if name == "identity_tiebreak":
            return {"winner_index": None, "confidence": 0, "reason": "unsure"}
        raise AssertionError(f"unexpected prompt {name}")

    @staticmethod
    def _drone_answer(name: str, variables: dict[str, str]) -> dict[str, Any]:
        """The Brief-columns scenario (P2): a Brief with typed columns, then a new column."""
        keys = {p["key"] for p in json.loads(variables.get("predicates") or "[]")}
        if name == "relevance_judge":
            return {"relevant": True, "reason": "a utility with a drone programme"}
        if name == "extraction":
            # Only the record's Brief's columns are sent, not every workspace/global predicate.
            assert keys == {"drone_programme", "industry"}, keys
            return {
                "mentions": [
                    {
                        "entity_type": "company",
                        "raw_name": "Volt Grid",
                        "raw_domain": "voltgrid.example",
                        "raw_country": "NL",
                        "raw_attributes": {},
                    }
                ],
                "claims": [
                    {
                        "mention_index": 0,
                        "predicate_key": "drone_programme",
                        "value": True,
                        "quote": "runs a drone inspection programme",
                        "confidence": 90,
                    },
                    {
                        "mention_index": 0,
                        "predicate_key": "industry",
                        "value": "energy",
                        "confidence": 80,
                    },
                ],
            }
        if name == "extraction_backfill":
            assert keys == {"site_count"}, keys
            names = [m["name"] for m in json.loads(variables["mentions"])]
            assert names == ["Volt Grid"], names
            return {
                "claims": [
                    {
                        "mention_index": 0,
                        "predicate_key": "site_count",
                        "value": 40,
                        "quote": "across its 40 substations",
                        "confidence": 85,
                    },
                    # An index that was not given is dropped.
                    {
                        "mention_index": 3,
                        "predicate_key": "site_count",
                        "value": 2,
                        "confidence": 85,
                    },
                ],
                "source_date": "2026-10-01",
            }
        if name == "account_card":
            return {"bullets": ["Runs drones"], "next_step": "Send the case study"}
        if name == "identity_tiebreak":
            return {"winner_index": None, "confidence": 0, "reason": "unsure"}
        raise AssertionError(f"unexpected prompt {name} in the drone scenario")


async def _configure(conn: AsyncConnection[Any]) -> None:
    await conn.execute("set role service_role")
    await conn.execute("set search_path to procrastinate, public, extensions")
    await conn.execute("set app.channel to 'system'")
    await conn.commit()


class Db:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def admin(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Run as the database owner (fixtures and assertions)."""
        async with self.pool.connection() as conn:
            await conn.execute("reset role")
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall() if cur.description else []
            await conn.execute("set role service_role")
        return rows

    async def one(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        rows = await self.admin(sql, params)
        return rows[0][0] if rows else None

    async def as_user(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run as the signed-in member through the interface's public.* entry points."""
        async with self.pool.connection() as conn:
            await conn.execute("reset role")
            await conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(USER),))
            await conn.execute("set local role authenticated")
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall() if cur.description else []
            await conn.commit()
            await conn.execute("set role service_role")
        return rows[0][0] if rows else None


async def _seed(db: Db) -> UUID:
    await db.admin("insert into auth.users (id, email) values (%s, 'owner@e2e.test')", (USER,))
    await db.admin("insert into public.organisation (id, name) values (%s, 'E2E Org')", (ORG,))
    await db.admin(
        "insert into public.workspace (id, org_id, name) values (%s, %s, 'E2E')", (WS, ORG)
    )
    await db.admin(
        "insert into public.member (id, org_id, user_id, email) values (%s, %s, %s, 'owner@e2e.test')",
        (MEMBER, ORG, USER),
    )
    await db.admin(
        "insert into public.role_assignment (org_id, member_id, role) values (%s, %s, 'Owner')",
        (ORG, MEMBER),
    )
    await db.admin(
        "insert into public.brief (id, org_id, workspace_id, name, state) values (%s, %s, %s, 'Revit users', 'Active')",
        (BRIEF, ORG, WS),
    )
    config = {
        "hypothesis": "Engineering firms that design in Revit",
        "personas": {"buyer": ["BIM"]},
    }
    # Evidence carries the whole priority so a Confirmed account lands in band High.
    weights = {"fit": 0, "evidence": 1, "scale": 0, "timing": 0}
    brief_version = await db.one(
        "insert into public.brief_version (brief_id, org_id, workspace_id, version, config, rubric, weights)"
        " values (%s, %s, %s, 1, %s::jsonb, '{}', %s::jsonb) returning id",
        (BRIEF, ORG, WS, json.dumps(config), json.dumps(weights)),
    )
    await db.admin(
        "update public.brief set current_version_id = %s where id = %s", (brief_version, BRIEF)
    )
    for key, value_type in (("uses_revit", "boolean"), ("vertical", "text"), ("sites", "number")):
        await db.admin(
            "insert into public.predicate (key, org_id, workspace_id, label, value_type, decay_half_life_days)"
            " values (%s, %s, %s, %s, %s, 365)",
            (key, ORG, WS, key, value_type),
        )
    return UUID(str(brief_version))


async def _drain(app: Any, connector: InMemoryConnector, pool: AsyncConnectionPool) -> None:
    """Relay the outbox and run queued jobs until nothing is left to do."""
    store = PgOutboxStore(pool)
    defer = procrastinate_deferrer(app.tasks[DISPATCH_TASK])
    now = datetime.now(UTC)
    for _ in range(40):
        relayed = await relay_once(store, defer)
        due = [
            j
            for j in connector.jobs.values()
            if j["status"] == "todo"
            and j["queue_name"] in QUEUES
            and (j["scheduled_at"] is None or j["scheduled_at"] <= now)
        ]
        if not relayed and not due:
            return
        await app.run_worker_async(queues=QUEUES, wait=False, install_signal_handlers=False)
    raise AssertionError("pipeline did not settle")


def _failed_jobs(connector: InMemoryConnector) -> list[str]:
    return [
        f"{j['task_name']} {j['args']}" for j in connector.jobs.values() if j["status"] == "failed"
    ]


@pytest.mark.asyncio
async def test_pipeline_end_to_end(conninfo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=1,
        open=False,
        configure=_configure,
        kwargs={"autocommit": False, "prepare_threshold": None},
    )
    await pool.open()
    try:
        await _scenario(pool, monkeypatch)
    finally:
        await pool.close()
        get_settings.cache_clear()


async def _scenario(pool: AsyncConnectionPool, monkeypatch: pytest.MonkeyPatch) -> None:
    db = Db(pool)
    brief_version = await _seed(db)
    gateway = FakeGateway()
    connector = InMemoryConnector()
    app = build_app(
        connector,
        DbKillSwitch(pool),
        HealthState(),
        PgHealthStore(pool),
        ai_configured=gateway.configured,
        approvals=PgApprovalStore(pool),
        gateway=gateway,  # type: ignore[arg-type]
        pool=pool,
    )
    ctx = CallContext(ORG, WS, None, None, "test", {})

    async with app.open_async():
        # Every job the worker registers has a kill-switch row, or it would never run (S1).
        seeded = {r[0] for r in await db.admin("select job_type from public.job_type_config")}
        assert {t for t in app.tasks if "builtin" not in t} - seeded == set()

        # ── Discovery → extraction → identity → verdict → score → research → card ──
        payload = SourcePayload(
            url="https://acme-eng.example/careers",
            fetched_at=datetime.now(UTC),
            content_hash=hashlib.sha256(PAGE.encode()).hexdigest(),
            payload_ref=None,
            language="en",
            raw=PAGE.encode(),
        )
        assert await create_source_records(pool, ORG, WS, "radar:e2e", "website", [payload]) == 1
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []

        source_record = await db.one(
            "select id from public.source_record where org_id = %s", (ORG,)
        )
        assert (
            await db.one(
                "select extraction_status::text from public.source_record where id = %s",
                (source_record,),
            )
            == "extracted"
        )
        # A re-fetch of the same content is not a new record.
        await create_source_records(pool, ORG, WS, "radar:e2e", "website", [payload])
        assert (
            await db.one("select count(*) from public.source_record where org_id = %s", (ORG,)) == 1
        )
        # A retried extraction job on a committed record makes no AI call and writes nothing.
        counts_sql = (
            "select (select count(*) from public.entity where org_id = %s),"
            " (select count(*) from public.mention where org_id = %s),"
            " (select count(*) from public.claim where org_id = %s)"
        )
        counts_before = (await db.admin(counts_sql, (ORG, ORG, ORG)))[0]
        ai_calls_before = len(gateway.calls)
        await app.tasks["extract.structured"].defer_async(source_record_id=str(source_record))
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert len(gateway.calls) == ai_calls_before
        assert (await db.admin(counts_sql, (ORG, ORG, ORG)))[0] == counts_before
        # The page gave no date: its claims are observed at the record's fetch time.
        assert await db.one(
            "select bool_and(c.observed_at = sr.fetched_at) from public.claim c"
            " join public.source_record sr on sr.id = c.source_record_id where sr.id = %s",
            (source_record,),
        )

        account, entity, stage = (
            await db.admin(
                "select a.id, a.entity_id, a.stage::text from public.account a where a.workspace_id = %s",
                (WS,),
            )
        )[0]
        assert stage == "Untouched"
        assert (
            await db.one("select count(*) from public.account where workspace_id = %s", (WS,)) == 1
        )
        assert (
            await db.one(
                "select count(*) from public.claim where entity_id = %s and status = 'active'",
                (entity,),
            )
            == 3
        )
        assert (
            await db.one(
                "select count(*) from public.entity_alias where entity_id = %s and alias_type = 'domain'",
                (entity,),
            )
            == 1
        )

        verdict, claim_count = (
            await db.admin(
                "select value::text, cardinality(claim_ids) from public.verdict"
                " where entity_id = %s and brief_version_id = %s",
                (entity, brief_version),
            )
        )[0]
        assert (verdict, claim_count) == ("Confirmed", 3)

        # ── A second page names the company without a domain: it attaches, and the new
        # claims re-judge the verdict through the claim and merge events ──
        first_computed_at = await db.one(
            "select computed_at from public.verdict where entity_id = %s and brief_version_id = %s",
            (entity, brief_version),
        )
        verdict_jobs_before = sum(
            1
            for j in connector.jobs.values()
            if j["task_name"] == "verdict.compute" and j["args"]["entity_id"] == str(entity)
        )
        payload_2 = SourcePayload(
            url="https://news.example/acme-rotterdam",
            fetched_at=datetime.now(UTC),
            content_hash=hashlib.sha256(PAGE_2.encode()).hexdigest(),
            payload_ref=None,
            language="en",
            raw=PAGE_2.encode(),
        )
        assert await create_source_records(pool, ORG, WS, "radar:e2e", "website", [payload_2]) == 1
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        source_record_2 = await db.one(
            "select id from public.source_record where org_id = %s and id <> %s",
            (ORG, source_record),
        )
        assert (
            await db.one(
                "select extraction_status::text from public.source_record where id = %s",
                (source_record_2,),
            )
            == "extracted"
        )
        assert (
            await db.one("select count(*) from public.review_item where workspace_id = %s", (WS,))
            == 0
        )
        assert (
            await db.one(
                "select entity_id from public.mention where source_record_id = %s",
                (source_record_2,),
            )
            == entity
        )
        claims_by_source = dict(
            await db.admin(
                "select source_record_id, count(*) from public.claim where entity_id = %s"
                " group by source_record_id",
                (entity,),
            )
        )
        assert claims_by_source == {source_record: 3, source_record_2: 2}
        assert (
            await db.one("select count(*) from public.account where workspace_id = %s", (WS,)) == 1
        )
        recomputed_at = await db.one(
            "select computed_at from public.verdict where entity_id = %s and brief_version_id = %s",
            (entity, brief_version),
        )
        assert recomputed_at > first_computed_at, "the verdict was not recomputed after the merge"
        assert (
            sum(
                1
                for j in connector.jobs.values()
                if j["task_name"] == "verdict.compute"
                and j["args"]["entity_id"] == str(entity)
                and j["status"] == "succeeded"
            )
            > verdict_jobs_before
        )
        # The verdict change also queued a radar yield refresh (one global scan).
        assert any(
            j["task_name"] == "radar.yield_compute" and j["queueing_lock"] == "yield:all"
            for j in connector.jobs.values()
        )

        # A claim written straight to the store (no extraction, no identity job) reaches the
        # verdict only through the claim.created event: the dispatcher must queue the recompute.
        def succeeded_verdict_jobs() -> int:
            return sum(
                1
                for j in connector.jobs.values()
                if j["task_name"] == "verdict.compute"
                and j["args"]["entity_id"] == str(entity)
                and j["status"] == "succeeded"
            )

        before_claim_event = (succeeded_verdict_jobs(), recomputed_at)
        new_claim = await db.one(
            "select app.create_claim(%s, %s, (select id from public.predicate where key = 'sites'"
            " and workspace_id = %s), '15'::jsonb, null, null, null, %s, now(), %s, 70::smallint)",
            (ORG, entity, WS, source_record_2, CANARY_EXTRACTOR_VERSION),
        )
        assert new_claim is not None
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert (
            await db.one(
                "select count(*) from public.outbox where event = 'claim.created'"
                " and aggregate_id = %s and published_at is not null",
                (new_claim,),
            )
            == 1
        )
        assert succeeded_verdict_jobs() == before_claim_event[0] + 1
        assert (
            await db.one(
                "select computed_at from public.verdict"
                " where entity_id = %s and brief_version_id = %s",
                (entity, brief_version),
            )
            > before_claim_event[1]
        ), "claim.created did not recompute the verdict"

        band, evidence, scale = (
            await db.admin(
                "select band::text, evidence, scale from public.score where account_id = %s and brief_version_id = %s",
                (account, brief_version),
            )
        )[0]
        assert band == "High" and evidence >= 90 and scale > 0
        labels = {
            (r[0], r[1])
            for r in await db.admin(
                "select kind, value from public.label where account_id = %s", (account,)
            )
        }
        assert {
            ("radar", "e2e"),
            ("vertical", "engineering"),
            ("persona_coverage", "buyer:uncovered"),
        } <= labels

        assert (
            await db.one(
                "select count(*) from public.memory_chunk where doc_id = %s", (source_record,)
            )
            >= 1
        )
        assert (
            await db.one(
                "select count(*) from public.account_card where account_id = %s", (account,)
            )
            == 1
        )
        # Research ran because the account is High and Confirmed, and kept only the real citation.
        assert await db.one(
            "select source_record_ids from public.research_doc where account_id = %s", (account,)
        ) == [source_record]

        # ── A contact, then the score reflects reachability and the stale label goes ──
        async with pool.connection() as conn:
            await conn.execute(
                "select app.upsert_person_contact(%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s::public.contact_verification_status, %s, %s, %s)",
                (
                    str(ORG),
                    str(WS),
                    str(account),
                    "Jane Doe",
                    "BIM Manager",
                    "buyer",
                    "email",
                    "enc:v1:test",
                    "hash-jane",
                    "valid",
                    datetime.now(UTC),
                    "manual",
                    str(source_record),
                ),
            )
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        person, _contact_point = (
            await db.admin(
                "select entity_id, id from public.contact_point where account_id = %s", (account,)
            )
        )[0]
        assert (
            await db.one("select reachability from public.score where account_id = %s", (account,))
            == 100
        )
        labels = {
            (r[0], r[1])
            for r in await db.admin(
                "select kind, value from public.label where account_id = %s", (account,)
            )
        }
        assert ("persona_coverage", "buyer:covered") in labels
        assert ("persona_coverage", "buyer:uncovered") not in labels

        # ── Campaign: dry run first, then live ────────────────────────────────
        campaign = await db.as_user(
            "select public.create_campaign(%s, %s, 'Benelux Revit', 'Vertical')", (WS, BRIEF)
        )
        with pytest.raises(Exception, match="dry run"):
            await db.as_user("select public.set_campaign_push_mode(%s, 'live')", (campaign,))
        await db.as_user("select public.set_campaign_push_mode(%s, 'dry run')", (campaign,))
        assert await app.tasks["campaign.push_cycle"](timestamp=0) == 1
        await _drain(app, connector, pool)
        assert (
            await db.one(
                "select count(*) from public.external_write_log where dry_run and target_id = %s",
                (person,),
            )
            == 1
        )
        assert (
            await db.one(
                "select count(*) from public.campaign_member where campaign_id = %s", (campaign,)
            )
            == 0
        )

        await db.as_user("select public.set_campaign_push_mode(%s, 'live')", (campaign,))
        result = await push_campaign_batch(pool, UUID(str(campaign)), ctx)
        assert result["pushed"] == 1
        assert (await push_campaign_batch(pool, UUID(str(campaign)), ctx))["pushed"] == 0
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert (
            await db.one("select stage::text from public.account where id = %s", (account,))
            == "Queued"
        )

        # ── Inbound events from the sequencer ─────────────────────────────────
        integration = await m13.create_integration(
            pool, ctx, "Sequencer", "Sequencers", "Read", signing_secret="inbound-secret"
        )
        api = build_api()
        api.state.pool = pool

        async def post_event(client: AsyncClient, event: dict[str, Any]) -> dict[str, Any]:
            body = json.dumps(event).encode()
            signature = hmac.new(b"inbound-secret", body, hashlib.sha256).hexdigest()
            response = await client.post(
                f"/v1/webhooks/{integration}",
                content=body,
                headers={
                    "X-Signal-Signature": f"sha256={signature}",
                    "Content-Type": "application/json",
                },
            )
            assert response.status_code == 200, response.text
            return dict(response.json())

        base = {
            "account_id": str(account),
            "person_entity_id": str(person),
            "campaign_id": str(campaign),
        }
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            bad = await client.post(
                f"/v1/webhooks/{integration}",
                content=b"{}",
                headers={"X-Signal-Signature": "sha256=00"},
            )
            assert bad.status_code == 401

            sent = await post_event(client, {**base, "external_id": "m1", "type": "email_sent"})
            again = await post_event(client, {**base, "external_id": "m1", "type": "email_sent"})
            assert sent["activity_id"] == again["activity_id"]
            assert (
                await db.one(
                    "select bounce_messages_sent from public.campaign where id = %s", (campaign,)
                )
                == 1
            )
            assert (
                await db.one("select stage::text from public.account where id = %s", (account,))
                == "Contacted"
            )

            reply = await post_event(
                client,
                {
                    **base,
                    "external_id": "r1",
                    "type": "email_replied",
                    "text": "Yes, let us talk next week.",
                },
            )
            await _drain(app, connector, pool)
            assert _failed_jobs(connector) == []
            reply_class, reply_source = (
                await db.admin(
                    "select payload ->> 'reply_class', payload ->> 'reply_class_source' from public.activity where id = %s",
                    (reply["activity_id"],),
                )
            )[0]
            assert (reply_class, reply_source) == ("Interested", "ai")
            assert (
                await db.one("select stage::text from public.account where id = %s", (account,))
                == "Engaged"
            )

            # A human mark wins and is stored as feedback; the AI cannot overwrite it.
            await db.as_user(
                "select public.classify_reply(%s, 'Referral')", (reply["activity_id"],)
            )
            assert (
                await app.tasks["reply.auto_classify"](
                    activity_id=reply["activity_id"], workspace_id=str(WS), org_id=str(ORG)
                )
                is None
            )
            assert (
                await db.one(
                    "select payload ->> 'reply_class' from public.activity where id = %s",
                    (reply["activity_id"],),
                )
                == "Referral"
            )
            assert (
                await db.one(
                    "select count(*) from public.feedback where target_id = %s and target_type = 'reply_class'",
                    (reply["activity_id"],),
                )
                == 1
            )

            # ── API and MCP, scoped to the token's workspace ─────────────────
            token = await db.as_user("select public.create_api_token(%s, 'e2e', 'Read')", (WS,))
            auth = {"Authorization": f"Bearer {token}"}
            for path in (
                "accounts",
                "entities",
                "claims",
                "briefs",
                "campaigns",
                "activity",
                "tasks",
                "meetings",
            ):
                response = await client.get(
                    f"/v1/{path}", params={"workspace_id": str(WS)}, headers=auth
                )
                assert response.status_code == 200, f"{path}: {response.text}"
            listed = await client.get(
                "/v1/accounts", params={"workspace_id": str(WS)}, headers=auth
            )
            assert [a["id"] for a in listed.json()["items"]] == [str(account)]
            other = await client.get(
                "/v1/accounts", params={"workspace_id": str(uuid4())}, headers=auth
            )
            assert other.status_code == 403
            versions = await client.get(f"/v1/briefs/{BRIEF}/versions", headers=auth)
            assert versions.status_code == 200 and len(versions.json()["items"]) == 1

            # Admin endpoints need an Admin token; a Read token is refused.
            new_integration = {"workspace_id": str(WS), "name": "CRM", "category": "CRMs"}
            refused = await client.post("/v1/integrations", json=new_integration, headers=auth)
            assert refused.status_code == 403
            admin_token = await db.as_user(
                "select public.create_api_token(%s, 'e2e admin', 'Admin')", (WS,)
            )
            admin_auth = {"Authorization": f"Bearer {admin_token}"}
            created = await client.post(
                "/v1/integrations", json=new_integration, headers=admin_auth
            )
            assert created.status_code == 201 and created.json()["access_mode"] == "Read"
            assert created.json()["signing_secret"]
            blocked = await client.post(
                "/v1/webhook-subscriptions",
                json={
                    "workspace_id": str(WS),
                    "url": "http://localhost/hook",
                    "event_types": ["task.created"],
                },
                headers=admin_auth,
            )
            assert blocked.status_code == 422

            mcp_server._pool = pool
            mcp_server._token_workspace.set(str(WS))
            card = await mcp_server.get_account_card(str(account), str(WS))
            assert card["display_name"] == "Acme Engineering" and card["verdict"] == "Confirmed"
            assert len(await mcp_server.search_account("acme", str(WS))) == 1
            assert (
                len(await mcp_server.get_claims(str(entity), str(WS))) == 6
            )  # both pages + direct
            assert len(await mcp_server.find_accounts(str(WS), band="High")) == 1
            with pytest.raises(mcp_server.WorkspaceNotPermitted):
                await mcp_server.find_accounts(str(uuid4()))

            # ── Meeting: matched by domain, counted only with proof, attributed ──
            calendar = await m13.create_integration(
                pool, ctx, "Calendar", "Calendars and note-takers"
            )
            # Scheduled after the campaign touch, so the touch falls inside the attribution window.
            scheduled = datetime.now(UTC) + timedelta(minutes=5)
            meeting = await sync_meeting(
                pool, ORG, WS, calendar, "evt-1", scheduled, ["ACME-eng.example"]
            )
            assert (
                await sync_meeting(
                    pool, ORG, WS, calendar, "evt-1", scheduled, ["acme-eng.example"]
                )
                == meeting
            )
            unmatched = await sync_meeting(
                pool, ORG, WS, calendar, "evt-2", scheduled, ["nobody.example"]
            )
            assert (
                await db.one("select account_id from public.meeting where id = %s", (unmatched,))
                is None
            )
            assert await confirm_meeting(pool, meeting, scheduled, "unconfirmed") is False
            await db.as_user("select public.confirm_meeting(%s)", (meeting,))
            await _drain(app, connector, pool)
            assert _failed_jobs(connector) == []
            proof, tier, attributed = (
                await db.admin(
                    "select proof_status, attribution_tier, attribution_campaign_id from public.meeting where id = %s",
                    (meeting,),
                )
            )[0]
            assert (proof, tier, attributed) == ("confirmed", "account", campaign)
            assert (
                await db.one("select stage::text from public.account where id = %s", (account,))
                == "Meeting"
            )

            # ── Bounce guard: one bounce on one send pauses the campaign ───────
            await post_event(client, {**base, "external_id": "b1", "type": "email_bounced"})
            assert (
                await db.one("select status from public.campaign where id = %s", (campaign,))
                == "paused"
            )
            assert (await push_campaign_batch(pool, UUID(str(campaign)), ctx))["paused"] is True

            # ── Unsubscribe suppresses the person for the whole organisation ──
            await post_event(client, {**base, "external_id": "u1", "type": "email_unsubscribed"})
            assert (
                await db.one(
                    "select count(*) from public.suppression_list where org_id = %s and person_entity_id = %s",
                    (ORG, person),
                )
                == 1
            )

        # ── Next step and tasks ───────────────────────────────────────────────
        assert await db.one(
            "select suggested_text from public.next_step where account_id = %s", (account,)
        ) == ("Follow up with the BIM lead.")
        task = await db.as_user("select public.create_task(%s, 'Call Jane', null)", (account,))
        await db.as_user("select public.complete_task(%s)", (task,))
        assert await db.one("select status from public.task where id = %s", (task,)) == "done"
        assert (
            await db.one(
                "select count(*) from public.activity where account_id = %s and kind = 'task_completed'",
                (account,),
            )
            == 1
        )

        # ── Metrics: one definition, Brief-scoped, same value for worker and interface ──
        expected = {
            "accounts_discovered": 1,
            "accounts_verified": 1,
            "accounts_researched": 1,
            "accounts_reachable": 1,
            "accounts_contacted": 1,
            "people_contacted": 1,
            "messages_sent": 1,
            "genuine_replies_people": 1,
            "genuine_replies_accounts": 1,
            "companies_met": 1,
            "meetings_held": 1,
            "bounce_rate": 1,
            "high_band_coverage": 1,
        }
        for key, value in expected.items():
            assert (await compute_metric(pool, WS, BRIEF, key))["value"] == value, key
        assert (
            await db.as_user("select public.compute_metric(%s, %s, 'companies_met')", (WS, BRIEF))
            == 1
        )
        funnel = {row["stage"]: row["count"] for row in await get_funnel(pool, WS, BRIEF)}
        assert funnel["Meeting"] == 1
        assert await app.tasks["metric.snapshot"](
            workspace_id=str(WS), brief_id=str(BRIEF), org_id=str(ORG)
        )

        # ── Outbound webhook: signed, recorded, retried, replayable ───────────
        await _drain(app, connector, pool)  # events from before the subscription existed
        with pytest.raises(m13.WebhookUrlBlocked):
            await m13.create_webhook_subscription(
                pool, ORG, WS, ["task.created"], "https://127.0.0.1/hook"
            )
        monkeypatch.setattr(m13, "check_webhook_url", lambda url: None)
        subscription, secret = await m13.create_webhook_subscription(
            pool, ORG, WS, ["task.created"], "https://hooks.example/signal"
        )
        posts: list[tuple[bytes, str]] = []
        responses = [(500, None), (200, None), (200, None)]

        async def fake_post(
            url: str, payload_bytes: bytes, signature: str
        ) -> tuple[int | None, str | None]:
            posts.append((payload_bytes, signature))
            return responses[len(posts) - 1]

        monkeypatch.setattr(m13, "_post", fake_post)
        await db.as_user("select public.create_task(%s, 'Send proposal', null)", (account,))
        await _drain(app, connector, pool)
        # The first attempt got a 500 and re-queued itself as attempt 2 after the backoff delay.
        assert len(posts) == 1 and posts[0][1] == m13.sign(secret, posts[0][0])
        retry = [
            j
            for j in connector.jobs.values()
            if j["task_name"] == "webhook.deliver" and j["status"] == "todo"
        ]
        assert len(retry) == 1 and retry[0]["args"]["attempt"] == 2
        assert retry[0]["scheduled_at"] > datetime.now(UTC)
        assert await app.tasks["webhook.deliver"](**retry[0]["args"]) == "delivered"
        assert posts[1] == posts[0]
        delivery, status, attempts = (
            await db.admin(
                "select id, status, attempt_count from public.webhook_delivery where subscription_id = %s",
                (subscription,),
            )
        )[0]
        assert (status, attempts) == ("delivered", 2)
        assert await m13.replay_webhook_delivery(pool, delivery) == "delivered"
        assert posts[2] == posts[0], "a replay re-sends the signed bytes unchanged"

        # ── Periodic jobs run cleanly against the schema ──────────────────────
        assert await mark_dormant_accounts(pool, WS, ctx) == 0
        assert await app.tasks["radar.fetch_cycle"](timestamp=0) == 0
        assert (await app.tasks["inbox.reconcile"](timestamp=0))["queued"] == 0
        assert await app.tasks["account.dormancy_sweep"](timestamp=0) == 0
        assert await app.tasks["health.check"](timestamp=0)

        # ── Verdict review: weekly sample, a human mark that holds, conflicting claims ──
        verdict_id = await db.one(
            "select id from public.verdict where entity_id = %s and brief_version_id = %s",
            (entity, brief_version),
        )
        assert await app.tasks["verdict.sample_weekly"](timestamp=0) == 1
        assert await app.tasks["verdict.sample_weekly"](timestamp=0) == 0, "one sample a week"
        review = await db.one(
            "select id from public.verdict_review where verdict_id = %s and reason = 'sample'"
            " and status = 'pending'",
            (verdict_id,),
        )
        assert review is not None
        await db.as_user(
            "select public.resolve_verdict_review(%s, 'correct', 'Likely', 'one source only')",
            (review,),
        )
        assert (
            await db.admin(
                "select v.value::text, r.status, r.corrected_value::text"
                "  from public.verdict_review r join public.verdict v on v.id = r.verdict_id"
                " where r.id = %s",
                (review,),
            )
        )[0] == ("Likely", "corrected", "Likely")
        assert (
            await db.one(
                "select action from public.feedback where target_type = 'verdict' and target_id = %s",
                (verdict_id,),
            )
            == "correct"
        )
        await _drain(app, connector, pool)

        # The judge would say Confirmed again; the human mark holds while the claims are the same.
        judged = gateway.calls.count("verdict")
        assert await m05.compute_verdict(pool, gateway, entity, WS, ctx) == "Likely"  # type: ignore[arg-type]
        assert gateway.calls.count("verdict") == judged

        # A new claim that says the opposite: the verdict is judged again and goes to review
        # with every uses_revit claim (one yes from each page, and the no).
        await db.admin(
            "select app.create_claim(%s, %s, (select id from public.predicate where key = 'uses_revit'"
            " and workspace_id = %s), 'false'::jsonb, 'moved every project off Revit', null, null, %s,"
            " now(), %s, 85::smallint)",
            (ORG, entity, WS, source_record, CANARY_EXTRACTOR_VERSION),
        )
        assert await m05.compute_verdict(pool, gateway, entity, WS, ctx) == "Confirmed"  # type: ignore[arg-type]
        assert gateway.calls.count("verdict") == judged + 1
        assert (
            await db.one(
                "select cardinality(claim_ids) from public.verdict_review"
                " where verdict_id = %s and reason = 'conflict' and status = 'pending'",
                (verdict_id,),
            )
            == 3
        )
        # Asking again about the same claims does not add a second item.
        await m05.compute_verdict(pool, gateway, entity, WS, ctx)  # type: ignore[arg-type]
        assert (
            await db.one(
                "select count(*) from public.verdict_review where verdict_id = %s and reason = 'conflict'",
                (verdict_id,),
            )
            == 1
        )
        await _drain(app, connector, pool)

        # ── Brief edit: preview, publish, re-derive (M1) ──────────────────────
        draft = {
            "hypothesis": "Engineering firms that design in Revit",
            "personas": {"buyer": ["BIM"]},
            "offer": "BIM services",
            "examples": {"positive": ["a", "b", "c"], "negative": ["d", "e"]},
            "sources": {"radar_types": ["company_websites", "tenders_procurement"]},
            "icp": {"geographies": ["Germany"]},
        }
        await db.as_user(
            "select public.save_brief_draft_ui(%s, %s::jsonb)", (BRIEF, json.dumps(draft))
        )
        preview = await db.as_user("select public.request_brief_preview(%s)", (BRIEF,))
        await _drain(app, connector, pool)
        status, tier, rejudge, accounts = (
            await db.admin(
                "select status, tier::text, rejudge, (result ->> 'accounts')::int"
                " from public.brief_preview where id = %s",
                (preview,),
            )
        )[0]
        assert (status, tier, rejudge) == ("ready", "Expand", True)
        assert accounts >= 1

        # Publishing provisions a tenders radar from the Brief's sources (M2). Brave and the
        # page fetches are stubbed; everything else (metering, attribution, pre-filter) is real.
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "test-key")
        get_settings.cache_clear()

        async def fake_search(self: Any, q: str, params: dict[str, str]) -> dict[str, Any]:
            assert q == "hotel fit-out tender" and params["freshness"] == "py"
            return {
                "web": {
                    "results": [
                        {"url": "https://tenders.example/award", "title": "Award"},
                        {
                            "url": "https://tenders.example/other",
                            "title": "Other",
                            "description": "An unrelated announcement",
                        },
                    ]
                }
            }

        async def fake_page(url: str) -> tuple[int, str, str | None, bytes]:
            if url.endswith("/award"):
                body = b"<html><body>Hotel group fake tender: fit-out contract</body></html>"
                return 200, "text/html", None, body
            return 404, "text/html", None, b""

        async def no_guard(self: Any, url: str) -> None:
            return None

        monkeypatch.setattr(brave.BraveSearchConnector, "_default_search", fake_search)
        monkeypatch.setattr(brave, "_raw_get", fake_page)
        monkeypatch.setattr(FetchGuard, "check", no_guard)

        version_2 = await db.as_user("select public.publish_brief(%s)", (BRIEF,))
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        tender_radar, lexicon = (
            await db.admin(
                "select id, lexicon from public.radar"
                " where brief_id = %s and connector_type = 'tender'",
                (BRIEF,),
            )
        )[0]
        assert lexicon == ["fake tender"]
        assert (
            await db.one(
                "select count(*) from public.radar where brief_id = %s and connector_type = 'website'",
                (BRIEF,),
            )
            == 0
        )  # websites need a URL list; they are not provisioned
        records = await db.admin(
            "select content_kind, radar_query_id is not null, extraction_status::text, status_detail"
            " from public.source_record where radar_id = %s order by url",
            (tender_radar,),
        )
        assert records == [
            ("full", True, "irrelevant", "fixture"),
            ("snippet", True, "irrelevant", "prefilter"),
        ]
        assert (
            await db.one(
                "select count(*) from public.usage_ledger where provider = 'brave' and unit = 'fetch'"
            )
            == 1
        )
        await db.admin("select app.compute_radar_yield()")
        assert (
            await db.admin(
                "select record_count, prefiltered_count, yield_count from public.radar where id = %s",
                (tender_radar,),
            )
        )[0] == (2, 1, 0)
        assert (
            await db.one(
                "select edit_tier::text from public.brief_version where id = %s", (version_2,)
            )
            == "Expand"
        )
        assert (
            await db.one(
                "select count(*) from public.score where brief_version_id = %s", (version_2,)
            )
            >= 1
        )
        assert _failed_jobs(connector) == []

        # ── Exact-name identity against the real schema: same type, live, compatible country ─
        async def _alias(entity_type: str, value: str) -> UUID:
            new_id = await db.one(
                "select app.create_entity(%s, %s::public.entity_type, %s)",
                (ORG, entity_type, value.split("|")[0]),
            )
            await db.admin(
                "select app.create_entity_alias(%s, %s, 'name_country'::public.alias_type,"
                " %s, 50::smallint, %s)",
                (ORG, new_id, value, source_record),
            )
            return UUID(str(new_id))

        def _mention(entity_type: str, name: str, country: str) -> dict[str, Any]:
            return {
                "entity_type": entity_type,
                "raw_name": name,
                "raw_country": country,
                "raw_domain": None,
                "raw_attributes": {},
            }

        person = await _alias("person", "jordan blake|gb")
        # Sorted before "in" too, so neither heap nor index order can put "in" in the first five.
        for country in ("ae", "at", "be", "ch", "de", "dk"):
            await _alias("company", f"fugro|{country}")
        fugro_in = await _alias("company", "fugro|in")
        async with pool.connection() as lookup_conn:
            assert await m04._lookup_aliases(
                lookup_conn, ORG, _mention("company", "Fugro", "IN")
            ) == [(str(fugro_in), "name_country", 95)]
            # A company never matches a person's name alias; a person never gets the 95 rule.
            assert (
                await m04._lookup_aliases(
                    lookup_conn, ORG, _mention("company", "Jordan Blake", "GB")
                )
                == []
            )
            person_hits = await m04._lookup_aliases(
                lookup_conn, ORG, _mention("person", "Jordan Blake", "GB")
            )
            assert person_hits and all(c[0] == str(person) and c[2] <= 89 for c in person_hits)

        # ── Removing a member revokes their tokens at once ────────────────────
        await db.admin("update public.member set status = 'removed' where id = %s", (MEMBER,))
        assert await db.one("select count(*) from public.api_token where revoked_at is null") == 0


# ── P2: Brief-scoped extraction and column backfill ─────────────────────────

FLYTBASE = json.loads(
    (REPO / "packages" / "signal_core" / "tests" / "fixtures" / "flytbase_columns.json").read_text(
        encoding="utf-8"
    )
)


async def _insert_version(db: Db, brief: UUID, version: int, config: dict[str, Any]) -> UUID:
    """A new Brief version through a real insert path: the sync trigger fires on insert."""
    version_id = await db.one(
        "insert into public.brief_version (brief_id, org_id, workspace_id, version, config, rubric, weights)"
        " values (%s, %s, %s, %s, %s::jsonb, '{}', '{}'::jsonb) returning id",
        (brief, ORG_P2, WS_P2, version, json.dumps(config)),
    )
    await db.admin(
        "update public.brief set current_version_id = %s where id = %s", (version_id, brief)
    )
    return UUID(str(version_id))


async def _raw_record(db: Db, radar: UUID, status: str, tag: str) -> UUID:
    """A record written straight to the store (no event), in a given state."""
    record = await db.one(
        "insert into public.source_record (org_id, workspace_id, connector_id, source_type, url,"
        " fetched_at, content_hash, extraction_status, radar_id)"
        " values (%s, %s, %s, 'website', %s, now(), %s, %s::public.extraction_status, %s) returning id",
        (ORG_P2, WS_P2, f"radar:{radar}", f"https://{tag}.example/", tag, status, radar),
    )
    return UUID(str(record))


@pytest.mark.asyncio
async def test_brief_columns_backfill_end_to_end(conninfo: str) -> None:
    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=1,
        open=False,
        configure=_configure,
        kwargs={"autocommit": False, "prepare_threshold": None},
    )
    await pool.open()
    try:
        await _brief_columns_scenario(pool)
    finally:
        await pool.close()


async def _brief_columns_scenario(pool: AsyncConnectionPool) -> None:
    db = Db(pool)
    await db.admin("insert into public.organisation (id, name) values (%s, 'P2 Org')", (ORG_P2,))
    await db.admin(
        "insert into public.workspace (id, org_id, name) values (%s, %s, 'P2')", (WS_P2, ORG_P2)
    )
    for brief, name in ((BRIEF_P2, "Drone utilities"), (OTHER_BRIEF_P2, "Other")):
        await db.admin(
            "insert into public.brief (id, org_id, workspace_id, name, state)"
            " values (%s, %s, %s, %s, 'Active')",
            (brief, ORG_P2, WS_P2, name),
        )
    # A workspace predicate no Brief uses: extraction must not send it.
    await db.admin(
        "insert into public.predicate (key, org_id, workspace_id, label, value_type)"
        " values ('other_key', %s, %s, 'Other', 'text')",
        (ORG_P2, WS_P2),
    )
    v1_config = {
        "hypothesis": "Utilities that run drone programmes",
        "predicates": FLYTBASE["predicates"][:2],  # drone_programme, industry
        "evidence_rules": FLYTBASE["evidence_rules"],
    }
    await _insert_version(db, BRIEF_P2, 1, v1_config)
    await _insert_version(db, OTHER_BRIEF_P2, 1, {"hypothesis": "Something else"})
    radars: dict[UUID, UUID] = {}
    for brief in (BRIEF_P2, OTHER_BRIEF_P2):
        radars[brief] = UUID(
            str(
                await db.one(
                    "insert into public.radar (org_id, workspace_id, brief_id, connector_type, name)"
                    " values (%s, %s, %s, 'website', 'site') returning id",
                    (ORG_P2, WS_P2, brief),
                )
            )
        )

    gateway = FakeGateway()
    connector = InMemoryConnector()
    app = build_app(
        connector,
        DbKillSwitch(pool),
        HealthState(),
        PgHealthStore(pool),
        ai_configured=gateway.configured,
        approvals=PgApprovalStore(pool),
        gateway=gateway,  # type: ignore[arg-type]
        pool=pool,
    )
    async with app.open_async():
        await _drain(app, connector, pool)  # version 1's backfill event: nothing extracted yet
        payload = SourcePayload(
            url="https://voltgrid.example/news",
            fetched_at=datetime.now(UTC),
            content_hash=hashlib.sha256(DRONE_PAGE.encode()).hexdigest(),
            payload_ref=None,
            language="en",
            raw=DRONE_PAGE.encode(),
        )
        assert (
            await create_source_records(
                pool, ORG_P2, WS_P2, f"radar:{radars[BRIEF_P2]}", "website", [payload]
            )
            == 1
        )
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        record, radar_id, status = (
            await db.admin(
                "select id, radar_id, extraction_status::text from public.source_record"
                " where org_id = %s and content_hash = %s",
                (ORG_P2, payload.content_hash),
            )
        )[0]
        assert (radar_id, status) == (radars[BRIEF_P2], "extracted")
        entity = await db.one(
            "select entity_id from public.mention where source_record_id = %s", (record,)
        )
        assert entity is not None
        claim_keys_sql = (
            "select p.key from public.claim c join public.predicate p on p.id = c.predicate_id"
            " where c.entity_id = %s and c.status = 'active' order by p.key"
        )
        assert [r[0] for r in await db.admin(claim_keys_sql, (entity,))] == [
            "drone_programme",
            "industry",
        ]

        # The Brief's rules decide: a quoted drone programme claim Confirms without a verdict call.
        verdict_sql = (
            "select v.value::text, v.claim_ids, v.inputs from public.verdict v"
            " join public.brief b on b.current_version_id = v.brief_version_id"
            " where v.entity_id = %s and b.id = %s"
        )
        value, claim_ids, inputs = (await db.admin(verdict_sql, (entity, BRIEF_P2)))[0]
        drone_claim = await db.one(
            "select c.id from public.claim c join public.predicate p on p.id = c.predicate_id"
            " where c.entity_id = %s and p.key = 'drone_programme'",
            (entity,),
        )
        assert (value, [str(c) for c in claim_ids]) == ("Confirmed", [str(drone_claim)])
        assert inputs["method"] == "rubric"
        assert [(r["predicate"], r["met"]) for r in inputs["rubric"]] == [
            ("drone_programme", True),
            ("industry", False),
        ]
        # Only the other, rule-less Brief in the workspace asked the AI, through verdict v2.
        assert gateway.verdicts, "the legacy Brief still judges through verdict v2"
        assert set(gateway.verdicts) == {(2, "Something else")}, gateway.verdicts
        other_method = await db.one(
            "select v.inputs ->> 'method' from public.verdict v"
            " join public.brief b on b.current_version_id = v.brief_version_id"
            " where v.entity_id = %s and b.id = %s",
            (entity, OTHER_BRIEF_P2),
        )
        assert other_method == "ai"

        # Out of scope: another Brief's extracted record, and this Brief's pending record.
        other = await _raw_record(db, radars[OTHER_BRIEF_P2], "extracted", "other-brief")
        pending = await _raw_record(db, radars[BRIEF_P2], "pending", "pending-record")
        scope = [r[0] for r in await db.admin(BACKFILL_SCOPE_SQL, (WS_P2, BRIEF_P2, 500))]
        assert scope == [record]
        assert other not in scope and pending not in scope

        entities_sql = (
            "select (select count(*) from public.entity where org_id = %s),"
            " (select count(*) from public.mention where org_id = %s)"
        )
        before = (await db.admin(entities_sql, (ORG_P2, ORG_P2)))[0]

        # ── Version 2 adds site_count; its event reaches the dispatcher and the jobs run ──
        v2_config = v1_config | {"predicates": FLYTBASE["predicates"]}
        v2 = await _insert_version(db, BRIEF_P2, 2, v2_config)
        event_keys = await db.one(
            "select payload -> 'predicate_keys' from public.outbox"
            " where event = 'predicate.backfill_requested' and aggregate_id = %s",
            (v2,),
        )
        assert event_keys == ["site_count"]
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []

        backfills = [
            j for j in connector.jobs.values() if j["task_name"] == "extract.backfill_record"
        ]
        assert [(j["queueing_lock"], j["status"]) for j in backfills] == [
            (f"backfill:{record}:site_count", "succeeded")
        ]
        assert [r[0] for r in await db.admin(claim_keys_sql, (entity,))] == [
            "drone_programme",
            "industry",
            "site_count",
        ]
        value, observed_at = (
            await db.admin(
                "select c.value, c.observed_at from public.claim c"
                " join public.predicate p on p.id = c.predicate_id"
                " where c.entity_id = %s and p.key = 'site_count' and c.status = 'active'",
                (entity,),
            )
        )[0]
        assert value == 40
        assert observed_at == datetime(2026, 10, 1, tzinfo=UTC)
        # No new entity or mention; the record's status is untouched.
        assert (await db.admin(entities_sql, (ORG_P2, ORG_P2)))[0] == before
        assert (
            await db.one(
                "select extraction_status::text from public.source_record where id = %s", (record,)
            )
            == "extracted"
        )

        # A rerun adds no duplicate claim.
        claims_sql = "select count(*) from public.claim where entity_id = %s"
        claims_before = await db.one(claims_sql, (entity,))
        ctx = CallContext(ORG_P2, WS_P2, None, None, "extract.backfill", {})
        assert await backfill_record(pool, gateway, UUID(str(record)), ["site_count"], ctx) == 1  # type: ignore[arg-type]
        assert await db.one(claims_sql, (entity,)) == claims_before
        # Version 2 is judged under the same rules: still no verdict call for this Brief.
        assert set(gateway.verdicts) == {(2, "Something else")}, gateway.verdicts


# ── P3: column runs, formula auto-run ───────────────────────────────────────

USER_P3 = UUID("00000000-0000-4000-8000-0000000e3001")
ORG_P3 = UUID("00000000-0000-4000-8000-0000000e3002")
WS_P3 = UUID("00000000-0000-4000-8000-0000000e3003")
MEMBER_P3 = UUID("00000000-0000-4000-8000-0000000e3004")
BRIEF_P3 = UUID("00000000-0000-4000-8000-0000000e3005")
P3_PAGE = (
    "Volt Grid (voltgrid.example), a Dutch energy utility, published its drone fleet audit: the "
    "programme now inspects its substations every month."
)
P3_COLUMNS = [
    *FLYTBASE["predicates"],
    {
        "key": "has_programme_and_sites",
        "label": "Programme and sites",
        "value_type": "boolean",
        "fill_method": "formula",
        "formula": "drone_programme and site_count > 0",
        "inputs": ["drone_programme", "site_count"],
        "role": "info",
    },
    {
        "key": "sites_label",
        "label": "Sites",
        "value_type": "text",
        "fill_method": "formula",
        "formula": 'if(site_count > 0, concat(site_count, " sites"), null)',
        "inputs": ["site_count"],
        "role": "info",
    },
]


class P3Gateway(FakeGateway):
    """The P2 drone page answers, for a Brief whose columns include two formula columns."""

    @staticmethod
    def _answer(name: str, variables: dict[str, str]) -> dict[str, Any]:
        keys = {p["key"] for p in json.loads(variables.get("predicates") or "[]")}
        if name == "relevance_judge":
            return {"relevant": True, "reason": "a utility with a drone programme"}
        if name == "extraction":
            # Formula columns never reach an extraction prompt.
            assert keys == {"drone_programme", "industry", "site_count"}, keys
            return {
                "mentions": [
                    {
                        "entity_type": "company",
                        "raw_name": "Volt Grid",
                        "raw_domain": "voltgrid.example",
                        "raw_country": "NL",
                        "raw_attributes": {},
                    }
                ],
                "claims": [
                    {
                        "mention_index": 0,
                        "predicate_key": "drone_programme",
                        "value": True,
                        "quote": "published its drone fleet audit",
                        "confidence": 90,
                    },
                    {
                        "mention_index": 0,
                        "predicate_key": "industry",
                        "value": "energy",
                        "confidence": 80,
                    },
                ],
            }
        if name == "account_card":
            return {"bullets": ["Runs drones"], "next_step": "Send the case study"}
        if name == "identity_tiebreak":
            return {"winner_index": None, "confidence": 0, "reason": "unsure"}
        raise AssertionError(f"unexpected prompt {name} in the column runs scenario")


@pytest.mark.asyncio
async def test_column_runs_end_to_end(conninfo: str) -> None:
    pool = AsyncConnectionPool(
        conninfo,
        min_size=1,
        max_size=1,
        open=False,
        configure=_configure,
        kwargs={"autocommit": False, "prepare_threshold": None},
    )
    await pool.open()
    try:
        await _column_runs_scenario(pool)
    finally:
        await pool.close()


async def _as_p3_user(db: Db, sql: str, params: tuple[Any, ...] = ()) -> Any:
    async with db.pool.connection() as conn:
        await conn.execute("reset role")
        await conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(USER_P3),))
        await conn.execute("set local role authenticated")
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall() if cur.description else []
        await conn.commit()
        await conn.execute("set role service_role")
    return rows[0][0] if rows else None


async def _seed_p3(db: Db) -> tuple[UUID, dict[str, UUID]]:
    """The P3 tenant: an owner, an Active Brief with the FlytBase and two formula columns, and a
    website radar. Returns the radar and the workspace's predicate ids by key."""
    await db.admin("insert into auth.users (id, email) values (%s, 'owner@p3.test')", (USER_P3,))
    await db.admin("insert into public.organisation (id, name) values (%s, 'P3 Org')", (ORG_P3,))
    await db.admin(
        "insert into public.workspace (id, org_id, name) values (%s, %s, 'P3')", (WS_P3, ORG_P3)
    )
    await db.admin(
        "insert into public.member (id, org_id, user_id, email)"
        " values (%s, %s, %s, 'owner@p3.test')",
        (MEMBER_P3, ORG_P3, USER_P3),
    )
    await db.admin(
        "insert into public.role_assignment (org_id, member_id, role) values (%s, %s, 'Owner')",
        (ORG_P3, MEMBER_P3),
    )
    await db.admin(
        "insert into public.brief (id, org_id, workspace_id, name, state)"
        " values (%s, %s, %s, 'Drone utilities', 'Active')",
        (BRIEF_P3, ORG_P3, WS_P3),
    )
    config = {
        "hypothesis": "Utilities that run drone programmes",
        "predicates": P3_COLUMNS,
        "evidence_rules": FLYTBASE["evidence_rules"],
    }
    version_id = await db.one(
        "insert into public.brief_version"
        " (brief_id, org_id, workspace_id, version, config, rubric, weights)"
        " values (%s, %s, %s, 1, %s::jsonb, '{}', '{}'::jsonb) returning id",
        (BRIEF_P3, ORG_P3, WS_P3, json.dumps(config)),
    )
    await db.admin(
        "update public.brief set current_version_id = %s where id = %s", (version_id, BRIEF_P3)
    )
    radar = await db.one(
        "insert into public.radar (org_id, workspace_id, brief_id, connector_type, name)"
        " values (%s, %s, %s, 'website', 'site') returning id",
        (ORG_P3, WS_P3, BRIEF_P3),
    )
    rows = await db.admin("select key, id from public.predicate where workspace_id = %s", (WS_P3,))
    return UUID(str(radar)), {str(k): UUID(str(v)) for k, v in rows}


async def _column_runs_scenario(pool: AsyncConnectionPool) -> None:
    db = Db(pool)
    radar, predicates = await _seed_p3(db)
    formula, label = predicates["has_programme_and_sites"], predicates["sites_label"]
    entity: Any = None

    async def cell(predicate: UUID) -> tuple[Any, ...]:
        row = await _as_p3_user(
            db,
            "select array[state, value::text] from public.cells(%s, %s, %s)",
            (WS_P3, [entity], [predicate]),
        )
        return tuple(row)

    claims_sql = "select count(*) from public.claim where entity_id = %s and predicate_id = %s"
    active_sql = (
        "select id from public.claim where entity_id = %s and predicate_id = %s"
        " and status = 'active'"
    )
    created_sql = (
        "select count(*) from public.outbox where event = 'claim.created'"
        " and payload ->> 'entity_id' = %s and payload ->> 'predicate_id' = %s"
    )
    runs_sql = (
        "select status, inputs_hash = app.run_inputs_hash(entity_id, predicate_id), claim_id"
        " from public.predicate_run where entity_id = %s and predicate_id = %s"
        " order by queued_at, id"
    )

    gateway = P3Gateway()
    connector = InMemoryConnector()
    app = build_app(
        connector,
        DbKillSwitch(pool),
        HealthState(),
        PgHealthStore(pool),
        ai_configured=gateway.configured,
        approvals=PgApprovalStore(pool),
        gateway=gateway,  # type: ignore[arg-type]
        pool=pool,
    )
    async with app.open_async():
        await _drain(app, connector, pool)
        payload = SourcePayload(
            url="https://voltgrid.example/audit",
            fetched_at=datetime.now(UTC),
            content_hash=hashlib.sha256(P3_PAGE.encode()).hexdigest(),
            payload_ref=None,
            language="en",
            raw=P3_PAGE.encode(),
        )
        assert (
            await create_source_records(pool, ORG_P3, WS_P3, f"radar:{radar}", "website", [payload])
            == 1
        )

        # 1. Extracting the drone page creates the input claim.
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        record = await db.one(
            "select id from public.source_record where org_id = %s and content_hash = %s",
            (ORG_P3, payload.content_hash),
        )
        entity = await db.one(
            "select entity_id from public.mention where source_record_id = %s", (record,)
        )
        assert entity is not None
        assert (
            await db.one(
                "select value from public.claim where entity_id = %s and predicate_id = %s"
                " and status = 'active'",
                (entity, predicates["drone_programme"]),
            )
            is True
        )

        # 2. The auto-run computed the formula, once: derived (site_count unknown, so false).
        runs = await db.admin(runs_sql, (entity, formula))
        assert [(r[0], r[1]) for r in runs] == [("succeeded", True)]
        assert await cell(formula) == ("derived", "false")
        assert await cell(label) == ("empty", None)  # no site_count claim: never queued
        run_jobs = [j for j in connector.jobs.values() if j["task_name"] == "column.run"]
        assert [j["status"] for j in run_jobs] == ["succeeded"]
        assert run_jobs[0]["queueing_lock"] == f"run:{run_jobs[0]['args']['run_id']}"

        # 3. A new site_count claim makes the cell stale until the auto-run brings it back.
        await db.admin(
            "select app.create_claim(%s, %s, %s, '40'::jsonb, 'its 40 substations', null, null,"
            " %s, now(), %s, 85::smallint)",
            (ORG_P3, entity, predicates["site_count"], record, CANARY_EXTRACTOR_VERSION),
        )
        assert await cell(formula) == ("stale", "false")
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert await cell(formula) == ("derived", "true")
        assert await cell(label) == ("derived", '"40 sites"')
        assert [r[0] for r in await db.admin(runs_sql, (entity, formula))] == ["succeeded"] * 2

        # 4. Review Focus 5: a re-run with unchanged inputs writes no claim and emits nothing.
        claims_before = await db.one(claims_sql, (entity, formula))
        created_before = await db.one(created_sql, (str(entity), str(formula)))
        active_before = await db.one(active_sql, (entity, formula))
        request = await _as_p3_user(
            db,
            "select public.request_column_run(%s, %s, 'selected', %s, null, %s)",
            (WS_P3, formula, BRIEF_P3, [entity]),
        )
        assert request == {"queued": 1, "cost_usd": 0}
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        runs = await db.admin(runs_sql, (entity, formula))
        assert [r[0] for r in runs] == ["succeeded"] * 3
        assert runs[-1][2] == active_before  # the same claim, reused
        assert await db.one(claims_sql, (entity, formula)) == claims_before
        assert await db.one(created_sql, (str(entity), str(formula))) == created_before
        assert await cell(formula) == ("derived", "true")

        # A redelivered job for that finished run does nothing.
        last_run = await db.one(
            "select id from public.predicate_run where entity_id = %s and predicate_id = %s"
            " order by queued_at desc, id desc limit 1",
            (entity, formula),
        )
        finished_sql = "select finished_at from public.predicate_run where id = %s"
        finished = await db.one(finished_sql, (last_run,))
        await app.tasks["column.run"].defer_async(run_id=str(last_run))
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert await db.one(finished_sql, (last_run,)) == finished
        assert await db.one(claims_sql, (entity, formula)) == claims_before

        # Review Focus 2: site_count goes away; the label re-run finds nothing and the cell shows
        # no_data, not the older derived value.
        await db.admin(
            "update public.claim set status = 'retracted'"
            " where entity_id = %s and predicate_id = %s",
            (entity, predicates["site_count"]),
        )
        assert await cell(label) == ("stale", '"40 sites"')
        estimate = await _as_p3_user(
            db, "select public.estimate_column_run(%s, %s, 'stale')", (WS_P3, label)
        )
        assert (estimate["count"], estimate["cost_usd"]) == (1, 0)
        request = await _as_p3_user(
            db, "select public.request_column_run(%s, %s, 'stale')", (WS_P3, label)
        )
        assert request == {"queued": 1, "cost_usd": 0}
        await _drain(app, connector, pool)
        assert _failed_jobs(connector) == []
        assert await cell(label) == ("no_data", None)
        assert await db.one(active_sql, (entity, label)) is None
