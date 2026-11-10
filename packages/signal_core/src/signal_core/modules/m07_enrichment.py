"""M7 Enrichment engine — provider waterfall, verification gate, suppression.

Flow (per entity linked to the account):
  1. Fetch account org_id and entity_id.
  2. For the entity:
     a. Never-pay-twice: skip if valid contact_point exists within 30 days.
     b. Suppression: skip if invalid contact_point exists for same type.
     c. Run provider waterfall; reserve budget before each paid provider.
     d. On first valid result, write via app.create_contact_point RPC.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Provider definitions
# ---------------------------------------------------------------------------


class StubProvider:
    name = "stub"
    is_paid = False
    contact_type = "email"

    async def lookup(
        self,
        entity_id: UUID,
        display_name: str,
        company_domain: str,
    ) -> dict | None:
        """Returns dict with type/value_encrypted/status or None.

        Base stub returns nothing; tests override via subclass or monkeypatch.
        """
        return None


PROVIDERS: list[StubProvider] = [StubProvider()]

# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------


async def enrich_account(
    pool: object,  # AsyncConnectionPool
    gateway: AiGateway,
    account_id: UUID,
    workspace_id: UUID,
    ctx: CallContext,
) -> int:
    """Run enrichment waterfall for all person entities linked to account.

    Returns count of new contact_points created.
    """
    # 1. Fetch account org_id and entity_id
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT a.org_id, a.entity_id FROM public.account a
                WHERE a.id = %s AND a.workspace_id = %s
                """,
                (str(account_id), str(workspace_id)),
            )
            account_row = await cur.fetchone()

    if account_row is None:
        log.warning(
            "enrichment.account_not_found",
            account_id=str(account_id),
            workspace_id=str(workspace_id),
        )
        return 0

    org_id = UUID(str(account_row[0]))
    entity_id = UUID(str(account_row[1]))

    # 2. Fetch entity display_name
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT e.display_name FROM public.entity e WHERE e.id = %s",
                (str(entity_id),),
            )
            entity_row = await cur.fetchone()

    display_name: str = (entity_row[0] if entity_row else None) or "unknown"

    # 3. Fetch company domain from entity properties
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT e.properties->>'domain' FROM public.entity e WHERE e.id = %s",
                (str(entity_id),),
            )
            domain_row = await cur.fetchone()

    company_domain: str = (domain_row[0] if domain_row else None) or ""

    created_count = 0

    # 4. Run provider waterfall
    for provider in PROVIDERS:
        contact_type = provider.contact_type

        # Never-pay-twice: skip if valid contact_point exists within 30 days
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id FROM public.contact_point
                    WHERE entity_id = %s AND type = %s AND status = 'valid'
                      AND (verified_at IS NULL OR verified_at > now() - interval '30 days')
                    LIMIT 1
                    """,
                    (str(entity_id), contact_type),
                )
                existing_valid = await cur.fetchone()

        if existing_valid is not None:
            log.info(
                "enrichment.skip_already_valid",
                entity_id=str(entity_id),
                contact_type=contact_type,
            )
            continue

        # Suppression: skip if invalid contact_point exists for same type
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id FROM public.contact_point
                    WHERE entity_id = %s AND type = %s AND status = 'invalid'
                    LIMIT 1
                    """,
                    (str(entity_id), contact_type),
                )
                existing_invalid = await cur.fetchone()

        if existing_invalid is not None:
            log.info(
                "enrichment.skip_suppressed",
                entity_id=str(entity_id),
                contact_type=contact_type,
                provider=provider.name,
            )
            continue

        # Reserve budget before paid provider calls
        reservation_id: UUID | None = None
        if provider.is_paid:
            try:
                estimate_usd = Decimal("0.01")  # default estimate per lookup
                reservation_id = await _reserve_budget(pool, ctx, estimate_usd)
            except Exception as exc:
                log.warning(
                    "enrichment.budget_reserve_failed",
                    provider=provider.name,
                    error=str(exc),
                )
                continue

        # Call provider
        try:
            result = await provider.lookup(entity_id, display_name, company_domain)
        except Exception as exc:
            log.warning(
                "enrichment.provider_lookup_failed",
                provider=provider.name,
                entity_id=str(entity_id),
                error=str(exc),
            )
            if reservation_id is not None:
                await _release_budget(pool, reservation_id)
            continue

        if result is None:
            log.info(
                "enrichment.provider_no_result",
                provider=provider.name,
                entity_id=str(entity_id),
            )
            if reservation_id is not None:
                await _release_budget(pool, reservation_id)
            continue

        # Write via app.create_contact_point RPC (S8: all writes through RPCs)
        # params: (org_id, workspace_id, entity_id, account_id, type, value_encrypted,
        #          status::contact_verification_status, verified_at, provider)
        cp_type = result.get("type", contact_type)
        cp_value = result.get("value_encrypted", "")
        cp_status = result.get("status", "pending")
        cp_verified_at = result.get("verified_at", None)

        try:
            async with pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT app.create_contact_point(%s, %s, %s, %s, %s, %s,
                            %s::public.contact_verification_status, %s, %s)
                        """,
                        (
                            str(org_id),
                            str(workspace_id),
                            str(entity_id),
                            str(account_id),
                            cp_type,
                            cp_value,
                            cp_status,
                            cp_verified_at,
                            provider.name,
                        ),
                    )
            created_count += 1
            log.info(
                "enrichment.contact_point_created",
                entity_id=str(entity_id),
                provider=provider.name,
                contact_type=cp_type,
                status=cp_status,
            )
        except Exception as exc:
            log.warning(
                "enrichment.create_contact_point_failed",
                entity_id=str(entity_id),
                provider=provider.name,
                error=str(exc),
            )
            if reservation_id is not None:
                await _release_budget(pool, reservation_id)
            continue

        # Waterfall: stop at first valid result per provider type
        if cp_status == "valid":
            break

    log.info(
        "enrichment.complete",
        account_id=str(account_id),
        created_count=created_count,
    )
    return created_count


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------


async def _reserve_budget(pool: object, ctx: CallContext, estimate_usd: Decimal) -> UUID:
    """Reserve budget via app.reserve_budget RPC."""
    import json

    approval = ctx.job_args.get("approval_id")
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.reserve_budget(%s, %s, %s, %s, %s::jsonb, %s)",
                (
                    str(ctx.workspace_id),
                    str(ctx.brief_id) if ctx.brief_id else None,
                    str(estimate_usd),
                    ctx.job_type,
                    json.dumps(ctx.job_args, default=str),
                    str(approval) if approval else None,
                ),
            )
            row = await cur.fetchone()
    result = row[0] if row else {}
    if not result.get("ok"):
        raise RuntimeError(f"Budget reservation failed: {result}")
    return UUID(str(result["reservation_id"]))


async def _release_budget(pool: object, reservation_id: UUID) -> None:
    """Release a budget reservation."""
    try:
        async with pool.connection() as conn:  # type: ignore[union-attr]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT app.settle_reservation(%s, %s)",
                    (str(reservation_id), None),
                )
    except Exception as exc:
        log.warning(
            "enrichment.release_budget_failed",
            reservation_id=str(reservation_id),
            error=str(exc),
        )
