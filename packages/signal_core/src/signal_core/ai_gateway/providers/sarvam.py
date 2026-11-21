"""Sarvam Token Factory chat adapter (GLM and other open-weight models).

Talks to Sarvam's OpenAI-compatible ``/v2/chat/completions`` through the
official ``sarvamai`` SDK with structured outputs (``response_format``
json_schema). Pricing rows for the models used must exist in
``public.model_price`` (provider ``sarvam``).

Reasoning: ``glm5.3`` defaults to ``max``, which burns thousands of
output-billed reasoning tokens; structured extraction and verdict work does
not need it, so this provider pins ``reasoning_effort="low"``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import SecretStr
from sarvamai import SarvamAI

from signal_core.ai_gateway.errors import AiProviderError
from signal_core.ai_gateway.providers.anthropic import strict_schema
from signal_core.ai_gateway.providers.base import Completion

__all__ = ["SarvamChatProvider"]

TIMEOUT_SECONDS = 120.0
# The gateway's default 2048 can be eaten by reasoning plus a large schema.
MIN_OUTPUT_TOKENS = 4096

_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


class SarvamChatProvider:
    name = "sarvam"

    def __init__(self, api_key: SecretStr, *, client: SarvamAI | None = None) -> None:
        self._client = client or SarvamAI(api_subscription_key=api_key.get_secret_value())

    async def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int,
        json_schema: dict[str, Any] | None,
        effort: Literal["low"] | None = None,
    ) -> Completion:
        response_format: dict[str, Any] | None = None
        if json_schema is not None:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": strict_schema(json_schema),
                    "strict": True,
                },
            }

        def call() -> Any:
            return self._client.chat.completions_v2(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max(MIN_OUTPUT_TOKENS, max_tokens),
                reasoning_effort="low",
                response_format=response_format,
                request_options={"timeout_in_seconds": int(TIMEOUT_SECONDS)},
            )

        try:
            resp = await asyncio.to_thread(call)
        except Exception as exc:
            raise AiProviderError(f"sarvam error: {type(exc).__name__}: {str(exc)[:250]}") from None

        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        finish = getattr(choice, "finish_reason", "stop") or "stop"
        return Completion(
            text=choice.message.content or "",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=model,
            stop_reason=_STOP_MAP.get(finish, "end_turn"),
        )
