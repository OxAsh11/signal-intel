"""S6 metering: price book, usage ledger, budget guard and cost preview.

The budget check comes before the call: a reservation over a cap never goes through.
Nothing here logs prompt text or completions; at most ids, amounts and statuses.

Job/approval contract (R30). When a call would exceed a cap, `reserve` raises BudgetPaused and an
approval is recorded from the CallContext. After it is approved, `budget.resume` re-queues the job
only if all of these hold:
  * `CallContext.job_type` is a registered Procrastinate task name;
  * `CallContext.job_args` are exactly that task's keyword arguments;
  * the task accepts an `approval_id` keyword argument (passed through to CallContext.job_args so
    the retried call is let through).
An approval covers one call's estimate, not a whole job. Calls from the eval CLI are not resumable:
their pause is reported and nothing re-queues them.
"""

import json
import math
import time
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool

from signal_core.contracts.enums import UsageUnit
from signal_core.logging import get_logger

log = get_logger(__name__)

PRICE_TTL_SECONDS = 300.0
_SIX_PLACES = Decimal("0.000001")
_MILLION = Decimal(1_000_000)

SettleStatus = Literal["ok", "rejected"]


class BudgetPaused(Exception):
    """A reservation would exceed a cap; an approval request was raised."""

    def __init__(self, approval_id: UUID, period: str, cap: Decimal, spent: Decimal) -> None:
        super().__init__(f"budget paused ({period}): cap {cap}, spent {spent}")
        self.approval_id = approval_id
        self.period = period
        self.cap = cap
        self.spent = spent


@dataclass(frozen=True)
class CallContext:
    org_id: UUID
    workspace_id: UUID
    brief_id: UUID | None
    job_id: str | None
    job_type: str
    job_args: dict[str, Any]


@dataclass
class UsageLine:
    unit: UsageUnit
    quantity: int
    cost_usd: Decimal


@dataclass(frozen=True)
class CostEstimate:
    volume: int
    cost_usd: Decimal
    duration_seconds: int


def cost_usd(per_million: Decimal, quantity: int) -> Decimal:
    """Cost of `quantity` units at `per_million` USD per million, half-up to 6 places."""
    return (per_million * quantity / _MILLION).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)


def preview(
    items: int,
    avg_input_tokens: int,
    avg_output_tokens: int,
    model_prices: tuple[Decimal, Decimal],
    seconds_per_item: float,
    concurrency: int,
) -> CostEstimate:
    """S6 cost and time preview. `model_prices` is (input, output) USD per million tokens."""
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    if items < 0:
        raise ValueError("items must be >= 0")
    in_price, out_price = model_prices
    per_item = avg_input_tokens * in_price + avg_output_tokens * out_price
    total = (per_item * items / _MILLION).quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
    duration = math.ceil(items * seconds_per_item / concurrency)
    return CostEstimate(volume=items, cost_usd=total, duration_seconds=duration)


class PriceBook(Protocol):
    async def price(self, model: str, unit: UsageUnit) -> Decimal | None: ...


class BudgetGuard(Protocol):
    async def reserve(self, ctx: CallContext, estimate_usd: Decimal) -> UUID: ...

    async def settle(
        self,
        reservation_id: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[UsageLine],
        latency_ms: int,
        status: SettleStatus,
    ) -> None: ...

    async def release(self, reservation_id: UUID) -> None: ...


@dataclass(frozen=True)
class ApprovedJob:
    """A budget approval that was granted and whose paused job has not been re-queued yet."""

    id: UUID
    job_type: str
    job_args: dict[str, Any]


class ApprovalStore(Protocol):
    async def claim_approved(self, limit: int) -> list[ApprovedJob]: ...

    async def unclaim(self, approval_id: UUID) -> None: ...

    async def release_stale(self) -> dict[str, int]:
        """Release leaked reservations and expire old pending approvals; returns counts only."""
        ...


# -- Postgres implementations ---------------------------------------------------


class PgPriceBook:
    """Reads public.model_price, cached per (model, unit) for five minutes."""

    def __init__(self, pool: AsyncConnectionPool, ttl: float = PRICE_TTL_SECONDS) -> None:
        self._pool = pool
        self._ttl = ttl
        self._cache: dict[tuple[str, UsageUnit], tuple[Decimal | None, float]] = {}

    async def price(self, model: str, unit: UsageUnit) -> Decimal | None:
        key = (model, unit)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[1] < self._ttl:
            return hit[0]
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select usd_per_million from public.model_price where model = %s and unit = %s",
                (model, str(unit)),
            )
            row = await cur.fetchone()
        value = Decimal(str(row[0])) if row else None
        self._cache[key] = (value, now)
        return value


class PgBudgetGuard:
    """Budget guard over the app.reserve_budget / app.settle_reservation SQL functions."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def reserve(self, ctx: CallContext, estimate_usd: Decimal) -> UUID:
        approval = ctx.job_args.get("approval_id")
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "select app.reserve_budget(%s, %s, %s, %s, %s::jsonb, %s)",
                (
                    ctx.workspace_id,
                    ctx.brief_id,
                    estimate_usd,
                    ctx.job_type,
                    json.dumps(ctx.job_args, default=str),
                    approval,
                ),
            )
            row = await cur.fetchone()
        result: dict[str, Any] = row[0] if row else {}
        if not result.get("ok"):
            raise BudgetPaused(
                approval_id=UUID(str(result["approval_id"])),
                period=str(result["period"]),
                cap=Decimal(str(result["cap"])),
                spent=Decimal(str(result["spent"])),
            )
        reservation_id = UUID(str(result["reservation_id"]))
        log.info("budget.reserved", reservation_id=str(reservation_id), estimate=str(estimate_usd))
        return reservation_id

    async def settle(
        self,
        reservation_id: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[UsageLine],
        latency_ms: int,
        status: SettleStatus,
    ) -> None:
        actual = {
            "capability": capability,
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "status": status,
            "brief_id": str(ctx.brief_id) if ctx.brief_id else None,
            "job_id": ctx.job_id,
            "lines": [
                {"unit": str(ln.unit), "quantity": ln.quantity, "cost_usd": str(ln.cost_usd)}
                for ln in lines
            ],
        }
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "select app.settle_reservation(%s, %s::jsonb)",
                (reservation_id, json.dumps(actual)),
            )
        log.info("budget.settled", reservation_id=str(reservation_id), status=status)

    async def release(self, reservation_id: UUID) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "select app.settle_reservation(%s, %s)",
                (reservation_id, None),
            )
        log.info("budget.released", reservation_id=str(reservation_id))


class PgApprovalStore:
    """Approved budget approvals, claimed at most once by setting resumed_at."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def claim_approved(self, limit: int) -> list[ApprovedJob]:
        # One statement: the claim (resumed_at = now()) is atomic, so overlapping ticks or
        # replicas can never receive the same approval.
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "update public.budget_approval set resumed_at = now() where id in ("
                "select id from public.budget_approval "
                "where status = 'approved' and resumed_at is null "
                "order by decided_at for update skip locked limit %s) "
                "returning id, job_type, job_args, resumed_at",
                (limit,),
            )
            rows = await cur.fetchall()
        return [ApprovedJob(UUID(str(r[0])), str(r[1]), dict(r[2] or {})) for r in rows]

    async def unclaim(self, approval_id: UUID) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "update public.budget_approval set resumed_at = null "
                "where id = %s and resumed_at is not null",
                (approval_id,),
            )

    async def release_stale(self) -> dict[str, int]:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute("select app.release_stale_reservations()")
            row = await cur.fetchone()
        result = row[0] if row else {}
        return {k: int(result.get(k, 0)) for k in ("released", "expired")}


# -- In-memory test doubles -----------------------------------------------------


class MemoryPriceBook:
    def __init__(self, prices: dict[tuple[str, UsageUnit], Decimal] | None = None) -> None:
        self.prices = dict(prices or {})

    async def price(self, model: str, unit: UsageUnit) -> Decimal | None:
        return self.prices.get((model, unit))


@dataclass
class SettleRecord:
    reservation_id: UUID
    ctx: CallContext
    capability: str
    provider: str
    model: str
    lines: list[UsageLine]
    latency_ms: int
    status: SettleStatus


@dataclass
class _Reservation:
    workspace_id: UUID
    brief_id: UUID | None
    amount: Decimal


@dataclass
class MemoryBudgetGuard:
    """Same semantics as PgBudgetGuard: caps per (workspace, brief | None, period).

    Spend counts settled ok/rejected lines plus unreleased reservations. Periods are not
    windowed in memory: each cap sees all spend recorded in this guard.
    """

    caps: dict[tuple[UUID, UUID | None, str], Decimal] = field(default_factory=dict)
    settles: list[SettleRecord] = field(default_factory=list)
    reservations: dict[UUID, _Reservation] = field(default_factory=dict)
    paused: list[BudgetPaused] = field(default_factory=list)
    approved: set[UUID] = field(default_factory=set)
    _settled_spend: list[tuple[UUID, UUID | None, Decimal]] = field(default_factory=list)

    @property
    def lines(self) -> list[UsageLine]:
        return [ln for s in self.settles for ln in s.lines]

    def set_cap(self, workspace_id: UUID, brief_id: UUID | None, period: str, cap: Decimal) -> None:
        self.caps[(workspace_id, brief_id, period)] = cap

    def _spent(self, workspace_id: UUID, brief_id: UUID | None) -> Decimal:
        total = Decimal(0)
        for ws, br, amount in self._settled_spend:
            if ws == workspace_id and (brief_id is None or br == brief_id):
                total += amount
        for r in self.reservations.values():
            if r.workspace_id == workspace_id and (brief_id is None or r.brief_id == brief_id):
                total += r.amount
        return total

    def approve(self, approval_id: UUID) -> None:
        self.approved.add(approval_id)

    async def reserve(self, ctx: CallContext, estimate_usd: Decimal) -> UUID:
        scopes = [None, ctx.brief_id] if ctx.brief_id else [None]
        raw = ctx.job_args.get("approval_id")
        bypass = raw is not None and UUID(str(raw)) in self.approved
        for (ws, brief, period), cap in self.caps.items():
            if bypass or ws != ctx.workspace_id or brief not in scopes:
                continue
            spent = self._spent(ws, brief)
            if spent + estimate_usd > cap:
                err = BudgetPaused(uuid4(), period, cap, spent)
                self.paused.append(err)
                raise err
        reservation_id = uuid4()
        self.reservations[reservation_id] = _Reservation(
            ctx.workspace_id, ctx.brief_id, estimate_usd
        )
        return reservation_id

    async def settle(
        self,
        reservation_id: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[UsageLine],
        latency_ms: int,
        status: SettleStatus,
    ) -> None:
        self.reservations.pop(reservation_id, None)
        self.settles.append(
            SettleRecord(
                reservation_id, ctx, capability, provider, model, list(lines), latency_ms, status
            )
        )
        self._settled_spend.append(
            (ctx.workspace_id, ctx.brief_id, sum((ln.cost_usd for ln in lines), Decimal(0)))
        )

    async def release(self, reservation_id: UUID) -> None:
        self.reservations.pop(reservation_id, None)


class MemoryApprovalStore:
    def __init__(self, approvals: list[ApprovedJob] | None = None) -> None:
        self.approvals = list(approvals or [])
        self.resumed: set[UUID] = set()
        self.release_stale_calls = 0

    async def claim_approved(self, limit: int) -> list[ApprovedJob]:
        claimed = [a for a in self.approvals if a.id not in self.resumed][:limit]
        self.resumed.update(a.id for a in claimed)  # no await between check and claim: atomic
        return claimed

    async def unclaim(self, approval_id: UUID) -> None:
        self.resumed.discard(approval_id)

    async def release_stale(self) -> dict[str, int]:
        self.release_stale_calls += 1
        return {"released": 0, "expired": 0}
