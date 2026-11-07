import json
import time
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from signal_core.contracts.enums import UsageUnit
from signal_core.metering import (
    BudgetPaused,
    CallContext,
    MemoryBudgetGuard,
    MemoryPriceBook,
    PgBudgetGuard,
    PgPriceBook,
    UsageLine,
    cost_usd,
    preview,
)


def make_ctx(brief_id: UUID | None = None, **args: Any) -> CallContext:
    return CallContext(
        org_id=uuid4(),
        workspace_id=uuid4(),
        brief_id=brief_id,
        job_id="j1",
        job_type="enrich",
        job_args=args,
    )


def line(cost: str = "1.00") -> UsageLine:
    return UsageLine(UsageUnit.AI_INPUT_TOKENS, 1000, Decimal(cost))


def test_cost_rounding() -> None:
    assert cost_usd(Decimal("3"), 1500) == Decimal("0.004500")
    assert cost_usd(Decimal("0.5"), 1) == Decimal("0.000001")


async def test_memory_guard_pauses_over_cap() -> None:
    ctx = make_ctx()
    guard = MemoryBudgetGuard()
    guard.set_cap(ctx.workspace_id, None, "day", Decimal("1.00"))
    rid = await guard.reserve(ctx, Decimal("0.60"))
    with pytest.raises(BudgetPaused) as exc:
        await guard.reserve(ctx, Decimal("0.60"))
    assert exc.value.period == "day"
    assert exc.value.cap == Decimal("1.00")
    assert exc.value.spent == Decimal("0.60")
    await guard.release(rid)
    await guard.reserve(ctx, Decimal("0.60"))


async def test_memory_guard_brief_cap_only_for_that_brief() -> None:
    ctx = make_ctx(brief_id=uuid4())
    guard = MemoryBudgetGuard()
    guard.set_cap(ctx.workspace_id, ctx.brief_id, "month", Decimal("0.50"))
    with pytest.raises(BudgetPaused):
        await guard.reserve(ctx, Decimal("0.60"))
    other = CallContext(ctx.org_id, ctx.workspace_id, None, None, "enrich", {})
    await guard.reserve(other, Decimal("0.60"))


async def test_settle_records_rejected_status() -> None:
    ctx = make_ctx()
    guard = MemoryBudgetGuard()
    guard.set_cap(ctx.workspace_id, None, "day", Decimal("1.00"))
    rid = await guard.reserve(ctx, Decimal("0.50"))
    await guard.settle(rid, ctx, "classify", "anthropic", "m", [line("0.50")], 12, "rejected")
    assert guard.settles[0].status == "rejected"
    assert guard.lines == [line("0.50")]
    # settled spend still counts against the cap
    with pytest.raises(BudgetPaused):
        await guard.reserve(ctx, Decimal("0.60"))


def test_preview_duration_uses_concurrency() -> None:
    est = preview(100, 1000, 200, (Decimal("3"), Decimal("15")), 2.0, 4)
    assert est.duration_seconds == 50
    assert est.volume == 100
    # 100 * (1000*3 + 200*15) / 1e6
    assert est.cost_usd == Decimal("0.600000")


def test_preview_rounds_duration_up_and_validates() -> None:
    assert preview(3, 0, 0, (Decimal(0), Decimal(0)), 1.0, 2).duration_seconds == 2
    assert preview(0, 1, 1, (Decimal(1), Decimal(1)), 1.0, 1).cost_usd == Decimal("0.000000")
    with pytest.raises(ValueError):
        preview(1, 1, 1, (Decimal(1), Decimal(1)), 1.0, 0)
    with pytest.raises(ValueError):
        preview(-1, 1, 1, (Decimal(1), Decimal(1)), 1.0, 1)


async def test_memory_price_book_unknown_model_is_none() -> None:
    book = MemoryPriceBook({("m", UsageUnit.AI_INPUT_TOKENS): Decimal("3")})
    assert await book.price("m", UsageUnit.AI_INPUT_TOKENS) == Decimal("3")
    assert await book.price("zzz", UsageUnit.AI_INPUT_TOKENS) is None


# -- Pg classes over a fake pool ------------------------------------------------


class FakeCursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def fetchone(self) -> Any:
        return self._row


class FakeConn:
    def __init__(self, pool: "FakePool") -> None:
        self._pool = pool

    async def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self._pool.calls.append((sql, params))
        return FakeCursor(self._pool.rows.pop(0) if self._pool.rows else None)

    @asynccontextmanager
    async def transaction(self) -> Any:
        yield


class FakePool:
    def __init__(self, rows: list[Any] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, Any]] = []

    @asynccontextmanager
    async def connection(self) -> Any:
        yield FakeConn(self)


async def test_pg_reserve_passes_params_and_returns_id() -> None:
    rid = uuid4()
    approval = uuid4()
    pool = FakePool([({"ok": True, "reservation_id": str(rid)},)])
    ctx = make_ctx(approval_id=str(approval), x=1)
    got = await PgBudgetGuard(pool).reserve(ctx, Decimal("0.25"))  # type: ignore[arg-type]
    assert got == rid
    sql, params = pool.calls[0]
    assert "app.reserve_budget" in sql
    assert params[0] == ctx.workspace_id
    assert params[1] == ctx.brief_id
    assert params[2] == Decimal("0.25")
    assert params[3] == "enrich"
    assert json.loads(params[4]) == {"approval_id": str(approval), "x": 1}
    assert params[5] == str(approval)


async def test_pg_reserve_without_approval_passes_none() -> None:
    pool = FakePool([({"ok": True, "reservation_id": str(uuid4())},)])
    await PgBudgetGuard(pool).reserve(make_ctx(), Decimal("1"))  # type: ignore[arg-type]
    assert pool.calls[0][1][5] is None


async def test_pg_reserve_raises_budget_paused() -> None:
    approval = uuid4()
    refusal = {
        "ok": False,
        "approval_id": str(approval),
        "period": "month",
        "cap": 10,
        "spent": "9.5",
    }
    pool = FakePool([(refusal,)])
    with pytest.raises(BudgetPaused) as exc:
        await PgBudgetGuard(pool).reserve(make_ctx(), Decimal("1"))  # type: ignore[arg-type]
    assert exc.value.approval_id == approval
    assert exc.value.period == "month"
    assert exc.value.cap == Decimal("10")
    assert exc.value.spent == Decimal("9.5")


async def test_pg_settle_sends_decimal_strings() -> None:
    pool = FakePool()
    ctx = make_ctx(brief_id=uuid4())
    rid = uuid4()
    await PgBudgetGuard(pool).settle(  # type: ignore[arg-type]
        rid, ctx, "classify", "anthropic", "m", [line("0.004500")], 42, "ok"
    )
    sql, params = pool.calls[0]
    assert "app.settle_reservation" in sql
    assert params[0] == rid
    actual = json.loads(params[1])
    assert actual == {
        "capability": "classify",
        "provider": "anthropic",
        "model": "m",
        "latency_ms": 42,
        "status": "ok",
        "brief_id": str(ctx.brief_id),
        "job_id": "j1",
        "lines": [{"unit": "ai_input_tokens", "quantity": 1000, "cost_usd": "0.004500"}],
    }


async def test_pg_release_sends_null() -> None:
    pool = FakePool()
    rid = uuid4()
    await PgBudgetGuard(pool).release(rid)  # type: ignore[arg-type]
    sql, params = pool.calls[0]
    assert "app.settle_reservation" in sql
    assert params == (rid, None)


async def test_pg_price_book_caches_300s(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    pool = FakePool([(Decimal("3.0000"),), (Decimal("4.0000"),)])
    book = PgPriceBook(pool)  # type: ignore[arg-type]
    assert await book.price("m", UsageUnit.AI_INPUT_TOKENS) == Decimal("3.0000")
    now[0] += 299
    assert await book.price("m", UsageUnit.AI_INPUT_TOKENS) == Decimal("3.0000")
    assert len(pool.calls) == 1
    assert pool.calls[0][1] == ("m", "ai_input_tokens")
    now[0] += 2
    assert await book.price("m", UsageUnit.AI_INPUT_TOKENS) == Decimal("4.0000")
    assert len(pool.calls) == 2


async def test_pg_price_book_unknown_is_none() -> None:
    book = PgPriceBook(FakePool([None]))  # type: ignore[arg-type]
    assert await book.price("nope", UsageUnit.FETCH) is None
