"""Brave Search connector (M2 web-search radar types)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from signal_core.connectors.base import FetchBlocked, FetchDisallowed, FetchGuard
from signal_core.connectors.brave import (
    BraveError,
    BraveSearchConnector,
    SearchNotConfigured,
    build_query,
    freshness,
    targeted_search,
)

TODAY = datetime.now(tz=UTC).date()


class ScriptedGuard(FetchGuard):
    def __init__(self, outcomes: dict[str, Exception] | None = None) -> None:
        super().__init__()
        self.outcomes = outcomes or {}

    async def check(self, url: str) -> None:
        if url in self.outcomes:
            raise self.outcomes[url]


class FakeSearch:
    def __init__(self, results: dict[str, list[dict[str, Any]]], error: Exception | None = None):
        self.results = results
        self.error = error
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, q: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((q, params))
        if self.error:
            raise self.error
        return {"web": {"results": self.results.get(q, [])}}


class FakeMeter:
    def __init__(self) -> None:
        self.reserved: list[int] = []
        self.settled: list[tuple[object, int]] = []

    async def reserve(self, n_calls: int) -> object:
        self.reserved.append(n_calls)
        return "tok"

    async def settle(self, token: object, calls: int) -> None:
        self.settled.append((token, calls))


def result(url: str, title: str = "T", desc: str = "D", extra: list[str] | None = None):
    return {"url": url, "title": title, "description": desc, "extra_snippets": extra or []}


async def page(url: str) -> bytes | None:
    return b"<html><body><p>Full page about the hotel tender</p></body></html>"


def connector(search, *, kind="tender", meter=None, fetch_page=page, key="k"):
    return BraveSearchConnector(
        kind, api_key=key, search=search, fetch_page=fetch_page, meter=meter
    )


def test_freshness_first_run_is_past_year():
    assert freshness({}, date(2026, 11, 13)) == "py"
    assert freshness({"since": "2026-11-01"}, date(2026, 11, 13)) == "2026-11-01to2026-11-13"


def test_social_query_adds_default_sites():
    q = build_query("hotel opening", "social_post", None)
    assert q.startswith("hotel opening (site:linkedin.com OR site:x.com")
    assert q.endswith(")")
    assert build_query("q", "tender", ["ted.europa.eu"]) == "q (site:ted.europa.eu)"
    assert build_query("q", "tender", None) == "q"


@pytest.mark.asyncio
async def test_fetch_full_page_text():
    s = FakeSearch({"q1": [result("https://a.test/1", title="Award")]})
    payloads, _ = await connector(s).fetch({}, ["q1"], {}, ScriptedGuard(), 50)
    assert len(payloads) == 1
    p = payloads[0]
    assert p.content_kind == "full"
    assert p.query == "q1"
    assert b"Award" in p.raw and b"Full page about the hotel tender" in p.raw


@pytest.mark.asyncio
async def test_robots_blocked_keeps_snippet():
    url = "https://social.test/post"
    s = FakeSearch({"q1": [result(url, title="Post", desc="Desc", extra=["e1", "e2"])]})
    guard = ScriptedGuard({url: FetchDisallowed("no")})
    payloads, _ = await connector(s).fetch({}, ["q1"], {}, guard, 50)
    assert [p.content_kind for p in payloads] == ["snippet"]
    for part in (b"Post", b"Desc", b"e1", b"e2"):
        assert part in payloads[0].raw


@pytest.mark.asyncio
async def test_page_error_keeps_snippet():
    async def broken(url: str) -> bytes | None:
        return None

    s = FakeSearch({"q1": [result("https://a.test/1")]})
    payloads, _ = await connector(s, fetch_page=broken).fetch({}, ["q1"], {}, ScriptedGuard(), 50)
    assert payloads[0].content_kind == "snippet"


@pytest.mark.asyncio
async def test_private_ip_result_dropped():
    url = "http://10.0.0.1/x"
    s = FakeSearch({"q1": [result(url)]})
    payloads, _ = await connector(s).fetch(
        {}, ["q1"], {}, ScriptedGuard({url: FetchBlocked("private")}), 50
    )
    assert payloads == []


@pytest.mark.asyncio
async def test_cursor_advances_to_today_and_freshness_is_sent():
    s = FakeSearch({"q1": []})
    _, cursor = await connector(s).fetch({}, ["q1"], {"since": "2026-01-01"}, ScriptedGuard(), 50)
    assert cursor == {"since": TODAY.isoformat()}
    assert s.calls[0][1]["freshness"] == f"2026-01-01to{TODAY.isoformat()}"


@pytest.mark.asyncio
async def test_missing_key_raises_before_any_call():
    s = FakeSearch({})
    meter = FakeMeter()
    with pytest.raises(SearchNotConfigured):
        await connector(s, meter=meter, key=None).fetch({}, ["q1"], {}, ScriptedGuard(), 50)
    assert s.calls == [] and meter.reserved == []


@pytest.mark.asyncio
async def test_meter_reserves_per_query_and_settles_actual():
    s = FakeSearch({q: [result(f"https://a.test/{q}")] for q in ("q1", "q2", "q3")})
    meter = FakeMeter()
    payloads, _ = await connector(s, meter=meter).fetch(
        {}, ["q1", "q2", "q3"], {}, ScriptedGuard(), 2
    )
    assert len(payloads) == 2
    assert meter.reserved == [3]
    assert meter.settled == [("tok", 2)]


@pytest.mark.asyncio
async def test_search_error_settles_before_raising():
    meter = FakeMeter()
    s = FakeSearch({}, error=BraveError("429"))
    with pytest.raises(BraveError):
        await connector(s, meter=meter).fetch({}, ["q1"], {}, ScriptedGuard(), 50)
    assert meter.settled == [("tok", 0)]


@pytest.mark.asyncio
async def test_duplicate_urls_across_queries_kept_once():
    s = FakeSearch({"q1": [result("https://a.test/1")], "q2": [result("https://a.test/1")]})
    payloads, _ = await connector(s).fetch({}, ["q1", "q2"], {}, ScriptedGuard(), 50)
    assert [p.query for p in payloads] == ["q1"]


@pytest.mark.asyncio
async def test_sites_config_reaches_the_query():
    s = FakeSearch({})
    await connector(s).fetch({"sites": ["ted.europa.eu"]}, ["q1"], {}, ScriptedGuard(), 50)
    assert s.calls[0][0] == "q1 (site:ted.europa.eu)"


@pytest.mark.asyncio
async def test_fake_mode_returns_fixtures_without_network():
    s = FakeSearch({})
    payloads, cursor = await connector(s, key=None).fetch(
        {"fake": True}, ["q1"], {}, ScriptedGuard(), 50
    )
    assert s.calls == []
    assert len(payloads) == 2
    assert sorted(p.content_kind for p in payloads) == ["full", "snippet"]
    assert all(p.query == "q1" for p in payloads)
    assert cursor == {"since": TODAY.isoformat()}


@pytest.mark.asyncio
async def test_targeted_search_quotes_the_name_and_adds_terms():
    s = FakeSearch({})
    await targeted_search(
        connector(s), "Acme Hotels", ["tender", "fit-out", "award", "x"], ScriptedGuard()
    )
    q, params = s.calls[0]
    assert q == '"Acme Hotels" tender fit-out award'
    assert params["count"] == "5"


# ── Review fixes ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redirect_to_a_private_address_is_blocked():
    from signal_core.connectors.brave import fetch_page_guarded

    async def get(url: str):
        if url == "https://a.test/x":
            return 302, "text/html", "http://169.254.169.254/latest", b""
        return 200, "text/html", None, b"<body>secret</body>"

    guard = ScriptedGuard({"http://169.254.169.254/latest": FetchBlocked("private")})
    with pytest.raises(FetchBlocked):
        await fetch_page_guarded("https://a.test/x", guard, get=get)


@pytest.mark.asyncio
async def test_redirects_are_followed_through_the_guard_and_capped():
    from signal_core.connectors.brave import fetch_page_guarded

    seen: list[str] = []

    class Recording(ScriptedGuard):
        async def check(self, url: str) -> None:
            seen.append(url)

    async def hop(url: str):
        if url.endswith("/final"):
            return 200, "text/html; charset=utf-8", None, b"<body>ok</body>"
        return 301, "text/html", "/final", b""

    assert (
        await fetch_page_guarded("https://a.test/start", Recording(), get=hop) == b"<body>ok</body>"
    )
    assert seen == ["https://a.test/start", "https://a.test/final"]

    async def loop(url: str):
        return 302, "text/html", url + "x", b""

    assert await fetch_page_guarded("https://a.test/l", Recording(), get=loop) is None


@pytest.mark.asyncio
async def test_unexpected_guard_error_keeps_the_snippet():
    url = "https://xn--bad.test/"
    s = FakeSearch({"q1": [result(url, title="T")]})
    payloads, _ = await connector(s).fetch(
        {}, ["q1"], {}, ScriptedGuard({url: UnicodeError("idna")}), 50
    )
    assert [p.content_kind for p in payloads] == ["snippet"]


@pytest.mark.asyncio
async def test_result_pages_are_fetched_concurrently_in_order():
    import asyncio

    active = 0
    peak = 0

    async def slow_page(url: str) -> bytes | None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return f"<body>{url}</body>".encode()

    urls = [f"https://a.test/{i}" for i in range(10)]
    s = FakeSearch({"q1": [result(u) for u in urls]})
    payloads, _ = await connector(s, fetch_page=slow_page).fetch(
        {}, ["q1"], {}, ScriptedGuard(), 50
    )
    assert [p.url for p in payloads] == urls
    assert 1 < peak <= 8


@pytest.mark.asyncio
async def test_guard_resolves_dns_off_the_event_loop(monkeypatch):
    import asyncio

    calls: list[str] = []

    async def fake_to_thread(fn, *args):
        calls.append(fn.__name__)
        return fn(*args)

    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(FetchGuard, "_check_private_ip", staticmethod(lambda host: None))

    async def no_robots(self, url, parsed):
        return None

    monkeypatch.setattr(FetchGuard, "_check_robots", no_robots)
    await FetchGuard().check("https://a.test/")
    assert calls == ["<lambda>"]
