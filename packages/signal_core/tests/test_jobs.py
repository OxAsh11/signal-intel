from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from signal_core.ai_gateway.errors import AiNotConfigured
from signal_core.jobs import SKIPPED, guarded
from signal_core.metering import BudgetPaused


class FakeKillSwitch:
    def __init__(self, enabled: dict[str, bool]) -> None:
        self.enabled = enabled

    async def is_enabled(self, job_type: str) -> bool:
        return self.enabled.get(job_type, False)


async def test_job_kill_switch_skips_disabled_type() -> None:
    calls: list[int] = []

    async def body(n: int) -> int:
        calls.append(n)
        return n * 2

    runner = guarded("collect.radar", FakeKillSwitch({"collect.radar": False}), body)
    assert await runner(n=3) == SKIPPED
    assert calls == []


async def test_job_runs_when_enabled() -> None:
    async def body(n: int) -> int:
        return n * 2

    runner = guarded("collect.radar", FakeKillSwitch({"collect.radar": True}), body)
    assert await runner(n=3) == 6


async def test_unknown_job_type_is_disabled() -> None:
    async def body() -> str:
        return "ran"

    assert await guarded("never.registered", FakeKillSwitch({}), body)() == SKIPPED


async def test_budget_pause_ends_job_without_retry() -> None:
    approval = uuid4()

    async def body() -> None:
        raise BudgetPaused(approval, "day", Decimal("1"), Decimal("2"))

    with capture_logs() as logs:
        assert await guarded("extract.run", FakeKillSwitch({"extract.run": True}), body)() is None
    paused = [e for e in logs if e["event"] == "job.paused"]
    assert paused and paused[0]["job_type"] == "extract.run"
    assert paused[0]["approval_id"] == str(approval)


async def test_missing_key_ends_job_without_retry() -> None:
    async def body() -> None:
        raise AiNotConfigured("no key")

    with capture_logs() as logs:
        assert await guarded("extract.run", FakeKillSwitch({"extract.run": True}), body)() is None
    assert any(e["event"] == "job.not_configured" for e in logs)


async def test_other_exceptions_still_propagate() -> None:
    async def body() -> Any:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await guarded("extract.run", FakeKillSwitch({"extract.run": True}), body)()
