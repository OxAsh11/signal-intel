"""The single door for AI. Modules ask for a capability, never a vendor.

Order for every call: resolve model, check price, reserve budget, call, validate, settle.
Nothing here logs prompt text or completions.
"""

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ValidationError

from signal_core.ai_gateway.errors import (
    AiNotConfigured,
    AiOutputInvalid,
    AiProviderError,
    PromptChanged,
)
from signal_core.ai_gateway.prompts import Capability, Prompt, render
from signal_core.ai_gateway.providers.base import ChatProvider, Completion, EmbedProvider
from signal_core.config import Settings
from signal_core.contracts.enums import UsageUnit
from signal_core.logging import get_logger
from signal_core.metering import (
    BudgetGuard,
    CallContext,
    PriceBook,
    SettleStatus,
    UsageLine,
    cost_usd,
)

log = get_logger(__name__)

PARSER_VERSION = "1"
_FENCE = re.compile(r"\A```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?```\Z", re.DOTALL)


class ExtractorVersions(Protocol):
    async def ensure(
        self, capability: str, prompt: Prompt, model: str, parser_version: str
    ) -> UUID: ...


class PgExtractorVersions:
    """Upserts public.extractor_version and returns the row id."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def ensure(
        self, capability: str, prompt: Prompt, model: str, parser_version: str
    ) -> UUID:
        version = f"{prompt.name}/v{prompt.version}"
        async with self._pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                insert into public.extractor_version
                  (capability, prompt_version, model, parser_version, prompt_hash)
                values (%s, %s, %s, %s, %s)
                on conflict (capability, prompt_version, model, parser_version) do nothing
                returning id
                """,
                (capability, version, model, parser_version, prompt.sha256),
            )
            row = await cur.fetchone()
            stored_hash: str | None = prompt.sha256
            if row is None:
                cur = await conn.execute(
                    """
                    select id, prompt_hash from public.extractor_version
                    where capability = %s and prompt_version = %s
                      and model = %s and parser_version = %s
                    """,
                    (capability, version, model, parser_version),
                )
                row = await cur.fetchone()
                stored_hash = row[1] if row else None
        if row is None:
            raise AiProviderError("extractor_version row not found")
        if stored_hash is not None and stored_hash != prompt.sha256:
            raise PromptChanged(f"{version} differs from the released prompt")
        return UUID(str(row[0]))


class MemoryExtractorVersions:
    """Test double with the same semantics: one id per (capability, prompt, model, parser)."""

    def __init__(self) -> None:
        self.ids: dict[tuple[str, str, str, str], UUID] = {}
        self.hashes: dict[UUID, str] = {}

    async def ensure(
        self, capability: str, prompt: Prompt, model: str, parser_version: str
    ) -> UUID:
        key = (capability, f"{prompt.name}/v{prompt.version}", model, parser_version)
        if key in self.ids and self.hashes[self.ids[key]] != prompt.sha256:
            raise PromptChanged(f"{key[1]} differs from the released prompt")
        if key not in self.ids:
            self.ids[key] = uuid4()
            self.hashes[self.ids[key]] = prompt.sha256
        return self.ids[key]


@dataclass(frozen=True)
class Result[T: BaseModel]:
    value: T
    extractor_version_id: UUID
    cost_usd: Decimal


def strip_fence(text: str) -> str:
    """Strip one surrounding markdown code fence, if present."""
    stripped = text.strip()
    match = _FENCE.match(stripped)
    return match.group(1).strip() if match else stripped


def _error_strings(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()]


class AiGateway:
    def __init__(
        self,
        *,
        settings: Settings,
        chat: ChatProvider | None,
        embedder: EmbedProvider | None,
        prices: PriceBook,
        budget: BudgetGuard,
        versions: ExtractorVersions,
    ) -> None:
        self._settings = settings
        self._chat = chat
        self._embedder = embedder
        self._prices = prices
        self._budget = budget
        self._versions = versions

    def configured(self) -> dict[str, bool]:
        return {"chat": self._chat is not None, "embed": self._embedder is not None}

    def model_for(self, capability: Capability) -> str:
        s = self._settings
        return {
            Capability.EXTRACT: s.ai_model_extract,
            Capability.JUDGE: s.ai_model_judge,
            Capability.PREJUDGE: s.ai_model_prejudge,
            Capability.SUMMARISE: s.ai_model_write,
            Capability.WRITE: s.ai_model_write,
            Capability.EMBED: s.ai_model_embed,
        }[capability]

    async def _price(self, model: str, unit: UsageUnit) -> Decimal:
        price = await self._prices.price(model, unit)
        if price is None:
            raise AiNotConfigured(f"no price for model {model} ({unit})")
        return price

    async def run[T: BaseModel](
        self,
        capability: Capability,
        prompt: Prompt,
        variables: Mapping[str, str],
        output: type[T],
        ctx: CallContext,
        *,
        max_tokens: int = 2048,
    ) -> Result[T]:
        if capability is Capability.EMBED:
            raise ValueError("use embed() for the embed capability")
        chat = self._chat
        if chat is None:
            raise AiNotConfigured("no chat provider key is set")
        model = self.model_for(capability)
        in_price = await self._price(model, UsageUnit.AI_INPUT_TOKENS)
        out_price = await self._price(model, UsageUnit.AI_OUTPUT_TOKENS)

        user = render(prompt, variables)
        estimate = cost_usd(in_price, (len(prompt.system) + len(user)) // 4) + cost_usd(
            out_price, max_tokens
        )
        version_id = await self._versions.ensure(str(capability), prompt, model, PARSER_VERSION)
        reservation = await self._budget.reserve(ctx, estimate)

        started = time.monotonic()
        try:
            completion = await chat.complete(
                model=model,
                system=prompt.system,
                user=user,
                max_tokens=max_tokens,
                json_schema=output.model_json_schema(),
                effort="low" if capability is Capability.PREJUDGE else None,
            )
        except BaseException:
            await self._budget.release(reservation)
            raise
        latency_ms = int((time.monotonic() - started) * 1000)

        lines = [
            UsageLine(
                UsageUnit.AI_INPUT_TOKENS,
                completion.input_tokens,
                cost_usd(in_price, completion.input_tokens),
            ),
            UsageLine(
                UsageUnit.AI_OUTPUT_TOKENS,
                completion.output_tokens,
                cost_usd(out_price, completion.output_tokens),
            ),
        ]
        total = sum((ln.cost_usd for ln in lines), Decimal(0))

        async def settle(status: SettleStatus) -> None:
            await self._settle_or_release(
                reservation, ctx, str(capability), chat.name, model, lines, latency_ms, status
            )
            self._log_call(capability, prompt, completion, total, latency_ms, status)

        if completion.stop_reason == "refusal":
            await settle("rejected")
            raise AiProviderError("refused")
        if completion.stop_reason == "max_tokens":
            await settle("rejected")
            raise AiOutputInvalid(["output truncated at max_tokens"])
        try:
            value = output.model_validate_json(strip_fence(completion.text))
        except ValidationError as exc:
            await settle("rejected")
            raise AiOutputInvalid(_error_strings(exc)) from exc
        await settle("ok")
        return Result(value=value, extractor_version_id=version_id, cost_usd=total)

    async def embed(
        self,
        texts: list[str],
        ctx: CallContext,
        *,
        input_type: Literal["document", "query"],
    ) -> list[list[float]]:
        embedder = self._embedder
        if embedder is None:
            raise AiNotConfigured("no embedding provider key is set")
        model = self.model_for(Capability.EMBED)
        price = await self._price(model, UsageUnit.EMBED_TOKENS)
        estimate = cost_usd(price, sum(len(t) for t in texts) // 4)
        reservation = await self._budget.reserve(ctx, estimate)

        started = time.monotonic()
        try:
            result = await embedder.embed(model=model, texts=texts, input_type=input_type)
        except BaseException:
            await self._budget.release(reservation)
            raise
        latency_ms = int((time.monotonic() - started) * 1000)
        cost = cost_usd(price, result.tokens)
        await self._settle_or_release(
            reservation,
            ctx,
            str(Capability.EMBED),
            embedder.name,
            model,
            [UsageLine(UsageUnit.EMBED_TOKENS, result.tokens, cost)],
            latency_ms,
            "ok",
        )
        log.info(
            "ai.call",
            capability="embed",
            model=model,
            input_tokens=result.tokens,
            output_tokens=0,
            cost_usd=str(cost),
            latency_ms=latency_ms,
            status="ok",
        )
        return result.vectors

    async def _settle_or_release(
        self,
        reservation: UUID,
        ctx: CallContext,
        capability: str,
        provider: str,
        model: str,
        lines: list[UsageLine],
        latency_ms: int,
        status: SettleStatus,
    ) -> None:
        """Settle; if that fails, free the reservation so its estimate stops counting."""
        try:
            await self._budget.settle(
                reservation, ctx, capability, provider, model, lines, latency_ms, status
            )
        except BaseException:
            try:
                await self._budget.release(reservation)
            except Exception:
                log.error("ai.release_failed", reservation_id=str(reservation))
            raise

    def _log_call(
        self,
        capability: Capability,
        prompt: Prompt,
        completion: Completion,
        cost: Decimal,
        latency_ms: int,
        status: SettleStatus,
    ) -> None:
        log.info(
            "ai.call",
            capability=str(capability),
            prompt=f"{prompt.name}/v{prompt.version}",
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=str(cost),
            latency_ms=latency_ms,
            status=status,
        )
