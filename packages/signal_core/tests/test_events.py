from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import procrastinate
from procrastinate.testing import InMemoryConnector

from signal_core.contracts.enums import EventType
from signal_core.events import (
    DISPATCH_TASK,
    OutboxBatch,
    OutboxEvent,
    procrastinate_deferrer,
    relay_once,
)


class FakeBatch:
    def __init__(self, events: list[OutboxEvent], store: "FakeStore") -> None:
        self.events = events
        self._store = store

    async def mark_published(self, ids: list[UUID]) -> None:
        self._store.published.extend(ids)

    async def mark_failed(self, event_id: UUID, error: str) -> None:
        self._store.failed.append(event_id)


class FakeStore:
    """Returns the same pending events every time: simulates a crash before commit."""

    def __init__(self, events: list[OutboxEvent]) -> None:
        self.events = events
        self.published: list[UUID] = []
        self.failed: list[UUID] = []

    @asynccontextmanager
    async def claim(self, limit: int) -> AsyncIterator[OutboxBatch]:
        yield FakeBatch(self.events[:limit], self)


def make_event() -> OutboxEvent:
    return OutboxEvent(
        id=uuid4(),
        event=EventType.CLAIM_CREATED,
        org_id=uuid4(),
        workspace_id=None,
        aggregate_type="claim",
        aggregate_id=uuid4(),
        payload={"predicate_key": "uses_tool"},
    )


def make_app() -> tuple[procrastinate.App, InMemoryConnector, Any]:
    connector = InMemoryConnector()
    app = procrastinate.App(connector=connector)

    @app.task(name=DISPATCH_TASK)
    async def dispatch(**kwargs: Any) -> None:
        return None

    return app, connector, dispatch


async def test_relay_idempotent() -> None:
    app, connector, dispatch = make_app()
    event = make_event()
    store = FakeStore([event])
    async with app.open_async():
        defer = procrastinate_deferrer(dispatch)
        assert await relay_once(store, defer) == 1
        # Replay of the same row (e.g. crash after defer, before commit) must not enqueue twice.
        assert await relay_once(store, defer) == 1
    jobs = list(connector.jobs.values())
    assert len(jobs) == 1
    assert jobs[0]["queueing_lock"] == str(event.id)
    assert jobs[0]["args"]["event"] == "claim.created"


async def test_relay_marks_failures_and_continues() -> None:
    good, bad = make_event(), make_event()
    store = FakeStore([bad, good])

    async def defer(ev: OutboxEvent) -> None:
        if ev.id == bad.id:
            raise RuntimeError("queue unavailable")

    assert await relay_once(store, defer) == 1
    assert store.failed == [bad.id]
    assert store.published == [good.id]
