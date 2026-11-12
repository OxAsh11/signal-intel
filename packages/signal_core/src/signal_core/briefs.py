"""Which Brief versions derived values are computed under.

Verdicts, scores and labels are per Brief version (F5, F7). A workspace can run several Briefs,
so derivation covers the current version of every Brief that is piloting or active.
"""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool


@dataclass(frozen=True)
class ActiveBrief:
    brief_id: UUID
    brief_version_id: UUID
    org_id: UUID
    config: dict[str, Any]
    weights: dict[str, Any]
    state: str = "Active"

    @property
    def hypothesis(self) -> str:
        return str(self.config.get("hypothesis") or "")


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


async def active_brief_versions(pool: AsyncConnectionPool, workspace_id: UUID) -> list[ActiveBrief]:
    """Current versions of the workspace's Pilot and Active Briefs, most recently published first."""  # noqa: E501
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            SELECT b.id, bv.id, bv.org_id, bv.config, bv.weights, b.state
              FROM public.brief b
              JOIN public.brief_version bv ON bv.id = b.current_version_id
             WHERE b.workspace_id = %s
               AND b.state IN ('Pilot', 'Active')
             ORDER BY bv.published_at DESC
            """,
            (str(workspace_id),),
        )
        rows = await cur.fetchall()
    return [
        ActiveBrief(
            brief_id=UUID(str(r[0])),
            brief_version_id=UUID(str(r[1])),
            org_id=UUID(str(r[2])),
            config=_as_dict(r[3]),
            weights=_as_dict(r[4]),
            state=str(r[5]),
        )
        for r in rows
    ]


async def holds_pilot_slot(
    pool: AsyncConnectionPool, version_id: UUID, entity_id: UUID, *, claim: bool
) -> bool:
    """Whether the entity is in the pilot sample of this Brief version (M1).

    claim=True takes a free slot when there is one (verdicts); claim=False only checks (scores).
    """
    async with pool.connection() as conn, conn.cursor() as cur:
        if claim:
            await cur.execute(
                "SELECT app.claim_pilot_slot(%s, %s)", (str(version_id), str(entity_id))
            )
            row = await cur.fetchone()
            return bool(row and row[0])
        await cur.execute(
            "SELECT 1 FROM public.pilot_sample WHERE brief_version_id = %s AND entity_id = %s",
            (str(version_id), str(entity_id)),
        )
        return await cur.fetchone() is not None


_REDERIVE_BRIEF_SQL = """
    SELECT b.state, s.pilot_sample_size,
           (SELECT count(*) FROM public.pilot_sample ps WHERE ps.brief_version_id = bv.id)
      FROM public.brief_version bv
      JOIN public.brief b ON b.id = bv.brief_id
      JOIN public.workspace_settings s ON s.workspace_id = bv.workspace_id
     WHERE bv.id = %s
"""

# The workspace's accounts; ones a pilot of this Brief already sampled come first.
_REDERIVE_ACCOUNTS_SQL = """
    SELECT a.id, a.entity_id, a.workspace_id, a.org_id,
           EXISTS (SELECT 1 FROM public.verdict v
                    WHERE v.entity_id = a.entity_id AND v.brief_version_id = bv.id)
      FROM public.brief_version bv
      JOIN public.account a ON a.workspace_id = bv.workspace_id
     WHERE bv.id = %s
     ORDER BY EXISTS (SELECT 1 FROM public.pilot_sample ps
                        JOIN public.brief_version pv ON pv.id = ps.brief_version_id
                       WHERE ps.entity_id = a.entity_id AND pv.brief_id = bv.brief_id) DESC,
              a.created_at, a.id
"""


async def rederive_plan(
    pool: AsyncConnectionPool, version_id: UUID
) -> list[tuple[str, dict[str, str]]]:
    """Jobs that bring a newly published Brief version up to date (M1).

    Accounts whose verdict was carried over are only re-scored; the rest are judged. A Pilot
    version judges no more accounts than its sample has free slots.
    """
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(_REDERIVE_BRIEF_SQL, (str(version_id),))
        brief_row = await cur.fetchone()
        if brief_row is None:
            return []
        await cur.execute(_REDERIVE_ACCOUNTS_SQL, (str(version_id),))
        accounts = await cur.fetchall()
    state, sample_size, taken = brief_row
    free = int(sample_size or 0) - int(taken or 0) if state == "Pilot" else None

    plan: list[tuple[str, dict[str, str]]] = []
    for account_id, entity_id, workspace_id, org_id, has_verdict in accounts:
        if has_verdict:
            kwargs = {"account_id": str(account_id), "workspace_id": str(workspace_id)}
            plan.append(("score.compute", {**kwargs, "org_id": str(org_id)}))
        elif free is None or free > 0:
            kwargs = {"entity_id": str(entity_id), "workspace_id": str(workspace_id)}
            plan.append(("verdict.compute", {**kwargs, "org_id": str(org_id)}))
            if free is not None:
                free -= 1
    return plan
