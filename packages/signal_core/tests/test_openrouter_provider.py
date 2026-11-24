"""OpenRouter chat adapter and factory selection, against recorded responses (respx)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
import respx
from pydantic import SecretStr

from signal_core.ai_gateway.errors import AiNotConfigured, AiProviderError
from signal_core.ai_gateway.factory import build_gateway
from signal_core.ai_gateway.providers import openrouter
from signal_core.ai_gateway.providers.base import Completion
from signal_core.ai_gateway.providers.openrouter import OPENROUTER_URL, OpenRouterChatProvider
from signal_core.config import Settings

KEY = "sk-or-SECRET-0123456789"
MODEL = "anthropic/claude-sonnet-4.5"


def ok(content: str = "hello", finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


def provider() -> OpenRouterChatProvider:
    return OpenRouterChatProvider(SecretStr(KEY))


async def complete(p: OpenRouterChatProvider, schema: dict[str, Any] | None = None) -> Completion:
    return await p.complete(
        model=MODEL, system="sys", user="usr", max_tokens=100, json_schema=schema
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(openrouter, "RETRY_DELAY_SECONDS", 0)


@respx.mock
async def test_completion_and_usage_mapping() -> None:
    route = respx.post(OPENROUTER_URL).mock(return_value=ok("hi", "length"))
    result = await complete(provider())
    assert (result.text, result.input_tokens, result.output_tokens) == ("hi", 11, 7)
    assert result.model == MODEL
    assert result.stop_reason == "max_tokens"
    req = route.calls[0].request
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == MODEL
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert "response_format" not in body


@respx.mock
async def test_structured_output_sends_strict_schema() -> None:
    route = respx.post(OPENROUTER_URL).mock(return_value=ok('{"a": 1}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    result = await complete(provider(), schema)
    assert json.loads(result.text) == {"a": 1}
    rf = json.loads(route.calls[0].request.content)["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


@respx.mock
async def test_retries_once_on_429_then_succeeds() -> None:
    route = respx.post(OPENROUTER_URL).mock(
        side_effect=[httpx.Response(429, json={"error": {"message": "slow down"}}), ok()]
    )
    assert (await complete(provider())).text == "hello"
    assert route.call_count == 2


@respx.mock
async def test_second_5xx_raises_provider_error_without_key() -> None:
    route = respx.post(OPENROUTER_URL).mock(
        return_value=httpx.Response(503, json={"error": {"message": "down"}})
    )
    with pytest.raises(AiProviderError) as exc:
        await complete(provider())
    assert route.call_count == 2
    assert KEY not in str(exc.value)
    assert "503" in str(exc.value)


@respx.mock
async def test_401_is_a_clear_not_configured_error_and_not_retried() -> None:
    route = respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(AiNotConfigured, match="OPENROUTER_API_KEY") as exc:
        await complete(provider())
    assert route.call_count == 1
    assert KEY not in str(exc.value)


@respx.mock
async def test_network_error_after_retry_is_provider_error() -> None:
    respx.post(OPENROUTER_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(AiProviderError):
        await complete(provider())


@respx.mock
async def test_malformed_body_is_provider_error() -> None:
    respx.post(OPENROUTER_URL).mock(return_value=httpx.Response(200, json={"choices": []}))
    with pytest.raises(AiProviderError):
        await complete(provider())


def settings(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


def test_factory_selects_openrouter_only_when_chosen_and_keyed() -> None:
    pool = MagicMock()
    gw = build_gateway(
        settings(ai_chat_provider="openrouter", openrouter_api_key=SecretStr(KEY)), pool
    )
    assert gw._chat is not None
    assert gw._chat.name == "openrouter"

    # Key present but provider is anthropic: not selected.
    gw = build_gateway(
        settings(openrouter_api_key=SecretStr(KEY), anthropic_api_key=SecretStr("a")), pool
    )
    assert gw._chat is not None
    assert gw._chat.name != "openrouter"


def test_factory_openrouter_without_key_is_not_set_up() -> None:
    gw = build_gateway(
        settings(ai_chat_provider="openrouter", anthropic_api_key=SecretStr("a")), MagicMock()
    )
    assert gw.configured()["chat"] is False
