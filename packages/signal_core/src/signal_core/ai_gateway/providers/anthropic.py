"""Anthropic Messages adapter with structured outputs. Retries are the SDK's (max_retries=2)."""

from typing import Any, Literal

from anthropic import (
    AnthropicError,
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
)
from pydantic import SecretStr

from signal_core.ai_gateway.errors import AiProviderError
from signal_core.ai_gateway.providers.base import Completion

__all__ = ["AnthropicProvider", "AsyncAnthropic", "strict_schema"]

MAX_RETRIES = 2


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with `additionalProperties: false` on every object, `$defs` included."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object" or "properties" in out:
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result: dict[str, Any] = walk(schema)
    return result


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: SecretStr, *, client: AsyncAnthropic | None = None) -> None:
        self._client = client or AsyncAnthropic(
            api_key=api_key.get_secret_value(), max_retries=MAX_RETRIES
        )

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
        output_config: dict[str, Any] = {}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": strict_schema(json_schema)}
        if effort is not None:
            output_config["effort"] = effort
        extra: dict[str, Any] = {"output_config": output_config} if output_config else {}
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                **extra,
            )
        except APIStatusError as exc:
            raise AiProviderError(f"anthropic status {exc.status_code}") from exc
        except APIConnectionError as exc:
            raise AiProviderError(f"anthropic connection error: {type(exc).__name__}") from exc
        except AnthropicError as exc:
            raise AiProviderError(f"anthropic error: {type(exc).__name__}") from exc
        text = "".join(b.text for b in response.content if b.type == "text")
        return Completion(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=model,
            stop_reason=str(response.stop_reason or "end_turn"),
        )
