from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel

from signal_core.ai_gateway import AiGateway, MemoryExtractorVersions, load_prompt
from signal_core.ai_gateway.evals import EvalCase, load_cases, main, run_eval
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.metering import CallContext, MemoryBudgetGuard, MemoryPriceBook

SONNET = "claude-sonnet-5-5"


class Canary(BaseModel):
    company: str
    uses_tool: bool


def gateway(provider: FakeProvider) -> AiGateway:
    return AiGateway(
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        chat=provider,
        embedder=None,
        prices=MemoryPriceBook(
            {
                (SONNET, UsageUnit.AI_INPUT_TOKENS): Decimal("2.00"),
                (SONNET, UsageUnit.AI_OUTPUT_TOKENS): Decimal("10.00"),
            }
        ),
        budget=MemoryBudgetGuard(),
        versions=MemoryExtractorVersions(),
    )


def cases() -> list[EvalCase]:
    return [
        EvalCase(
            f"c{i}",
            {"tool": "AutoCAD", "text": f"text-{i}"},
            {"company": f"Co{i}", "uses_tool": True},
        )
        for i in range(5)
    ]


def ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "ai.eval", {})


async def test_eval_counts_pass_and_fail() -> None:
    def respond(user: str) -> str:
        i = user.split("text-")[1][0]
        uses = i != "3"  # case c3 is wrong
        return f'{{"company": "Co{i}", "uses_tool": {str(uses).lower()}}}'

    report = await run_eval(
        gateway(FakeProvider(respond)),
        load_prompt("canary_extract", 1),
        Canary,
        cases(),
        ctx(),
    )
    assert report.passed == 4
    assert report.failed == 1
    assert report.failures[0][0] == "c3"
    assert "uses_tool" in report.failures[0][1]


async def test_invalid_output_counts_as_failed() -> None:
    report = await run_eval(
        gateway(FakeProvider(lambda _u: "not json")),
        load_prompt("canary_extract", 1),
        Canary,
        cases()[:2],
        ctx(),
    )
    assert report.passed == 0
    assert report.failed == 2
    assert report.failures[0] == ("c0", "invalid output")


def test_shipped_cases_load() -> None:
    loaded = load_cases("canary_extract", 1)
    assert len(loaded) == 5
    assert any(c.expected["company"] == "" for c in loaded)
    assert {c.expected["uses_tool"] for c in loaded} == {True, False}


def test_main_fake_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["canary_extract@1", "--fake"]) == 0
    assert "5/5 passed" in capsys.readouterr().out


def test_main_unknown_prompt_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["nope@1", "--fake"]) == 2
    assert capsys.readouterr().err


def test_main_real_requires_workspace() -> None:
    args: list[Any] = ["canary_extract@1"]
    assert main(args) == 2
