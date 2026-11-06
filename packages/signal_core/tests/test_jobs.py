from signal_core.jobs import SKIPPED, guarded


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
