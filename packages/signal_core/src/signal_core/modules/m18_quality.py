"""M18 Compliance — DSR, evaluation sets, lawful basis, suppression check.

All writes go through ``app.*`` SECURITY DEFINER RPCs (S8 write discipline).

Functions:
  submit_dsr(pool, org_id, person_entity_id, type_, requested_by) -> UUID
  record_lawful_basis(pool, org_id, source_type, basis) -> UUID
  add_evaluation_example(pool, org_id, workspace_id, brief_version_id, example) -> None
  is_suppressed(pool, org_id, person_entity_id) -> bool
"""

from __future__ import annotations

import json
from uuid import UUID

from signal_core.logging import get_logger

log = get_logger(__name__)


async def submit_dsr(
    pool: object,
    org_id: UUID,
    person_entity_id: UUID,
    type_: str,
    requested_by: str,
) -> UUID:
    """Submit a data subject request via ``app.submit_dsr`` RPC.

    If *type_* is ``'deletion'``, the RPC also invokes
    ``app.process_dsr_deletion`` internally.

    Returns the UUID of the newly created DSR row.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.submit_dsr(%s, %s, %s, %s)",
                (str(org_id), str(person_entity_id), type_, requested_by),
            )
            row = await cur.fetchone()

    dsr_id = UUID(str(row[0]))

    log.info(
        "compliance.dsr_submitted",
        org_id=str(org_id),
        person_entity_id=str(person_entity_id),
        type_=type_,
        dsr_id=str(dsr_id),
    )

    return dsr_id


async def record_lawful_basis(
    pool: object,
    org_id: UUID,
    source_type: str,
    basis: str,
) -> UUID:
    """Record a lawful basis entry via ``app.record_lawful_basis`` RPC.

    Returns the UUID of the newly created row.
    """
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.record_lawful_basis(%s, %s, %s)",
                (str(org_id), source_type, basis),
            )
            row = await cur.fetchone()

    record_id = UUID(str(row[0]))

    log.info(
        "compliance.lawful_basis_recorded",
        org_id=str(org_id),
        source_type=source_type,
        basis=basis,
        record_id=str(record_id),
    )

    return record_id


async def add_evaluation_example(
    pool: object,
    org_id: UUID,
    workspace_id: UUID,
    brief_version_id: UUID,
    example: dict,
) -> None:
    """Append an example to the evaluation set via ``app.add_evaluation_example`` RPC."""
    example_json = json.dumps(example)

    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT app.add_evaluation_example(%s, %s, %s, %s::jsonb)",
                (str(org_id), str(workspace_id), str(brief_version_id), example_json),
            )

    log.info(
        "compliance.evaluation_example_added",
        org_id=str(org_id),
        workspace_id=str(workspace_id),
        brief_version_id=str(brief_version_id),
    )


async def is_suppressed(
    pool: object,
    org_id: UUID,
    person_entity_id: UUID,
) -> bool:
    """Return True if the person is on the suppression list for the given org."""
    async with pool.connection() as conn:  # type: ignore[union-attr]
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT EXISTS(SELECT 1 FROM public.suppression_list WHERE org_id=%s AND person_entity_id=%s)",
                (str(org_id), str(person_entity_id)),
            )
            row = await cur.fetchone()

    return bool(row[0]) if row else False
