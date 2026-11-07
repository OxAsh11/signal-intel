from signal_core.db import run_async


async def _answer() -> int:
    return 42


def test_run_async_returns_result() -> None:
    assert run_async(_answer()) == 42
