"""S4 outbox relay: moves committed outbox rows onto the job queue.

Rows are claimed with FOR UPDATE SKIP LOCKED and marked published in the SAME transaction,
so concurrent relays never take the same row. Delivery is at-least-once (a crash between
defer and commit re-relays): each event is deferred with queueing_lock = event id, which drops
a replay while the first copy is still queued, and consumers stay idempotent by event id.
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from procrastinate.exceptions import AlreadyEnqueued
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from signal_core.contracts.enums import EventType
from signal_core.logging import get_logger

log = get_logger(__name__)

DISPATCH_TASK = "outbox.dispatch"


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: UUID
    event: EventType
    org_id: UUID
    workspace_id: UUID | None
    aggregate_type: str
    aggregate_id: UUID
    payload: dict[str, Any]


class OutboxBatch(Protocol):
    events: list[OutboxEvent]

    async def mark_published(self, ids: list[UUID]) -> None: ...
    async def mark_failed(self, event_id: UUID, error: str) -> None: ...


class OutboxStore(Protocol):
    def claim(self, limit: int) -> AbstractAsyncContextManager[OutboxBatch]: ...


class Deferrer(Protocol):
    async def __call__(self, event: OutboxEvent) -> None: ...


def procrastinate_deferrer(task: Any) -> Deferrer:
    """Defers `outbox.dispatch` for an event, keyed by its id."""

    async def defer(event: OutboxEvent) -> None:
        await task.configure(queueing_lock=str(event.id)).defer_async(
            event_id=str(event.id),
            event=event.event.value,
            org_id=str(event.org_id),
            workspace_id=str(event.workspace_id) if event.workspace_id else None,
            aggregate_type=event.aggregate_type,
            aggregate_id=str(event.aggregate_id),
            payload=event.payload,
        )

    return defer


async def relay_once(store: OutboxStore, defer: Deferrer, *, batch_size: int = 100) -> int:
    """Relay one batch. Returns the number of events marked published."""
    async with store.claim(batch_size) as batch:
        published: list[UUID] = []
        for ev in batch.events:
            try:
                await defer(ev)
            except AlreadyEnqueued:
                log.info(
                    "outbox.duplicate_suppressed", event_id=str(ev.id), event_type=ev.event.value
                )
            except Exception as exc:
                log.error("outbox.defer_failed", event_id=str(ev.id), error=str(exc))
                await batch.mark_failed(ev.id, str(exc))
                continue
            published.append(ev.id)
        if published:
            await batch.mark_published(published)
        return len(published)


class _PgBatch:
    def __init__(self, conn: AsyncConnection, events: list[OutboxEvent]) -> None:
        self._conn = conn
        self.events = events

    async def mark_published(self, ids: list[UUID]) -> None:
        await self._conn.execute(
            "update public.outbox set published_at = now() where id = any(%s)", (ids,)
        )

    async def mark_failed(self, event_id: UUID, error: str) -> None:
        await self._conn.execute(
            "update public.outbox set attempts = attempts + 1, last_error = %s where id = %s",
            (error[:2000], event_id),
        )


class PgOutboxStore:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def claim(self, limit: int) -> AsyncIterator[OutboxBatch]:
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """select id, event, org_id, workspace_id, aggregate_type, aggregate_id, payload
                   from public.outbox where published_at is null
                   order by created_at limit %s for update skip locked""",
                (limit,),
            )
            rows = await cur.fetchall()
            events = [
                OutboxEvent(r[0], EventType(r[1]), r[2], r[3], r[4], r[5], r[6] or {}) for r in rows
            ]
            yield _PgBatch(conn, events)
