import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx2
import pytest
from pydantic import BaseModel, SecretStr

from signal_core.ai_gateway import (
    AiGateway,
    AiNotConfigured,
    AiOutputInvalid,
    AiProviderError,
    BudgetPaused,
    Capability,
    MemoryExtractorVersions,
    load_prompt,
    render,
)
from signal_core.ai_gateway.factory import build_gateway
from signal_core.ai_gateway.providers.anthropic import (
    AnthropicProvider,
    AsyncAnthropic,
    strict_schema,
)
from signal_core.ai_gateway.providers.fake import FakeProvider
from signal_core.ai_gateway.registry import Predicate, validate_claim_value
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.metering import CallContext, MemoryBudgetGuard, MemoryPriceBook

SONNET = "claude-sonnet-5-5"


class Canary(BaseModel):
    company: str
    uses_tool: bool


GOOD = '{"company": "Acme", "uses_tool": true}'


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def prices() -> MemoryPriceBook:
    return MemoryPriceBook(
        {
            (SONNET, UsageUnit.AI_INPUT_TOKENS): Decimal("2.00"),
            (SONNET, UsageUnit.AI_OUTPUT_TOKENS): Decimal("10.00"),
            ("claude-haiku-5-5", UsageUnit.AI_INPUT_TOKENS): Decimal("0.10"),
            ("claude-haiku-5-5", UsageUnit.AI_OUTPUT_TOKENS): Decimal("0.50"),
            ("voyage-4", UsageUnit.EMBED_TOKENS): Decimal("0.06"),
        }
    )


def ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, "j1", "canary", {})


def make(
    chat: Any = None, embedder: Any = None, budget: MemoryBudgetGuard | None = None, **kw: Any
) -> tuple[AiGateway, MemoryBudgetGuard, MemoryExtractorVersions]:
    budget = budget or MemoryBudgetGuard()
    versions = MemoryExtractorVersions()
    gw = AiGateway(
        settings=settings(**kw),
        chat=chat,
        embedder=embedder,
        prices=prices(),
        budget=budget,
        versions=versions,
    )
    return gw, budget, versions


def vars_() -> dict[str, str]:
    return {"tool": "Segment", "text": "Acme uses Segment."}


async def test_run_returns_typed_value_and_ledgers_ok() -> None:
    fake = FakeProvider({"*": GOOD}, tokens=(1000, 200))
    gw, budget, versions = make(chat=fake)
    prompt = load_prompt("canary_extract", 1)
    result = await gw.run(Capability.EXTRACT, prompt, vars_(), Canary, ctx())
    assert result.value == Canary(company="Acme", uses_tool=True)
    # 1000 in at 2.00/M = 0.002; 200 out at 10.00/M = 0.002
    assert [(ln.unit, ln.quantity, ln.cost_usd) for ln in budget.lines] == [
        (UsageUnit.AI_INPUT_TOKENS, 1000, Decimal("0.002000")),
        (UsageUnit.AI_OUTPUT_TOKENS, 200, Decimal("0.002000")),
    ]
    assert result.cost_usd == Decimal("0.004000")
    assert budget.settles[0].status == "ok"
    assert not budget.reservations
    assert result.extractor_version_id in versions.hashes
    assert versions.hashes[result.extractor_version_id] == prompt.sha256
    assert fake.calls[0]["model"] == SONNET
    assert fake.calls[0]["json_schema"]["properties"]["company"]


async def test_fenced_json_accepted() -> None:
    gw, _, _ = make(chat=FakeProvider({"*": f"```json\n{GOOD}\n```"}))
    r = await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert r.value.company == "Acme"


async def test_schema_invalid_output_rejected_and_ledgered() -> None:
    gw, budget, _ = make(chat=FakeProvider({"*": '{"company": "Acme"}'}))
    with pytest.raises(AiOutputInvalid) as exc:
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert exc.value.errors
    assert [s.status for s in budget.settles] == ["rejected"]
    assert len(budget.lines) == 2
    assert not budget.reservations


async def test_max_tokens_stop_is_invalid_and_ledgered() -> None:
    fake = FakeProvider({"*": '{"company": "Ac'}, stop_reason="max_tokens")
    gw, budget, _ = make(chat=fake)
    with pytest.raises(AiOutputInvalid):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert [s.status for s in budget.settles] == ["rejected"]


async def test_refusal_settles_rejected_and_raises_provider_error() -> None:
    fake = FakeProvider({"*": ""}, stop_reason="refusal")
    gw, budget, _ = make(chat=fake)
    with pytest.raises(AiProviderError, match="refused"):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert [s.status for s in budget.settles] == ["rejected"]


async def test_prejudge_uses_haiku_and_low_effort() -> None:
    fake = FakeProvider({"*": GOOD})
    gw, _, _ = make(chat=fake)
    await gw.run(Capability.PREJUDGE, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert fake.calls[0]["effort"] == "low"


async def test_budget_paused_before_provider_called() -> None:
    fake = FakeProvider({"*": GOOD})
    budget = MemoryBudgetGuard()
    c = ctx()
    budget.set_cap(c.workspace_id, None, "day", Decimal("0.000001"))
    gw, _, _ = make(chat=fake, budget=budget)
    with pytest.raises(BudgetPaused):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, c)
    assert fake.calls == []
    assert budget.settles == []


async def test_provider_429_retried_then_fails_without_ledger_row() -> None:
    attempts = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        return httpx2.Response(
            429,
            headers={"retry-after-ms": "1"},
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
        )

    client = AsyncAnthropic(
        api_key="x",
        max_retries=2,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    provider = AnthropicProvider(SecretStr("x"), client=client)
    gw, budget, _ = make(chat=provider)
    started = time.monotonic()
    with pytest.raises(AiProviderError):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert time.monotonic() - started < 2
    assert attempts == 3
    assert budget.settles == []
    assert not budget.reservations  # released


async def test_anthropic_request_uses_structured_output_config() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": SONNET,
                "content": [{"type": "text", "text": GOOD}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 7},
            },
        )

    client = AsyncAnthropic(
        api_key="x", http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    )
    gw, budget, _ = make(chat=AnthropicProvider(SecretStr("x"), client=client))
    r = await gw.run(Capability.PREJUDGE, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert r.value.uses_tool is True
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert seen["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert seen["output_config"]["effort"] == "low"
    for banned in ("thinking", "temperature", "top_p", "top_k"):
        assert banned not in seen
    assert budget.lines[0].quantity == 12


def test_strict_schema_closes_nested_objects() -> None:
    class Inner(BaseModel):
        a: int

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    schema = strict_schema(Outer.model_json_schema())
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Inner"]["additionalProperties"] is False


async def test_unknown_model_refused() -> None:
    fake = FakeProvider({"*": GOOD})
    gw, budget, _ = make(chat=fake, ai_model_extract="mystery-model")
    with pytest.raises(AiNotConfigured):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert fake.calls == []
    assert not budget.reservations


async def test_no_key_raises_not_configured() -> None:
    gw, _, _ = make(chat=None, embedder=None)
    assert gw.configured() == {"chat": False, "embed": False}
    with pytest.raises(AiNotConfigured):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    with pytest.raises(AiNotConfigured):
        await gw.embed(["x"], ctx(), input_type="query")


async def test_embed_is_1024_dims() -> None:
    fake = FakeProvider()
    gw, budget, _ = make(embedder=fake)
    assert gw.configured() == {"chat": False, "embed": True}
    vectors = await gw.embed(["hello world", "second"], ctx(), input_type="document")
    assert len(vectors) == 2
    assert all(len(v) == 1024 for v in vectors)
    again = await gw.embed(["hello world"], ctx(), input_type="document")
    assert again[0] == vectors[0]
    assert budget.settles[0].lines[0].unit == UsageUnit.EMBED_TOKENS
    assert budget.settles[0].model == "voyage-4"


def test_prompt_hash_stable_and_missing_variable_raises() -> None:
    a = load_prompt("canary_extract", 1)
    b = load_prompt("canary_extract", 1)
    assert a.sha256 == b.sha256 and len(a.sha256) == 64
    assert a.capability is Capability.EXTRACT
    assert "{text}" in a.user_template
    assert "Acme" in render(a, vars_())
    with pytest.raises(KeyError):
        render(a, {"text": "only text"})


def test_validate_claim_value() -> None:
    def p(t: str, allowed: list[str] | None = None) -> Predicate:
        return Predicate("k", t, allowed, None)

    assert validate_claim_value(p("boolean"), True) is None
    assert validate_claim_value(p("boolean"), "true") is not None
    assert validate_claim_value(p("number"), 3.5) is None
    assert validate_claim_value(p("number"), True) is not None
    assert validate_claim_value(p("text"), "hi") is None
    assert validate_claim_value(p("text"), 3) is not None
    assert validate_claim_value(p("enum", ["a", "b"]), "a") is None
    assert validate_claim_value(p("enum", ["a", "b"]), "c") is not None
    assert validate_claim_value(p("date"), "2026-11-07") is None
    assert validate_claim_value(p("date"), "yesterday") is not None
    assert validate_claim_value(p("entity_ref"), str(UUID(int=1))) is None
    assert validate_claim_value(p("entity_ref"), "nope") is not None
    assert validate_claim_value(p("weird"), "x") is not None


def test_factory_without_keys_is_not_configured() -> None:
    pool: Any = object()
    gw = build_gateway(settings(), pool)
    assert gw.configured() == {"chat": False, "embed": False}
    gw2 = build_gateway(settings(anthropic_api_key="k", voyage_api_key="v"), pool)
    assert gw2.configured() == {"chat": True, "embed": True}


async def test_voyage_adapter_requests_1024_dims() -> None:
    from types import SimpleNamespace

    from signal_core.ai_gateway.providers.voyage import VoyageProvider

    seen: dict[str, Any] = {}

    class FakeClient:
        async def embed(self, texts: list[str], **kw: Any) -> Any:
            seen.update(kw)
            return SimpleNamespace(embeddings=[[0.5] * 1024 for _ in texts], total_tokens=9)

    out = await VoyageProvider(SecretStr("k"), client=FakeClient()).embed(
        model="voyage-4", texts=["a"], input_type="query"
    )
    assert seen == {"model": "voyage-4", "input_type": "query", "output_dimension": 1024}
    assert out.tokens == 9 and len(out.vectors[0]) == 1024


class SettleFails(MemoryBudgetGuard):
    released: list[UUID] | None = None

    async def settle(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError("ledger down")

    async def release(self, reservation_id: UUID) -> None:
        self.released = [*(self.released or []), reservation_id]
        await super().release(reservation_id)


async def test_settle_failure_releases_reservation_and_propagates() -> None:
    budget = SettleFails()
    gw, _, _ = make(chat=FakeProvider({"*": GOOD}), budget=budget)
    with pytest.raises(RuntimeError, match="ledger down"):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert budget.released is not None and len(budget.released) == 1
    assert not budget.reservations


async def test_release_failure_during_settle_failure_is_swallowed() -> None:
    class BothFail(SettleFails):
        async def release(self, reservation_id: UUID) -> None:
            raise OSError("also down")

    gw, _, _ = make(chat=FakeProvider({"*": GOOD}), budget=BothFail())
    with pytest.raises(RuntimeError, match="ledger down"):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())


def test_strip_fence_negative_cases() -> None:
    from signal_core.ai_gateway.gateway import strip_fence

    assert strip_fence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_fence('```\n{"a": 1}\n```') == '{"a": 1}'
    # Text outside the fence is left alone (and then fails JSON validation).
    assert strip_fence('Here:\n```json\n{"a": 1}\n```') == 'Here:\n```json\n{"a": 1}\n```'
    assert strip_fence('```json\n{"a": 1}\n```\nThanks') == '```json\n{"a": 1}\n```\nThanks'
    # Only one fence layer is stripped; the inner fence stays and fails validation.
    assert strip_fence('```\n```json\n{"a": 1}\n```\n```') == '```json\n{"a": 1}\n```'
    # Any language tag is accepted by the regex; the payload is still validated later.
    assert strip_fence('```python\n{"a": 1}\n```') == '{"a": 1}'


async def test_text_around_fence_is_rejected() -> None:
    gw, budget, _ = make(chat=FakeProvider({"*": f"Sure!\n```json\n{GOOD}\n```"}))
    with pytest.raises(AiOutputInvalid):
        await gw.run(Capability.EXTRACT, load_prompt("canary_extract", 1), vars_(), Canary, ctx())
    assert [s.status for s in budget.settles] == ["rejected"]


async def test_memory_versions_detect_changed_prompt() -> None:
    from dataclasses import replace

    from signal_core.ai_gateway import PromptChanged

    mv = MemoryExtractorVersions()
    p = load_prompt("canary_extract", 1)
    first = await mv.ensure("extract", p, "m", "1")
    assert await mv.ensure("extract", p, "m", "1") == first
    with pytest.raises(PromptChanged):
        await mv.ensure("extract", replace(p, sha256="0" * 64), "m", "1")


@pytest.mark.db
async def test_pg_extractor_versions_idempotent() -> None:
    from dataclasses import replace

    from signal_core.ai_gateway import PgExtractorVersions, PromptChanged
    from signal_core.db import make_pool

    url = Settings().database_url_session
    assert url is not None
    prompt = replace(load_prompt("canary_extract", 1), name="test_prompt", sha256="a" * 64)
    pool = make_pool(url.get_secret_value())
    await pool.open()
    try:
        versions = PgExtractorVersions(pool)
        first = await versions.ensure("test", prompt, "test-model", "test-parser")
        second = await versions.ensure("test", prompt, "test-model", "test-parser")
        assert first == second
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select prompt_hash from public.extractor_version where id = %s", (first,)
            )
            row = await cur.fetchone()
        assert row is not None and row[0] == "a" * 64
        with pytest.raises(PromptChanged):
            await versions.ensure(
                "test", replace(prompt, sha256="b" * 64), "test-model", "test-parser"
            )
    finally:
        await pool.close()
