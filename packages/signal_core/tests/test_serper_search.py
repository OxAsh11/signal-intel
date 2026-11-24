"""Serper as the alternative web-search provider behind BraveSearchConnector."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from signal_core.config import Settings
from signal_core.connectors.base import FetchGuard
from signal_core.connectors.brave import (
    BraveError,
    BraveSearchConnector,
    SearchNotConfigured,
    serper_tbs,
)


def test_serper_tbs_maps_brave_freshness() -> None:
    today = date(2026, 11, 24)
    assert serper_tbs("py", today) == "qdr:y"
    assert serper_tbs("pd", today) == "qdr:d"
    assert serper_tbs("2026-11-24to2026-11-24", today) == "qdr:d"
    assert serper_tbs("2026-11-20to2026-11-24", today) == "qdr:w"
    assert serper_tbs("2026-11-01to2026-11-24", today) == "qdr:m"
    assert serper_tbs("2026-01-01to2026-11-24", today) == "qdr:y"
    assert serper_tbs("", today) is None


class _Resp:
    def __init__(self, status: int, data: dict[str, Any]) -> None:
        self.status = status
        self._data = data

    async def json(self, content_type: str | None = None) -> dict[str, Any]:
        return self._data

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *a: object) -> bool:
        return False


def _session(resp: _Resp, seen: list[Any]) -> type:
    class Session:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        def post(self, url: str, json: Any = None, headers: Any = None) -> _Resp:
            seen.append((url, json, headers))
            return resp

    return Session


async def _search(resp: _Resp, params: dict[str, str]) -> tuple[dict[str, Any], list[Any]]:
    seen: list[Any] = []
    c = BraveSearchConnector("tender", api_key="sk", provider="serper")
    with patch("aiohttp.ClientSession", new=_session(resp, seen)):
        out = await c._default_search("q", params)
    return out, seen


async def test_maps_organic_into_brave_shape_and_sends_header_and_body() -> None:
    organic = [{"link": "https://a.test/1", "title": "A", "snippet": "S", "position": 1}]
    out, seen = await _search(_Resp(200, {"organic": organic}), {"count": "7", "freshness": "py"})
    assert out == {
        "web": {"results": [{"url": "https://a.test/1", "title": "A", "description": "S"}]}
    }
    url, body, headers = seen[0]
    assert url == "https://google.serper.dev/search"
    assert body == {"q": "q", "num": 7, "tbs": "qdr:y"}
    assert headers["X-API-KEY"] == "sk"


async def test_unparseable_freshness_omits_tbs() -> None:
    _, seen = await _search(_Resp(200, {}), {"count": "3", "freshness": "weird"})
    assert "tbs" not in seen[0][1]


async def test_401_is_not_configured_and_500_is_search_error() -> None:
    with pytest.raises(SearchNotConfigured):
        await _search(_Resp(401, {}), {"count": "1"})
    with pytest.raises(BraveError):
        await _search(_Resp(500, {}), {"count": "1"})


def test_search_api_key_follows_provider() -> None:
    both: dict[str, Any] = {
        "serper_api_key": SecretStr("S"),
        "brave_search_api_key": SecretStr("B"),
    }
    assert Settings(_env_file=None, **both).search_api_key() == "S"  # type: ignore[call-arg]
    brave = Settings(_env_file=None, search_provider="brave", **both)  # type: ignore[call-arg]
    assert brave.search_api_key() == "B"
    only_brave = Settings(_env_file=None, brave_search_api_key=SecretStr("B"))  # type: ignore[call-arg]
    assert only_brave.search_api_key() is None


async def test_default_provider_serper_without_key_is_not_set_up() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.search_provider == "serper"
    c = BraveSearchConnector("tender", api_key=s.search_api_key(), provider=s.search_provider)
    with pytest.raises(SearchNotConfigured, match="not set up yet"):
        await c.fetch({}, ["q"], {}, FetchGuard(), 5)
