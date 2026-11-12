"""M7 Enrichment — find the right people at an account and verified ways to reach them.

Flow for one account:
  1. Load the account, its company name and domain, and the Brief's personas.
  2. Never pay twice: skip when the account already has a valid contact verified in the last
     30 days, or was looked up in that window.
  3. Provider waterfall: providers are tried in order and the waterfall stops at the first one
     that returns a valid contact. A paid lookup reserves budget first (S6); the reservation is
     settled with the real cost on success and released when nothing was bought.
  4. Each contact becomes a tenant-scoped person with a works_at link to the company. The value
     is stored encrypted, with a keyed digest for de-duplication; suppressed people are skipped.

Providers are configured by environment. With none configured the job does nothing: Settings
shows "Not set up yet".
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import httpx
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.briefs import active_brief_versions
from signal_core.config import get_settings
from signal_core.crypto import SecretsNotConfigured, encrypt_secret, lookup_hash
from signal_core.logging import get_logger
from signal_core.metering import BudgetPaused, CallContext

log = get_logger(__name__)

RECENT_DAYS = 30
MAX_CONTACTS_PER_ACCOUNT = 10


@dataclass(frozen=True)
class FoundContact:
    name: str
    title: str | None
    type: str  # email | phone | profile
    value: str
    status: str  # valid | risky | invalid | unknown
    verified_at: datetime | None = None


class EnrichmentProvider(Protocol):
    name: str
    is_paid: bool
    cost_usd: Decimal

    async def lookup(self, company_name: str, company_domain: str) -> list[FoundContact]: ...


class StubProvider:
    """Returns nothing. Used in tests (subclassed) and never configured in production."""

    name = "stub"
    is_paid = False
    cost_usd = Decimal(0)

    async def lookup(self, company_name: str, company_domain: str) -> list[FoundContact]:
        return []


class HunterProvider:
    """Hunter domain search: people at a company domain with a verification status."""

    name = "hunter"
    is_paid = True
    cost_usd = Decimal("0.05")
    _URL = "https://api.hunter.io/v2/domain-search"
    _STATUS = {"valid": "valid", "accept_all": "risky", "invalid": "invalid"}  # noqa: RUF012

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def lookup(self, company_name: str, company_domain: str) -> list[FoundContact]:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                self._URL,
                params={"domain": company_domain, "type": "personal", "limit": 25},
                headers={"X-API-KEY": self._api_key},
            )
        response.raise_for_status()
        emails = (response.json().get("data") or {}).get("emails") or []
        found: list[FoundContact] = []
        for item in emails:
            value = item.get("value")
            if not value:
                continue
            verification = item.get("verification") or {}
            status = self._STATUS.get(str(verification.get("status")), "unknown")
            verified_at: datetime | None = None
            if verification.get("date"):
                try:
                    verified_at = datetime.fromisoformat(str(verification["date"])).replace(
                        tzinfo=UTC
                    )
                except ValueError:
                    verified_at = None
            if status == "valid" and verified_at is None:
                verified_at = datetime.now(UTC)
            name = " ".join(p for p in (item.get("first_name"), item.get("last_name")) if p)
            found.append(
                FoundContact(
                    name=name or value.split("@")[0],
                    title=item.get("position"),
                    type="email",
                    value=str(value),
                    status=status,
                    verified_at=verified_at,
                )
            )
        return found


def configured_providers() -> list[EnrichmentProvider]:
    settings = get_settings()
    providers: list[EnrichmentProvider] = []
    if settings.hunter_api_key is not None:
        providers.append(HunterProvider(settings.hunter_api_key.get_secret_value()))
    return providers


def match_persona(title: str | None, personas: dict[str, list[str]]) -> str | None:
    """The persona role (buyer, user, influencer) whose titles the contact's title matches."""
    if not title:
        return None
    lowered = title.lower()
    for role, titles in personas.items():
        if any(t and t.lower() in lowered for t in titles):
            return role
    return None


async def enrich_account(
    pool: AsyncConnectionPool,
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
    *,
    providers: list[EnrichmentProvider] | None = None,
) -> int:
    """Run the waterfall for one account. Returns the number of contacts stored or refreshed."""
    providers = configured_providers() if providers is None else providers
    if not providers:
        log.info("enrichment.not_configured", account_id=str(account_id))
        return 0

    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT a.org_id, e.display_name,
                   (SELECT ea.value_normalised FROM public.entity_alias ea
                     WHERE ea.entity_id = a.entity_id AND ea.alias_type = 'domain'
                     ORDER BY ea.confidence DESC LIMIT 1),
                   EXISTS (SELECT 1 FROM public.contact_point cp
                            WHERE cp.account_id = a.id AND cp.status = 'valid'
                              AND cp.verified_at > now() - make_interval(days => %s)),
                   EXISTS (SELECT 1 FROM public.source_record sr
                            WHERE sr.org_id = a.org_id AND sr.source_type = 'enrichment'
                              AND sr.url = 'account:' || a.id::text
                              AND sr.fetched_at > now() - make_interval(days => %s))
              FROM public.account a
              JOIN public.entity e ON e.id = a.entity_id
             WHERE a.id = %s AND a.workspace_id = %s
            """,
            (RECENT_DAYS, RECENT_DAYS, str(account_id), str(workspace_id)),
        )
        row = await cur.fetchone()

    if row is None:
        log.warning("enrichment.account_not_found", account_id=str(account_id))
        return 0
    org_id, company_name, domain, has_recent_valid, looked_up_recently = row
    if not domain:
        log.info("enrichment.no_domain", account_id=str(account_id))
        return 0
    if has_recent_valid or looked_up_recently:
        log.info("enrichment.skip_recent", account_id=str(account_id))
        return 0

    briefs = await active_brief_versions(pool, workspace_id)
    personas_raw = briefs[0].config.get("personas") if briefs else None
    personas: dict[str, list[str]] = personas_raw if isinstance(personas_raw, dict) else {}
    wants_personas = any(personas.values())

    stored = 0
    for provider in providers:
        reservation_id = await _reserve(pool, ctx, provider) if provider.is_paid else None
        started = time.monotonic()
        try:
            contacts = await provider.lookup(str(company_name or ""), str(domain))
        except Exception as exc:
            log.warning("enrichment.lookup_failed", provider=provider.name, error=str(exc))
            await _settle(pool, reservation_id, None)
            continue

        latency_ms = int((time.monotonic() - started) * 1000)
        await _settle(
            pool,
            reservation_id,
            {
                "capability": "enrich",
                "provider": provider.name,
                "model": None,
                "latency_ms": latency_ms,
                "status": "ok",
                "job_id": ctx.job_id,
                "lines": [
                    {"unit": "enrichment_lookup", "quantity": 1, "cost_usd": str(provider.cost_usd)}
                ],
            },
        )

        # Provenance for whatever the provider returned (no personal data in the record).
        source_record_id = await _record_lookup(
            pool,
            UUID(str(org_id)),
            workspace_id,
            account_id,
            provider.name,
            str(domain),
            len(contacts),
        )

        any_valid = False
        for contact in contacts[: MAX_CONTACTS_PER_ACCOUNT * 3]:
            persona = match_persona(contact.title, personas)
            if wants_personas and persona is None:
                continue
            if stored >= MAX_CONTACTS_PER_ACCOUNT:
                break
            try:
                encrypted, digest = encrypt_secret(contact.value), lookup_hash(contact.value)
            except SecretsNotConfigured:
                log.error("enrichment.secrets_not_configured")
                return stored
            async with pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT app.upsert_person_contact(%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                    " %s::public.contact_verification_status, %s, %s, %s)",
                    (
                        str(org_id),
                        str(workspace_id),
                        str(account_id),
                        contact.name,
                        contact.title,
                        persona,
                        contact.type,
                        encrypted,
                        digest,
                        contact.status,
                        contact.verified_at,
                        provider.name,
                        str(source_record_id),
                    ),
                )
                cp_row = await cur.fetchone()
            if cp_row is not None and cp_row[0] is not None:
                stored += 1
                any_valid = any_valid or contact.status == "valid"

        log.info(
            "enrichment.provider_done",
            account_id=str(account_id),
            provider=provider.name,
            returned=len(contacts),
            stored=stored,
        )
        if any_valid:
            break

    return stored


async def _record_lookup(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    account_id: UUID,
    provider: str,
    domain: str,
    returned: int,
) -> UUID:
    """One source record per lookup: the receipt every contact found in it points to."""
    now = datetime.now(UTC)
    digest = hashlib.sha256(
        f"enrichment:{provider}:{account_id}:{now.isoformat()}".encode()
    ).hexdigest()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.create_source_record(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                str(org_id),
                str(workspace_id),
                f"enrichment:{provider}",
                "enrichment",
                f"account:{account_id}",
                now,
                digest,
                None,
                None,
                json.dumps({"provider": provider, "domain": domain, "contacts_returned": returned}),
            ),
        )
        row = await cur.fetchone()
    assert row is not None
    return UUID(str(row[0]))


async def _reserve(
    pool: AsyncConnectionPool, ctx: CallContext, provider: EnrichmentProvider
) -> UUID:
    """Reserve the lookup's cost. Over a cap, the job pauses for approval (BudgetPaused)."""
    approval = ctx.job_args.get("approval_id")
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT app.reserve_budget(%s, %s, %s::numeric, %s, %s::jsonb, %s)",
            (
                str(ctx.workspace_id),
                str(ctx.brief_id) if ctx.brief_id else None,
                str(provider.cost_usd),
                ctx.job_type,
                json.dumps(ctx.job_args, default=str),
                str(approval) if approval else None,
            ),
        )
        row = await cur.fetchone()
    result: dict[str, Any] = row[0] if row else {}
    if not result.get("ok"):
        raise BudgetPaused(
            UUID(str(result["approval_id"])),
            str(result.get("period")),
            Decimal(str(result.get("cap", 0))),
            Decimal(str(result.get("spent", 0))),
        )
    return UUID(str(result["reservation_id"]))


async def _settle(
    pool: AsyncConnectionPool, reservation_id: UUID | None, actual: dict[str, Any] | None
) -> None:
    """Settle a reservation with the actual usage, or release it when `actual` is None."""
    if reservation_id is None:
        return
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "SELECT app.settle_reservation(%s, %s::jsonb)",
                (str(reservation_id), json.dumps(actual) if actual is not None else None),
            )
    except Exception as exc:
        log.warning("enrichment.settle_failed", reservation_id=str(reservation_id), error=str(exc))
