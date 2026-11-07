"""Real-database proofs for the budget SQL. Uses a fixed fixture org and workspace."""

import asyncio
from decimal import Decimal
from uuid import UUID

import pytest
from psycopg_pool import AsyncConnectionPool

from signal_core.config import Settings
from signal_core.db import make_pool
from signal_core.metering import BudgetPaused, CallContext, PgBudgetGuard

FIXTURE_ORG = UUID("00000000-0000-4000-8000-0000000000f1")
FIXTURE_WS = UUID("00000000-0000-4000-8000-0000000000f2")


async def open_pool() -> AsyncConnectionPool:
    url = Settings().database_url_session
    assert url is not None
    pool = make_pool(url.get_secret_value())
    await pool.open()
    return pool


async def ensure_fixture(pool: AsyncConnectionPool, headroom: Decimal) -> None:
    """Idempotently create the fixture tenant and set its day cap to today's spend + headroom."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "insert into public.organisation (id, name) values (%s, 'SI test fixtures') "
            "on conflict do nothing",
            (FIXTURE_ORG,),
        )
        await conn.execute(
            "insert into public.workspace (id, org_id, name) values (%s, %s, 'SI test fixtures') "
            "on conflict do nothing",
            (FIXTURE_WS, FIXTURE_ORG),
        )
        cur = await conn.execute("select app.period_spend(%s, null, 'day')", (FIXTURE_WS,))
        row = await cur.fetchone()
        assert row is not None
        cap = Decimal(str(row[0])) + headroom
        await conn.execute(
            "insert into public.budget (org_id, workspace_id, brief_id, period, cap_usd) "
            "values (%s, %s, null, 'day', %s) "
            "on conflict on constraint budget_one_per_scope "
            "do update set cap_usd = excluded.cap_usd",
            (FIXTURE_ORG, FIXTURE_WS, cap),
        )


async def reserve(pool: AsyncConnectionPool, estimate: int, approval: UUID | None = None) -> dict:  # type: ignore[type-arg]
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "select app.reserve_budget(%s, null, %s, 'test.concurrency', '{}'::jsonb, %s)",
            (FIXTURE_WS, estimate, approval),
        )
        row = await cur.fetchone()
    assert row is not None
    return dict(row[0])


async def release(pool: AsyncConnectionPool, reservation_id: str) -> None:
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("select app.settle_reservation(%s, null)", (reservation_id,))


@pytest.mark.db
async def test_concurrent_reserves_cannot_both_overspend() -> None:
    pool = await open_pool()
    try:
        await ensure_fixture(pool, Decimal(10))
        first, second = await asyncio.gather(reserve(pool, 6), reserve(pool, 6))
        results = [first, second]
        ok = [r for r in results if r["ok"]]
        assert len(ok) == 1, results
        for r in ok:
            await release(pool, r["reservation_id"])
    finally:
        await pool.close()


@pytest.mark.db
async def test_approved_job_not_paused_again_in_sql() -> None:
    pool = await open_pool()
    try:
        await ensure_fixture(pool, Decimal(1))
        paused = await reserve(pool, 6)
        assert not paused["ok"]
        approval_id = UUID(str(paused["approval_id"]))
        # Decide as the owner would: a direct update stands in for decide_budget_approval.
        async with pool.connection() as conn, conn.transaction():
            await conn.execute(
                "update public.budget_approval set status = 'approved', decided_at = now() "
                "where id = %s",
                (approval_id,),
            )
        # Without the approval id the cap still blocks; with it, the reservation goes through.
        assert not (await reserve(pool, 6))["ok"]
        guard = PgBudgetGuard(pool)
        ctx = CallContext(
            FIXTURE_ORG,
            FIXTURE_WS,
            None,
            None,
            "test.concurrency",
            {"approval_id": str(approval_id)},
        )
        # job_type on the approval is 'test.concurrency' and brief is null: it matches.
        reservation = await guard.reserve(ctx, Decimal(6))
        await guard.release(reservation)
        # A different approval id never errors, it just does not bypass.
        other = CallContext(FIXTURE_ORG, FIXTURE_WS, None, None, "test.concurrency", {})
        with pytest.raises(BudgetPaused):
            await guard.reserve(other, Decimal(6))
    finally:
        await pool.close()


@pytest.mark.db
async def test_pg_claim_approved_is_exclusive() -> None:
    from signal_core.metering import PgApprovalStore

    pool = await open_pool()
    try:
        await ensure_fixture(pool, Decimal(10))
        async with pool.connection() as conn, conn.transaction():
            await conn.execute(
                "insert into public.budget_approval (org_id, workspace_id, job_type, job_args, "
                "estimate_usd, reason, status, decided_at) "
                "values (%s, %s, 'test.claim', '{}', 1, 'test', 'approved', now())",
                (FIXTURE_ORG, FIXTURE_WS),
            )
        store = PgApprovalStore(pool)
        a, b = await asyncio.gather(store.claim_approved(100), store.claim_approved(100))
        mine = [x for x in a + b if x.job_type == "test.claim"]
        assert len(mine) == 1  # the row stays claimed: that is the end state
    finally:
        await pool.close()
