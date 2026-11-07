"""M17 health: record checks and run the end-to-end canary (SQL function app.run_canary)."""

import json
from typing import Any, Literal, Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

HealthStatus = Literal["ok", "degraded", "incident", "silent"]


class HealthStore(Protocol):
    async def record(
        self, component: str, status: HealthStatus, detail: dict[str, Any], owner: str = "M17"
    ) -> None: ...

    async def run_canary(self) -> dict[str, Any]: ...


class PgHealthStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def record(
        self, component: str, status: HealthStatus, detail: dict[str, Any], owner: str = "M17"
    ) -> None:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "select app.record_health(%s, %s, %s, %s)",
                (component, status, Jsonb(detail), owner),
            )

    async def run_canary(self) -> dict[str, Any]:
        # app.run_canary() sets app.writer = 'derivation' for its own transaction and never raises:
        # a failure is recorded as a health incident and returned with status 'incident'.
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute("select app.run_canary()")
            row = await cur.fetchone()
        result = row[0] if row else {"status": "incident", "error": "no result"}
        return result if isinstance(result, dict) else json.loads(result)
