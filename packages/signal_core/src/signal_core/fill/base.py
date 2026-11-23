"""Shared plumbing for provider and research fills.

Every paid call reserves against the budget guard before it goes out and settles (or
releases) afterwards. Provider attempts are billed through ``predicate_run_attempt``; their
ledger lines deliberately carry no ``predicate_run_id`` (see migration 0044), so a run's cost
is counted once. Research uses the gateway, whose ledger lines *do* carry the run id.

All SQL here is service-role: the column executor is a worker, never a browser session.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.contracts.enums import UsageUnit
from signal_core.crypto import encrypt_secret, lookup_hash
from signal_core.formula import JsonScalar, check_result
from signal_core.metering import (
    BudgetGuard,
    BudgetPaused,
    CallContext,
    UsageLine,
    cost_usd,
)
from signal_core.providers.base import Provider, ProviderInput, ProviderResult

PRICE_SQL = "select usd_per_million from public.model_price where model = %s and unit = %s"
RECORD_ATTEMPT_SQL = "select app.record_attempt(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
CACHE_GET_SQL = "select app.cached_provider_payload(%s, %s, %s)"
CACHE_SET_SQL = "select app.store_provider_payload(%s, %s, %s, %s::jsonb, %s, %s)"
ORDER_SQL = "select provider_order from public.workspace_settings where workspace_id = %s"
ALIAS_SQL = (
    "select value_normalised from public.entity_alias"
    " where entity_id = %s and alias_type = %s"
    " order by confidence desc, created_at, id limit 1"
)
LINKEDIN_SQL = (
    "select value_normalised from public.entity_alias"
    " where entity_id = %s and alias_type = 'profile_url'"
    " order by confidence desc, created_at, id limit 1"
)
RECENT_CONTACT_SQL = (
    "select value_encrypted, verified_at from public.contact_point"
    " where entity_id = %s and type = 'email' and status = 'valid'"
    "   and verified_at is not null and verified_at > now() - interval '30 days'"
    " order by verified_at desc limit 1"
)
ACCOUNT_SQL = (
    "select a.id from public.account a"
    " join public.relationship r on r.to_entity_id = a.entity_id and r.relation = 'works_at'"
    " where r.from_entity_id = %s and a.workspace_id = %s limit 1"
)
STORE_CONTACT_SQL = (
    "insert into public.contact_point"
    " (org_id, workspace_id, entity_id, account_id, type, value_encrypted, value_hash,"
    "  masked_hint, status, verified_at, provider)"
    " values (%s, %s, %s, %s, 'email', %s, %s, %s, %s, %s, %s)"
    " on conflict (org_id, type, value_hash) where value_hash is not null do update set"
    "   value_encrypted = excluded.value_encrypted, masked_hint = excluded.masked_hint,"
    "   status = excluded.status, verified_at = excluded.verified_at,"
    "   provider = excluded.provider, account_id = coalesce(public.contact_point.account_id,"
    "   excluded.account_id)"
)
SOURCE_SQL = "select app.create_source_record(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
CLAIM_SQL = "select app.create_claim(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)"
EXTRACTOR_INSERT_SQL = (
    "insert into public.extractor_version (capability, prompt_version, model, parser_version)"
    " values ('provider', %s, 'adapter', 'v1')"
    " on conflict (capability, prompt_version, model, parser_version) do nothing"
)
EXTRACTOR_GET_SQL = (
    "select id from public.extractor_version where capability = 'provider'"
    " and prompt_version = %s and model = 'adapter' and parser_version = 'v1'"
)
ALIAS_INSERT_SQL = (
    "insert into public.entity_alias"
    " (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id, scope)"
    " values (%s, %s, %s::public.alias_type, %s, %s, %s, 'tenant')"
    " on conflict (org_id, alias_type, value_normalised, entity_id) do nothing"
)


@dataclass
class FillResult:
    """What a fill method decided. The executor turns it into a run outcome."""

    status: str
    claim_id: UUID | None = None
    inputs: Any = None  # run receipt: a dict for provider/research, claim ids for P3
    not_run_reason: dict[str, Any] | None = None
    error: str | None = None
    value: JsonScalar = None


def context_for_run(
    run: dict[str, Any], run_id: UUID, *, predicate_run_id: bool = True
) -> CallContext:
    """A metering context for this column run.

    ``predicate_run_id=False`` is for provider ledger lines: the run's cost comes from its
    attempts, so attributing the ledger line too would charge the cell twice.
    """
    brief = run.get("brief_id")
    return CallContext(
        org_id=UUID(str(run["org_id"])),
        workspace_id=UUID(str(run["workspace_id"])),
        brief_id=UUID(str(brief)) if brief else None,
        job_id=None,
        job_type="column.run",
        job_args={"run_id": str(run_id)},
        predicate_run_id=run_id if predicate_run_id else None,
    )


def as_json(value: Any) -> str:
    return json.dumps(value, allow_nan=False, default=str)


def first(row: Any) -> Any:
    return row[0] if row else None


async def unit_price(
    pool: AsyncConnectionPool, model: str, unit: UsageUnit = UsageUnit.ENRICHMENT_LOOKUP
) -> Decimal:
    """The owner-editable price of one unit, or zero when unpriced.

    ``model_price.usd_per_million`` is, as its name says, per million units; the provider
    adapters and the budget guard both work in USD per single call.
    """
    async with pool.connection() as conn:
        cur = await conn.execute(PRICE_SQL, (model, str(unit)))
        value = first(await cur.fetchone())
    return cost_usd(Decimal(str(value)), 1) if value is not None else Decimal(0)


async def record_attempt(
    pool: AsyncConnectionPool,
    run_id: UUID,
    result: ProviderResult,
    *,
    ordinal: int | None = None,
    provider: str | None = None,
) -> UUID:
    """One row in the run's receipt. A caller may override the key for a verify sub-call."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            RECORD_ATTEMPT_SQL,
            (
                run_id,
                provider or result.provider,
                result.status,
                result.cost_usd,
                result.error,
                ordinal,
                result.confidence if result.status == "found" else None,
                result.raw_ref,
                result.billable_units,
                result.error_kind,
            ),
        )
        return UUID(str(first(await cur.fetchone())))


async def paid_lookup(
    pool: AsyncConnectionPool,
    budget: BudgetGuard,
    ctx: CallContext,
    provider: Provider,
    data: ProviderInput,
    *,
    estimate: Decimal,
) -> ProviderResult:
    """Reserve → lookup → settle/release, in that order.

    A reservation that pauses spends nothing: BudgetPaused travels to the caller so the run
    can end with error 'budget' and keep the attempts already recorded.
    """
    reservation = await budget.reserve(ctx, estimate)
    started = time.monotonic()
    try:
        result = await provider.lookup(data)
    except BaseException:
        await budget.release(reservation)
        raise
    latency_ms = int((time.monotonic() - started) * 1000)
    ledger_ctx = replace(ctx, predicate_run_id=None)
    await budget.settle(
        reservation,
        ledger_ctx,
        "enrich",
        provider.key,
        provider.key,
        [
            UsageLine(
                UsageUnit.ENRICHMENT_LOOKUP,
                result.billable_units,
                result.cost_usd,
            )
        ],
        latency_ms,
        "ok" if result.status != "error" else "rejected",
    )
    return result


def typed(value: Any, run: dict[str, Any]) -> str | None:
    """The P3 result-versus-type message, or None when the value is usable."""
    return check_result(value, str(run["value_type"]), run.get("allowed_values"))


def dotted(payload: Any, path: str) -> Any:
    """Read ``location.country`` from a provider payload; missing paths are None."""
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def mask_email(value: str) -> str:
    """``jane@acme.com`` → ``j•••@acme.com``. Only the mask is readable in SQL or the UI."""
    local, _, domain = value.strip().partition("@")
    if not domain:
        return (local[:1] or "•") + "•••"
    return (local[:1] or "•") + "•••@" + domain


def page_title(url: str) -> str:
    host = urlparse(url).netloc
    return host or url


async def store_contact(
    pool: AsyncConnectionPool,
    run: dict[str, Any],
    email: str,
    provider: str,
    source_id: UUID,
    *,
    status: str = "valid",
    verified_now: datetime | None = None,
) -> UUID:
    """Persist one work email as ciphertext plus a keyed digest and a display mask."""
    async with pool.connection() as conn:
        cur = await conn.execute(ACCOUNT_SQL, (run["entity_id"], run["workspace_id"]))
        account_id = first(await cur.fetchone())
        cur = await conn.execute(
            STORE_CONTACT_SQL,
            (
                run["org_id"],
                run["workspace_id"],
                run["entity_id"],
                account_id,
                encrypt_secret(email),
                lookup_hash(email),
                mask_email(email),
                status,
                verified_now or datetime.now(UTC),
                provider,
            ),
        )
        return UUID(str(first(await cur.fetchone())))


async def create_source(
    pool: AsyncConnectionPool,
    run: dict[str, Any],
    *,
    connector: str,
    source_type: str,
    url: str | None,
    payload_ref: str | None,
    content: str,
) -> UUID:
    """A receipt for a provider or research answer. Never a ``derived:*`` source."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    async with pool.connection() as conn:
        cur = await conn.execute(
            SOURCE_SQL,
            (
                run["org_id"],
                run["workspace_id"],
                connector,
                source_type,
                url,
                datetime.now(UTC),
                digest,
                payload_ref,
                "en",
                content[:100_000],
            ),
        )
        return UUID(str(first(await cur.fetchone())))


async def provider_version(pool: AsyncConnectionPool, key: str) -> UUID:
    """One immutable provenance row per provider adapter, just like a prompt version.

    ``claim.extractor_version_id`` is not nullable: a provider's answer needs the same receipt
    an extraction prompt has. The row is created on first use and reused after that.
    """
    async with pool.connection() as conn:
        await conn.execute(EXTRACTOR_INSERT_SQL, (key,))
        cur = await conn.execute(EXTRACTOR_GET_SQL, (key,))
        return UUID(str(first(await cur.fetchone())))


async def write_claim(
    pool: AsyncConnectionPool,
    run: dict[str, Any],
    value: Any,
    *,
    quote: str,
    source_id: UUID,
    confidence: int,
    extractor_version_id: UUID | None = None,
    observed_at: datetime | None = None,
) -> UUID:
    """Write (or reuse) one active claim. Provider and research claims are evidence."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            CLAIM_SQL,
            (
                run["org_id"],
                run["entity_id"],
                run["predicate_id"],
                as_json(value),
                quote,
                None,
                "en",
                source_id,
                observed_at or datetime.now(UTC),
                extractor_version_id,
                confidence,
            ),
        )
        return UUID(str(first(await cur.fetchone())))


async def write_alias(
    pool: AsyncConnectionPool,
    run: dict[str, Any],
    alias_type: str,
    value: str,
    source_id: UUID,
    *,
    confidence: int = 80,
) -> None:
    """Attach a handle to the entity, deduplicated per entity and normalised value."""
    if not value or not value.strip():
        return
    async with pool.connection() as conn:
        await conn.execute(
            ALIAS_INSERT_SQL,
            (
                run["org_id"],
                run["entity_id"],
                alias_type,
                value.strip()[:1000],
                confidence,
                source_id,
            ),
        )


def budget_error(exc: BudgetPaused) -> str:
    """The run's short error for a stop mid-waterfall (R30 keeps the approval id in the ledger)."""
    return "budget"
