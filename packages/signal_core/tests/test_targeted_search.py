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


# ── run_targeted_search (the job body) ────────────────────────────────────────


class _Cur:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self, pool: "FakePool") -> None:
        self.pool = pool

    async def execute(self, sql: str, args: Any = None) -> _Cur:
        self.pool.sql.append(sql)
        if "claim_targeted_search" in sql:
            return _Cur([(self.pool.claim,)])
        if "display_name" in sql:
            return _Cur([("Acme Hotels", str(uuid4()), ["tender"])])
        return _Cur([])

    def cursor(self) -> "_Conn":
        return self

    async def fetchone(self) -> None:
        return None

    async def __aenter__(self) -> "_Conn":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakePool:
    def __init__(self, claim: bool = True) -> None:
        self.claim = claim
        self.sql: list[str] = []

    def connection(self) -> _Conn:
        return _Conn(self)


class FailingConnector:
    async def fetch(self, *_: Any, **__: Any):
        from signal_core.connectors.brave import BraveError

        raise BraveError("429")


@pytest.mark.asyncio
async def test_failed_search_gives_the_slot_back() -> None:
    from signal_core.connectors.brave import BraveError
    from signal_core.modules.m02_targeted import run_targeted_search

    pool = FakePool()
    with pytest.raises(BraveError):
        await run_targeted_search(
            pool,  # type: ignore[arg-type]
            lambda ctx: FailingConnector(),  # type: ignore[arg-type,return-value]
            entity_id=str(uuid4()),
            brief_id=str(uuid4()),
            workspace_id=str(uuid4()),
            approval_id=None,
            configured=True,
        )
    assert any("app.release_targeted_search" in q for q in pool.sql)


@pytest.mark.asyncio
async def test_throttled_and_unconfigured_do_not_search() -> None:
    from signal_core.modules.m02_targeted import run_targeted_search

    args = {"entity_id": str(uuid4()), "brief_id": str(uuid4()), "workspace_id": str(uuid4())}
    pool = FakePool(claim=False)
    r = await run_targeted_search(
        pool,
        lambda ctx: FailingConnector(),
        approval_id=None,
        configured=True,
        **args,  # type: ignore[arg-type,return-value]
    )
    assert r == {"searched": False, "reason": "throttled"}
    pool = FakePool()
    r = await run_targeted_search(
        pool,
        lambda ctx: FailingConnector(),
        approval_id=None,
        configured=False,
        **args,  # type: ignore[arg-type,return-value]
    )
    assert r == {"searched": False, "reason": "not_configured"} and pool.sql == []


@pytest.mark.asyncio
async def test_resumed_search_skips_the_claim() -> None:
    from signal_core.connectors.brave import BraveError
    from signal_core.modules.m02_targeted import run_targeted_search

    pool = FakePool(claim=False)
    with pytest.raises(BraveError):
        await run_targeted_search(
            pool,  # type: ignore[arg-type]
            lambda ctx: FailingConnector(),  # type: ignore[arg-type,return-value]
            entity_id=str(uuid4()),
            brief_id=str(uuid4()),
            workspace_id=str(uuid4()),
            approval_id=str(uuid4()),
            configured=True,
        )
    assert not any("claim_targeted_search" in q for q in pool.sql)
