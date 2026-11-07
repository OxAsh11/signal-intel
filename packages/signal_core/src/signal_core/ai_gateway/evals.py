"""Eval hook (M18): run a prompt version against stored example cases before promoting it.

Never prints or logs prompt text or completions; a FAIL line shows only the expected and
actual values of the mismatched keys.
"""

import argparse
import json
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from signal_core.ai_gateway.errors import AiNotConfigured, AiOutputInvalid
from signal_core.ai_gateway.gateway import AiGateway, MemoryExtractorVersions
from signal_core.ai_gateway.outputs import OUTPUT_MODELS
from signal_core.ai_gateway.prompts import Prompt, load_prompt
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.config import Settings, get_settings
from signal_core.contracts.enums import UsageUnit
from signal_core.db import run_async
from signal_core.metering import BudgetPaused, CallContext, MemoryBudgetGuard, MemoryPriceBook

# packages/signal_core/evals, next to src/. Evals run from the repo checkout.
EVALS_DIR = Path(__file__).resolve().parents[3] / "evals"
_ZERO = UUID(int=0)


@dataclass(frozen=True)
class EvalCase:
    id: str
    variables: dict[str, str]
    expected: dict[str, Any]


@dataclass
class EvalReport:
    prompt: str
    version: int
    passed: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def load_cases(name: str, version: int) -> list[EvalCase]:
    path = EVALS_DIR / name / f"v{version}.jsonl"
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            cases.append(EvalCase(raw["id"], raw["variables"], raw["expected"]))
    return cases


async def run_eval(
    gateway: AiGateway,
    prompt: Prompt,
    output: type[BaseModel],
    cases: list[EvalCase],
    ctx: CallContext,
) -> EvalReport:
    """Run each case through the gateway. Only keys present in `expected` are compared."""
    report = EvalReport(prompt=prompt.name, version=prompt.version)
    for case in cases:
        try:
            result = await gateway.run(prompt.capability, prompt, case.variables, output, ctx)
        except AiOutputInvalid:
            report.failed += 1
            report.failures.append((case.id, "invalid output"))
            continue
        actual = result.value.model_dump()
        bad = [k for k, v in case.expected.items() if actual.get(k) != v]
        if bad:
            report.failed += 1
            detail = "; ".join(
                f"{k}: expected {case.expected[k]!r}, got {actual.get(k)!r}" for k in bad
            )
            report.failures.append((case.id, detail))
        else:
            report.passed += 1
    return report


def _defaults(output: type[BaseModel]) -> dict[str, Any]:
    return {n: (False if f.annotation is bool else "") for n, f in output.model_fields.items()}


def _fake_gateway(output: type[BaseModel], cases: list[EvalCase]) -> AiGateway:
    def respond(user: str) -> str:
        for case in cases:
            if all(v in user for v in case.variables.values()):
                return json.dumps({**_defaults(output), **case.expected})
        return "{}"

    settings = Settings(_env_file=None)
    model = settings.ai_model_extract
    return AiGateway(
        settings=settings,
        chat=FakeProvider(respond),
        embedder=None,
        prices=MemoryPriceBook(
            {
                (model, UsageUnit.AI_INPUT_TOKENS): Decimal("2.00"),
                (model, UsageUnit.AI_OUTPUT_TOKENS): Decimal("10.00"),
            }
        ),
        budget=MemoryBudgetGuard(),
        versions=MemoryExtractorVersions(),
    )


async def _run_fake(prompt: Prompt, output: type[BaseModel], cases: list[EvalCase]) -> EvalReport:
    ctx = CallContext(_ZERO, _ZERO, None, None, "ai.eval", {})
    return await run_eval(_fake_gateway(output, cases), prompt, output, cases, ctx)


async def _run_real(
    prompt: Prompt, output: type[BaseModel], cases: list[EvalCase], workspace: UUID
) -> EvalReport:
    from signal_core.ai_gateway.factory import build_gateway
    from signal_core.db import make_pool

    settings = get_settings()
    if settings.database_url_session is None:
        raise AiNotConfigured("DATABASE_URL_SESSION is not set")
    if settings.anthropic_api_key is None:
        raise AiNotConfigured("no chat provider key is set")
    pool = make_pool(settings.database_url_session.get_secret_value())
    await pool.open()
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select org_id from public.workspace where id = %s", (workspace,)
            )
            row = await cur.fetchone()
        if row is None:
            raise AiNotConfigured(f"workspace {workspace} not found")
        ctx = CallContext(
            UUID(str(row[0])),
            workspace,
            None,
            None,
            "ai.eval",
            {"prompt": f"{prompt.name}@{prompt.version}"},
        )
        return await run_eval(build_gateway(settings, pool), prompt, output, cases, ctx)
    finally:
        await pool.close()


def _run(make: Callable[[], Coroutine[Any, Any, EvalReport]]) -> EvalReport:
    return run_async(make())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-eval")
    parser.add_argument("target", help="<name>@<version>")
    parser.add_argument("--fake", action="store_true", help="use a deterministic fake provider")
    parser.add_argument("--workspace", help="workspace uuid to charge (required unless --fake)")
    args = parser.parse_args(argv)

    name, _, ver = args.target.partition("@")
    if name not in OUTPUT_MODELS:
        print(f"unknown prompt {name!r}: no output model registered", file=sys.stderr)
        return 2
    try:
        version = int(ver)
        prompt = load_prompt(name, version)
        cases = load_cases(name, version)
    except (ValueError, FileNotFoundError) as exc:
        print(f"cannot load {args.target}: {type(exc).__name__}", file=sys.stderr)
        return 2
    output = OUTPUT_MODELS[name]

    try:
        if args.fake:
            report = _run(lambda: _run_fake(prompt, output, cases))
        else:
            if not args.workspace:
                print("--workspace is required unless --fake is given", file=sys.stderr)
                return 2
            try:
                workspace = UUID(args.workspace)
            except ValueError:
                print("--workspace must be a uuid", file=sys.stderr)
                return 2
            report = _run(lambda: _run_real(prompt, output, cases, workspace))
    except AiNotConfigured as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2
    except BudgetPaused as exc:
        print(f"budget paused: approval {exc.approval_id}", file=sys.stderr)
        return 3

    for case_id, reason in report.failures:
        print(f"FAIL {case_id}: {reason}")
    print(f"{report.passed}/{report.passed + report.failed} passed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
