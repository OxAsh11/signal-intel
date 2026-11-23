"""Provider-backed column fills (P6): the work-email waterfall and PDL company fields.

A waterfall tries keyed finders in the workspace's order and stops at the first answer that
passes validation. Findymail's verifier, when keyed, is the validator; otherwise a finder's own
confidence must reach 80. Every call — including a verify and a cache hit — leaves one attempt
in the run, so the cell panel can show what was tried and what each call cost.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from signal_core.fill.base import (
    CACHE_GET_SQL,
    CACHE_SET_SQL,
    LINKEDIN_SQL,
    ORDER_SQL,
    RECENT_CONTACT_SQL,
    FillResult,
    context_for_run,
    create_source,
    dotted,
    first,
    paid_lookup,
    provider_version,
    record_attempt,
    store_contact,
    typed,
    unit_price,
    write_alias,
    write_claim,
)
from signal_core.metering import BudgetGuard, BudgetPaused
from signal_core.providers import configured
from signal_core.providers.base import (
    NOT_SET_UP,
    Provider,
    ProviderInput,
    ProviderResult,
)

MIN_CONFIDENCE = 80
CACHE_TTL_DAYS = 30


def _attempt(
    result: ProviderResult, ordinal: int, *, provider: str | None = None, cached: bool = False
) -> dict[str, Any]:
    """The receipt line for a call. No key, header, prompt or raw vendor body is kept."""
    row: dict[str, Any] = {
        "ordinal": ordinal,
        "provider": provider or result.provider,
        "status": result.status,
        "cost_usd": str(result.cost_usd),
    }
    if result.error:
        row["error"] = result.error
    if cached:
        row["cache"] = True
    return row


def _person_input(run: dict[str, Any], linkedin_url: str | None) -> ProviderInput:
    name = str(run.get("name") or "").strip()
    first_name, _, last_name = name.partition(" ")
    return ProviderInput(
        first_name=first_name or None,
        last_name=last_name.strip() or None,
        full_name=name or None,
        domain=run.get("domain") or None,
        linkedin_url=linkedin_url,
    )


async def _order(pool: AsyncConnectionPool, workspace_id: str) -> list[str]:
    async with pool.connection() as conn:
        cur = await conn.execute(ORDER_SQL, (workspace_id,))
        order = first(await cur.fetchone())
    return [str(k) for k in order] if isinstance(order, list) else []


async def _price_map(pool: AsyncConnectionPool, keys: list[str]) -> dict[str, Decimal]:
    return {key: await unit_price(pool, key) for key in dict.fromkeys(keys)}


async def fill_work_email(
    pool: AsyncConnectionPool,
    budget: BudgetGuard,
    run: dict[str, Any],
    run_id: UUID,
    *,
    providers: list[Provider] | None = None,
    verifier: Provider | None = None,
    today: date | None = None,
    ctx: Any = None,
) -> FillResult:
    """Run the email finder waterfall for one person.

    *providers* and *verifier* are injectable so a test can use fakes instead of keys; in the
    worker they are built from the keyed providers and the workspace's saved order.
    """
    ctx = ctx or context_for_run(run, run_id)
    today = today or datetime.now(UTC).date()

    # Never pay twice: a recent valid address already on this person's contact card wins.
    async with pool.connection() as conn:
        cur = await conn.execute(RECENT_CONTACT_SQL, (run["entity_id"],))
        recent = await cur.fetchone()
    if recent and recent[1] is not None:
        verified_at = recent[1]
        verified_on = (
            verified_at.date().isoformat()
            if isinstance(verified_at, datetime)
            else str(verified_at)
        )
        reason = f"Verified on {verified_on}"
        return FillResult(
            "condition_not_met",
            not_run_reason={"skipped": reason},
            inputs={"attempts": [], "skipped": reason, "verified_on": verified_on},
        )

    async with pool.connection() as conn:
        cur = await conn.execute(LINKEDIN_SQL, (run["entity_id"],))
        linkedin = first(await cur.fetchone())
    data = _person_input(run, linkedin)

    if providers is None:
        order = await _order(pool, str(run["workspace_id"]))
        prices = await _price_map(pool, ["leadmagic", "findymail", "prospeo", "findymail_verify"])
        providers = configured("email_finder", order=order, prices=prices)
        if verifier is None:
            verifier = next(iter(configured("email_verifier", order=order, prices=prices)), None)
    if not providers:
        return FillResult("error", error=NOT_SET_UP, inputs={"attempts": []})

    attempts: list[dict[str, Any]] = []
    ordinal = 0
    last_error: str | None = None
    for provider in providers:
        try:
            result = await paid_lookup(
                pool,
                budget,
                ctx,
                provider,
                data,
                estimate=await unit_price(pool, provider.key),
            )
        except BudgetPaused as exc:
            return FillResult(
                "error",
                error="budget",
                inputs={"attempts": attempts, "approval_id": str(exc.approval_id)},
            )
        ordinal += 1
        await record_attempt(pool, run_id, result, ordinal=ordinal)
        attempts.append(_attempt(result, ordinal))
        if result.status == "error":
            last_error = f"{provider.key}: {result.error or 'error'}"
            continue
        if result.status != "found":
            continue
        email = result.value if isinstance(result.value, str) else None
        if not email or "@" not in email:
            last_error = f"{provider.key}: unusable answer"
            continue

        accepted = False
        if verifier is not None:
            try:
                verdict = await paid_lookup(
                    pool,
                    budget,
                    ctx,
                    verifier,
                    ProviderInput(email=email),
                    estimate=await unit_price(pool, "findymail_verify"),
                )
            except BudgetPaused as exc:
                return FillResult(
                    "error",
                    error="budget",
                    inputs={"attempts": attempts, "approval_id": str(exc.approval_id)},
                )
            ordinal += 1
            await record_attempt(pool, run_id, verdict, ordinal=ordinal, provider=verifier.key)
            attempts.append(_attempt(verdict, ordinal, provider=verifier.key))
            accepted = verdict.status == "found" and str(verdict.value).lower() == "valid"
        else:
            accepted = result.confidence >= MIN_CONFIDENCE
        if not accepted:
            last_error = f"{provider.key}: failed validation"
            continue

        source_id = await create_source(
            pool,
            run,
            connector=f"provider:{provider.key}",
            source_type="api",
            url=None,
            payload_ref=result.raw_ref,
            content=f"{provider.key}:{email}",
        )
        await store_contact(pool, run, email, provider.key, source_id)
        problem = typed(email, run)
        if problem is not None:
            return FillResult(
                "error", error=problem, inputs={"provider": provider.key, "attempts": attempts}
            )
        claim_id = await write_claim(
            pool,
            run,
            email,
            quote=f"{provider.key} found {email[:1]}•••",
            source_id=source_id,
            confidence=result.confidence,
            extractor_version_id=await provider_version(pool, provider.key),
        )
        return FillResult(
            "succeeded",
            claim_id=claim_id,
            inputs={"provider": provider.key, "attempts": attempts},
        )

    if last_error is not None:
        return FillResult("error", error=last_error, inputs={"attempts": attempts})
    return FillResult("no_data", inputs={"attempts": attempts, "tried": len(attempts)})


async def fill_company_field(
    pool: AsyncConnectionPool,
    budget: BudgetGuard,
    run: dict[str, Any],
    run_id: UUID,
    *,
    provider: Provider | None = None,
    ctx: Any = None,
    ttl_days: int = CACHE_TTL_DAYS,
) -> FillResult:
    """Fill one PDL company column. One call fills every due column on the account.

    The whole PDL payload is cached per workspace/account for 30 days; a second column reads
    the cache, records a $0 attempt and writes its own claim without another vendor call.
    """
    ctx = ctx or context_for_run(run, run_id)
    config = run.get("fill_config") or {}
    field = str(config.get("field") or "")
    if not field:
        return FillResult("error", error="a provider company column needs a field")
    key = str(config.get("provider") or "pdl_company")
    entity_id = str(run["entity_id"])

    async with pool.connection() as conn:
        cur = await conn.execute(CACHE_GET_SQL, (run["workspace_id"], key, entity_id))
        cached = first(await cur.fetchone())

    ordinal = 0
    if cached is not None:
        payload = cached.get("payload") if isinstance(cached, dict) else None
        confidence = int(cached.get("confidence") or 0) if isinstance(cached, dict) else 0
        source_id_raw = cached.get("source_record_id") if isinstance(cached, dict) else None
        ordinal += 1
        hit = ProviderResult(
            provider=key,
            status="found",
            value=payload,
            confidence=confidence,
            raw_ref="provider_cache",
        )
        await record_attempt(pool, run_id, hit, ordinal=ordinal)
        attempts = [_attempt(hit, ordinal, cached=True)]
        source_id = (
            UUID(str(source_id_raw))
            if source_id_raw
            else await create_source(
                pool,
                run,
                connector=f"provider:{key}",
                source_type="api",
                url=None,
                payload_ref="provider_cache",
                content=f"cache:{key}:{entity_id}",
            )
        )
    else:
        if provider is None:
            keyed = configured("company", prices={key: await unit_price(pool, key)})
            provider = next((p for p in keyed if p.key == key), None)
        if provider is None:
            return FillResult("error", error=NOT_SET_UP, inputs={"attempts": []})
        try:
            result = await paid_lookup(
                pool,
                budget,
                ctx,
                provider,
                ProviderInput(
                    domain=run.get("domain") or None, company_name=run.get("name") or None
                ),
                estimate=await unit_price(pool, provider.key),
            )
        except BudgetPaused as exc:
            return FillResult(
                "error",
                error="budget",
                inputs={"attempts": [], "approval_id": str(exc.approval_id)},
            )
        ordinal += 1
        await record_attempt(pool, run_id, result, ordinal=ordinal)
        attempts = [_attempt(result, ordinal)]
        if result.status == "error":
            return FillResult(
                "error",
                error=f"{provider.key}: {result.error or 'error'}",
                inputs={"attempts": attempts},
            )
        if result.status != "found" or not isinstance(result.value, dict):
            return FillResult("no_data", inputs={"attempts": attempts})
        payload = result.value
        confidence = result.confidence
        source_id = await create_source(
            pool,
            run,
            connector=f"provider:{key}",
            source_type="api",
            url=None,
            payload_ref=result.raw_ref,
            content=f"{key}:{entity_id}:{result.raw_ref or ''}",
        )
        cached_payload = {
            "payload": payload,
            "confidence": confidence,
            "source_record_id": str(source_id),
            "raw_ref": result.raw_ref,
        }
        async with pool.connection() as conn:
            await conn.execute(
                CACHE_SET_SQL,
                (
                    run["workspace_id"],
                    key,
                    entity_id,
                    json.dumps(cached_payload),
                    result.raw_ref,
                    ttl_days,
                ),
            )
        raw_location = payload.get("location")
        location: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}
        locality = str(location.get("locality") or "")
        region = str(location.get("region") or "")
        country = str(location.get("country") or "")
        await write_alias(
            pool, run, "name_country", " ".join(p for p in (locality, country) if p), source_id
        )
        await write_alias(
            pool, run, "site", ", ".join(p for p in (locality, region, country) if p), source_id
        )

    value = dotted(payload, field)
    if value is None:
        return FillResult("no_data", inputs={"attempts": attempts})
    problem = typed(value, run)
    if problem is not None:
        return FillResult("error", error=problem, inputs={"attempts": attempts})
    claim_id = await write_claim(
        pool,
        run,
        value,
        quote=f"{key}:{field}",
        source_id=source_id,
        confidence=confidence,
        extractor_version_id=await provider_version(pool, key),
    )
    return FillResult("succeeded", claim_id=claim_id, inputs={"attempts": attempts})
