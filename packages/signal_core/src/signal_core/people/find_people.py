"""Find people at one account with PDL Person Search (P6).

The job is deliberately small: load the account and the lens Brief's persona titles, reserve the
full estimate, make one provider call, then write every returned person with a works_at link. A
result whose company cannot be matched to an account becomes an ``unlinked_person`` Review item,
so the People page never shows a person without an account. Person values are encrypted in the
worker; the database and the browser only ever see the keyed digest and a masked hint.

No key is required in CI: with no configured provider the job spends nothing and does nothing.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import httpx
from psycopg_pool import AsyncConnectionPool

from signal_core import providers as registry
from signal_core.contracts.enums import UsageUnit
from signal_core.crypto import encrypt_secret, lookup_hash
from signal_core.fill.base import unit_price
from signal_core.logging import get_logger
from signal_core.metering import BudgetGuard, CallContext, PgBudgetGuard, UsageLine
from signal_core.providers.base import ProviderInput, ProviderResult

log = get_logger(__name__)

ACCOUNT_SQL = """
select a.id, a.org_id, a.workspace_id, a.entity_id, e.display_name,
       (select al.value_normalised from public.entity_alias al
         where al.entity_id = a.entity_id and al.alias_type = 'domain'
         order by al.confidence desc, al.created_at, al.id limit 1) as domain,
       (select btrim(m.raw_country) from public.mention m
         where m.entity_id = a.entity_id and btrim(coalesce(m.raw_country, '')) <> ''
         order by m.created_at desc, m.id desc limit 1) as country,
       (select coalesce(jsonb_agg(al.value_normalised), '[]'::jsonb) from public.entity_alias al
         where al.entity_id = a.entity_id and al.alias_type = 'site') as sites
  from public.account a
  join public.entity e on e.id = a.entity_id
 where a.id = %s and a.workspace_id = %s
"""
PERSONAS_SQL = (
    "select bv.config -> 'personas' from public.brief b"
    " join public.brief_version bv on bv.id = b.current_version_id where b.id = %s"
)
HOME_LENS_SQL = "select app.home_lens(%s, %s)"
SOURCE_SQL = "select app.create_source_record(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
UPSERT_SQL = "select app.upsert_found_person(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)"
QUEUE_UNLINKED_SQL = "select app.queue_unlinked_person(%s, %s)"
MATCH_DOMAIN_SQL = (
    "select a.id from public.account a"
    " join public.entity_alias al on al.entity_id = a.entity_id"
    "  and al.alias_type = 'domain'"
    " where a.workspace_id = %s and lower(al.value_normalised) = %s limit 1"
)
MATCH_NAME_SQL = (
    "select a.id from public.account a join public.entity e on e.id = a.entity_id"
    " where a.workspace_id = %s and lower(e.display_name) = %s limit 1"
)
FLAG_SQL = "select app.set_entity_flag(%s, %s, 'location_mismatch', %s)"
PROFILE_ALIAS_SQL = (
    "select entity_id from public.entity_alias"
    " where org_id = %s and alias_type = 'profile_url' and value_normalised = %s limit 1"
)
EMAIL_ALIAS_SQL = (
    "select entity_id from public.contact_point"
    " where org_id = %s and type = 'email' and value_hash = %s limit 1"
)
CREATE_ENTITY_SQL = "select app.create_entity(%s, 'person'::public.entity_type, %s)"
ALIAS_PROFILE_SQL = (
    "select app.create_entity_alias(%s, %s, 'profile_url'::public.alias_type, %s, 80::smallint, %s)"
)
ALIAS_EMAIL_SQL = (
    "select app.create_entity_alias(%s, %s, 'email'::public.alias_type, %s, 80::smallint, %s)"
)
CONTACT_INSERT_SQL = (
    "insert into public.contact_point"
    " (org_id, workspace_id, entity_id, type, value_encrypted, value_hash,"
    "  masked_hint, provider)"
    " values (%s, %s, %s, 'email', %s, %s, %s, 'pdl_person')"
    " on conflict (org_id, type, value_hash) where value_hash is not null do nothing"
)


class PersonSearchProvider(Protocol):
    key: str

    async def lookup(self, input: ProviderInput) -> ProviderResult: ...


@dataclass(frozen=True)
class Account:
    id: UUID
    org_id: UUID
    workspace_id: UUID
    entity_id: UUID
    name: str
    domain: str | None
    country: str | None
    sites: tuple[str, ...]


def mask_email(email: str) -> str:
    """A hint safe for the table: ``j•••@acme.com``. The full value stays in the worker."""
    local, sep, domain = email.strip().partition("@")
    head = local[:1] if local else "*"
    return f"{head}•••@{domain}" if sep else f"{head}•••"


def persona_titles(personas: dict[str, Any]) -> tuple[str, ...]:
    """Flatten buyer/user/influencer titles, keeping the Brief's role order."""
    titles: list[str] = []
    for role in ("buyer", "user", "influencer"):
        values = personas.get(role)
        if not isinstance(values, list):
            continue
        for value in values:
            title = str(value).strip()
            if title and title not in titles:
                titles.append(title)
    return tuple(titles)


def persona_for(title: str | None, personas: dict[str, Any]) -> str | None:
    lowered = (title or "").lower()
    if not lowered:
        return None
    for role in ("buyer", "user", "influencer"):
        values = personas.get(role)
        if not isinstance(values, list):
            continue
        if any(str(t).strip().lower() in lowered for t in values if str(t).strip()):
            return role
    return None


def location_mismatch(person: dict[str, Any], account: Account) -> str | None:
    """Review Focus 2: a person in another city/country is linked but flagged, never merged away."""
    city = str(person.get("location_locality") or person.get("location_name") or "").strip()
    country = str(person.get("location_country") or "").strip()
    if not city and not country:
        return None
    if country and account.country and country.lower() != account.country.lower():
        expected = account.country
        found = city or country
        return f"Possibly a different office ({found} vs {expected})"
    if city and account.sites and not any(city.lower() in site.lower() for site in account.sites):
        expected = account.country or "the account's sites"
        return f"Possibly a different office ({city} vs {expected})"
    return None


async def _one(
    pool: AsyncConnectionPool, sql: str, params: tuple[Any, ...]
) -> tuple[Any, ...] | None:
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def load_account(
    pool: AsyncConnectionPool, account_id: UUID, workspace_id: UUID
) -> Account | None:
    row = await _one(pool, ACCOUNT_SQL, (account_id, workspace_id))
    if row is None:
        return None
    sites = row[7] if isinstance(row[7], list) else []
    return Account(
        id=UUID(str(row[0])),
        org_id=UUID(str(row[1])),
        workspace_id=UUID(str(row[2])),
        entity_id=UUID(str(row[3])),
        name=str(row[4] or ""),
        domain=str(row[5]) if row[5] else None,
        country=str(row[6]) if row[6] else None,
        sites=tuple(str(s) for s in sites),
    )


async def load_personas(pool: AsyncConnectionPool, brief_id: UUID | None) -> dict[str, Any]:
    if brief_id is None:
        return {}
    row = await _one(pool, PERSONAS_SQL, (brief_id,))
    raw = row[0] if row else None
    return raw if isinstance(raw, dict) else {}


async def match_company(
    pool: AsyncConnectionPool, workspace_id: UUID, record: dict[str, Any]
) -> UUID | None:
    """Match the PDL company fields to an account of this workspace, domain first, then name."""
    website = str(record.get("job_company_website") or "").strip().lower()
    name = str(record.get("job_company_name") or "").strip().lower()
    if website:
        row = await _one(pool, MATCH_DOMAIN_SQL, (workspace_id, website))
        if row:
            return UUID(str(row[0]))
    if name:
        row = await _one(pool, MATCH_NAME_SQL, (workspace_id, name))
        if row:
            return UUID(str(row[0]))
    return None


async def _source_record(pool: AsyncConnectionPool, account: Account, returned: int) -> UUID:
    now = datetime.now(UTC)
    digest = hashlib.sha256(
        f"pdl:person:{account.id}:{now.isoformat()}:{uuid4()}".encode()
    ).hexdigest()
    row = await _one(
        pool,
        SOURCE_SQL,
        (
            account.org_id,
            account.workspace_id,
            "provider:pdl_person",
            "enrichment",
            f"account:{account.id}",
            now,
            digest,
            None,
            "en",
            json.dumps({"provider": "pdl_person", "returned": returned}),
        ),
    )
    assert row is not None
    return UUID(str(row[0]))


async def _store_person(
    pool: AsyncConnectionPool,
    account: Account,
    target: UUID,
    record: dict[str, Any],
    personas: dict[str, Any],
    source: UUID,
) -> UUID:
    name = str(record.get("full_name") or "").strip() or "Unknown contact"
    email = str(record.get("work_email") or "").strip().lower() or None
    profile = str(record.get("linkedin_url") or "").strip() or None
    location = {
        "locality": record.get("location_locality"),
        "region": record.get("location_region"),
        "country": record.get("location_country") or record.get("location_name"),
    }
    encrypted = encrypt_secret(email) if email else None
    digest = lookup_hash(email) if email else None
    row = await _one(
        pool,
        UPSERT_SQL,
        (
            account.org_id,
            account.workspace_id,
            target,
            name,
            record.get("job_title"),
            persona_for(str(record.get("job_title") or ""), personas),
            profile,
            encrypted,
            digest,
            mask_email(email) if email else None,
            json.dumps(location),
            source,
        ),
    )
    assert row is not None
    return UUID(str(row[0]))


async def _queue_unlinked(
    pool: AsyncConnectionPool,
    account: Account,
    record: dict[str, Any],
    personas: dict[str, Any],
    source: UUID,
) -> UUID:
    """A person whose company is not an account goes to Review, never onto the People page."""
    name = str(record.get("full_name") or "").strip() or "Unknown contact"
    email = str(record.get("work_email") or "").strip().lower() or None
    profile = str(record.get("linkedin_url") or "").strip() or None
    digest = lookup_hash(email) if email else None
    existing: UUID | None = None
    if profile:
        row = await _one(pool, PROFILE_ALIAS_SQL, (account.org_id, profile))
        if row:
            existing = UUID(str(row[0]))
    if existing is None and digest:
        row = await _one(pool, EMAIL_ALIAS_SQL, (account.org_id, digest))
        if row:
            existing = UUID(str(row[0]))
    if existing is None:
        row = await _one(pool, CREATE_ENTITY_SQL, (account.org_id, name))
        assert row is not None
        existing = UUID(str(row[0]))

    async with pool.connection() as conn:
        if profile:
            await conn.execute(ALIAS_PROFILE_SQL, (account.org_id, existing, profile, source))
        if email:
            await conn.execute(ALIAS_EMAIL_SQL, (account.org_id, existing, digest, source))
            await conn.execute(
                CONTACT_INSERT_SQL,
                (
                    account.org_id,
                    account.workspace_id,
                    existing,
                    encrypt_secret(email),
                    digest,
                    mask_email(email),
                ),
            )
        cur = await conn.execute(QUEUE_UNLINKED_SQL, (existing, account.workspace_id))
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def default_provider(pool: AsyncConnectionPool) -> PersonSearchProvider | None:
    """The keyed PDL person-search adapter, or None when CI has no PDL key."""
    price = await unit_price(pool, "pdl_person")
    providers = registry.configured(
        "person_search", prices={"pdl_person": price}, client=httpx.AsyncClient()
    )
    return providers[0] if providers else None


async def find_people(
    pool: AsyncConnectionPool,
    ctx: CallContext,
    account_id: UUID,
    limit: int,
    *,
    provider: PersonSearchProvider,
    budget: BudgetGuard | None = None,
    brief_id: UUID | None = None,
) -> dict[str, Any]:
    """Find at most *limit* people at one account. Returns counts and ids, never person values."""
    if not 1 <= limit <= 10:
        raise ValueError("find people takes a limit of 1 to 10")
    account = await load_account(pool, account_id, ctx.workspace_id)
    if account is None:
        return {"found": 0, "reason": "account_not_found"}
    if not account.domain:
        return {"found": 0, "reason": "no_domain"}

    if brief_id is None:
        row = await _one(pool, HOME_LENS_SQL, (account.workspace_id, ctx.brief_id))
        brief_id = UUID(str(row[0])) if row and row[0] else ctx.brief_id
    personas = await load_personas(pool, brief_id)

    price = await unit_price(pool, "pdl_person")
    guard = budget or PgBudgetGuard(pool)
    reservation = await guard.reserve(ctx, price * limit)
    started = time.monotonic()
    try:
        result = await provider.lookup(
            ProviderInput(
                domain=account.domain,
                company_name=account.name,
                titles=persona_titles(personas),
                limit=limit,
            )
        )
    except BaseException:
        await guard.release(reservation)
        raise
    latency_ms = int((time.monotonic() - started) * 1000)
    await guard.settle(
        reservation,
        ctx,
        "enrich",
        provider.key,
        provider.key,
        [UsageLine(UsageUnit.ENRICHMENT_LOOKUP, result.billable_units, result.cost_usd)],
        latency_ms,
        "ok" if result.status != "error" else "rejected",
    )
    if result.status != "found" or not isinstance(result.value, list):
        return {"found": 0, "status": result.status, "error": result.error}

    people = [p for p in result.value if isinstance(p, dict)][:limit]
    source = await _source_record(pool, account, len(people))
    linked: list[str] = []
    flagged: list[str] = []
    unlinked: list[str] = []
    for person in people:
        reason = location_mismatch(person, account)
        company = await match_company(pool, account.workspace_id, person)
        target = company or account.id
        person_id = (
            await _queue_unlinked(pool, account, person, personas, source)
            if company is None
            else await _store_person(pool, account, target, person, personas, source)
        )
        if company is None:
            unlinked.append(str(person_id))
            continue
        linked.append(str(person_id))
        if reason:
            flagged.append(str(person_id))
            await _one(pool, FLAG_SQL, (person_id, account.workspace_id, reason))
    log.info(
        "people.find.done",
        account_id=str(account.id),
        linked=len(linked),
        flagged=len(flagged),
        unlinked=len(unlinked),
    )
    return {"found": len(people), "linked": linked, "flagged": flagged, "unlinked": unlinked}
