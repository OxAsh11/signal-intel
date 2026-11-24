"""OpenRouter chat adapter (any model OpenRouter hosts) over its OpenAI-style chat API.

``AI_MODEL_*`` hold OpenRouter model ids (``anthropic/claude-sonnet-4.5``). Each id used needs
rows in ``public.model_price`` (``ai_input_tokens`` and ``ai_output_tokens``); the gateway raises
``AiNotConfigured`` for an unpriced model before any call is made.

Structured output uses ``response_format`` json_schema (strict). Retries: one, on 429 or 5xx.
The key goes only in the Authorization header and is never logged or put in an error message.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from signal_core.ai_gateway.errors import AiNotConfigured, AiProviderError
from signal_core.ai_gateway.providers.anthropic import strict_schema
from signal_core.ai_gateway.providers.base import Completion

__all__ = ["OPENROUTER_URL", "OpenRouterChatProvider"]

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 60.0
RETRY_DELAY_SECONDS = 1.0

_STOP_MAP = {"stop": "end_turn", "length": "max_tokens", "content_filter": "refusal"}


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        message = err.get("message") if isinstance(err, dict) else None
    except ValueError:
        message = None
    return f"{resp.status_code}: {str(message)[:250]}" if message else str(resp.status_code)


class OpenRouterChatProvider:
    name = "openrouter"

    def __init__(self, api_key: SecretStr, *, client: httpx.AsyncClient | None = None) -> None:
        self._key = api_key
        self._client = client

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        owned = self._client is None
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            resp: httpx.Response | None = None
            for attempt in range(2):
                try:
                    resp = await client.post(OPENROUTER_URL, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    if attempt == 0:
                        await asyncio.sleep(RETRY_DELAY_SECONDS)
                        continue
                    raise AiProviderError(f"openrouter error: {type(exc).__name__}") from None
                if attempt == 0 and (resp.status_code == 429 or resp.status_code >= 500):
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                    continue
                break
            assert resp is not None
            return resp
        finally:
            if owned:
                await client.aclose()

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
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": strict_schema(json_schema),
                    "strict": True,
                },
            }
        resp = await self._post(payload)
        if resp.status_code in (401, 403):
            raise AiNotConfigured("OpenRouter rejected the API key (check OPENROUTER_API_KEY)")
        if resp.status_code >= 400:
            raise AiProviderError(f"openrouter error: {_error_text(resp)}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise AiProviderError("openrouter error: unexpected response shape") from None
        usage = data.get("usage") or {}
        finish = choice.get("finish_reason") or "stop"
        return Completion(
            text=str(content),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            model=model,
            stop_reason=_STOP_MAP.get(finish, "end_turn"),
        )
