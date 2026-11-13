"""An Unverified verdict asks for one narrow search for that entity (M2/M5)."""

from typing import Any
from uuid import uuid4

import pytest

import signal_core.modules.m05_verdict as m05
from signal_core.briefs import ActiveBrief


class FakeJob:
    def __init__(self) -> None:
        self.deferred: list[tuple[str, dict[str, Any]]] = []
        self._lock = ""

    def configure(self, *, queueing_lock: str) -> "FakeJob":
        self._lock = queueing_lock
        return self

    async def defer_async(self, **kwargs: Any) -> None:
        self.deferred.append((self._lock, kwargs))


def brief() -> ActiveBrief:
    return ActiveBrief(uuid4(), uuid4(), uuid4(), {}, {})


@pytest.mark.asyncio
async def test_only_unverified_verdicts_request_a_search(monkeypatch: pytest.MonkeyPatch) -> None:
    job = FakeJob()
    monkeypatch.setattr(m05, "targeted_search_job", job)
    a, b, c = brief(), brief(), brief()
    entity, ws = uuid4(), uuid4()

    n = await m05.defer_targeted_searches([a, b, c], ["Unverified", "Confirmed", None], entity, ws)

    assert n == 1
    assert job.deferred == [
        (
            f"ts:{a.brief_id}:{entity}",
            {"entity_id": str(entity), "brief_id": str(a.brief_id), "workspace_id": str(ws)},
        )
    ]


@pytest.mark.asyncio
async def test_no_job_wired_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m05, "targeted_search_job", None)
    assert await m05.defer_targeted_searches([brief()], ["Unverified"], uuid4(), uuid4()) == 0
