"""Brave Search connector — one class serving the web-search radar types (M2).

Registries and permits, tenders and procurement, events, directories and partner lists, and
public social posts are all found globally through web search rather than per-country APIs.

* Each radar query runs against Brave's web search, limited by ``freshness`` to the days since
  the radar's last fetch (first run: the past year).
* Brave returns snippets only, so every result page is fetched through :class:`FetchGuard` and
  stripped to text. When robots.txt disallows the page or it cannot be fetched, the record keeps
  Brave's title and snippets (``content_kind = "snippet"``). A result on a private or reserved
  address is dropped.
* A fetch reserves budget for one call per query before any call and settles the calls made (S6).
* Without ``BRAVE_SEARCH_API_KEY`` the fetch raises :class:`SearchNotConfigured` before any call.

Fake mode: ``config["fake"]`` returns two fixtures (one full page, one snippet) with no network.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urljoin
from uuid import UUID

import aiohttp

from signal_core.connectors import SourcePayload
from signal_core.connectors.base import FetchBlocked, FetchDisallowed
from signal_core.connectors.base import content_hash as _content_hash
from signal_core.connectors.website import _strip_html
from signal_core.logging import get_logger
from signal_core.metering import BudgetPaused, CallContext

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from signal_core.connectors.base import FetchGuard

log = get_logger(__name__)

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
PRICE_MODEL = "brave-search"
_FALLBACK_PRICE = Decimal("0.005")
_MAX_COUNT = 20
_MAX_PAGE_BYTES = 2_000_000
_SEARCH_TIMEOUT = aiohttp.ClientTimeout(total=20)
_PAGE_TIMEOUT = aiohttp.ClientTimeout(total=15)
_MAX_REDIRECTS = 3
_PAGE_CONCURRENCY = 8
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# connector_type → source_record.source_type
BRAVE_KINDS: dict[str, str] = {
    "registry": "registry",
    "tender": "tender",
    "event": "event",
    "directory": "directory",
    "social_post": "social",
}

SOCIAL_SITES = [
    "linkedin.com",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "reddit.com",
    "youtube.com",
    "threads.net",
    "mastodon.social",
]

# Targeted search for one entity is not a radar type; its records have source_type "web_search".
TARGETED_KIND = "web_search"

NOT_SET_UP = "Brave Search is not set up yet"


class SearchNotConfigured(Exception):
    """No (or a rejected) Brave Search key: retrying will not help."""


class BraveError(Exception):
    """Brave answered with a rate limit or server error; the job may retry."""


SearchFn = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]
PageFn = Callable[[str], Awaitable[bytes | None]]
# (status, content_type, location, body) for one GET without following redirects.
RawGet = Callable[[str], Awaitable[tuple[int, str, str | None, bytes]]]


class SearchMeter(Protocol):
    async def reserve(self, n_calls: int) -> object | None: ...

    async def settle(self, token: object | None, calls: int) -> None: ...


def build_query(query: str, kind: str, sites: list[str] | None) -> str:
    """Append a ``site:`` clause: the radar's own sites, else the public social defaults."""
    chosen = sites or (SOCIAL_SITES if kind == "social_post" else None)
    if not chosen:
        return query
    return f"{query} ({' OR '.join(f'site:{s}' for s in chosen)})"


def freshness(cursor: dict[str, Any], today: date) -> str:
    since = cursor.get("since")
    return f"{since}to{today.isoformat()}" if since else "py"


class BraveSearchConnector:
    def __init__(
        self,
        kind: str,
        *,
        api_key: str | None,
        search: SearchFn | None = None,
        fetch_page: PageFn | None = None,
        meter: SearchMeter | None = None,
    ) -> None:
        if kind not in BRAVE_KINDS and kind != TARGETED_KIND:
            raise ValueError(f"unknown Brave radar kind {kind!r}")
        self.kind = kind
        self.source_type = BRAVE_KINDS.get(kind, kind)
        self._api_key = api_key
        self._search = search or self._default_search
        # Injected page fetchers (tests) get the guard checked here; the default follows
        # redirects itself so that every hop goes through the guard.
        self._fetch_page = fetch_page
        self._meter = meter

    async def fetch(
        self,
        config: dict[str, Any],
        queries: list[str],
        cursor: dict[str, Any],
        guard: FetchGuard,
        limit: int,
    ) -> tuple[list[SourcePayload], dict[str, Any]]:
        today = datetime.now(tz=UTC).date()
        next_cursor = {"since": today.isoformat()}
        if config.get("fake"):
            return self._fake(queries[0] if queries else None), next_cursor
        if not self._api_key:
            raise SearchNotConfigured(NOT_SET_UP)

        sites = config.get("sites") or None
        sem = asyncio.Semaphore(_PAGE_CONCURRENCY)
        fresh = freshness(cursor, today)
        token = await self._meter.reserve(len(queries)) if self._meter else None
        calls = 0
        seen: set[str] = set()
        payloads: list[SourcePayload] = []
        try:
            for query in queries:
                if len(payloads) >= limit:
                    break
                count = min(_MAX_COUNT, limit - len(payloads))
                params = {"count": str(count), "freshness": fresh}
                data = await self._search(build_query(query, self.kind, sites), params)
                calls += 1
                fresh_results = []
                for res in (data.get("web") or {}).get("results") or []:
                    url = str(res.get("url") or "")
                    if url and url not in seen:
                        seen.add(url)
                        fresh_results.append(res)
                for payload in await asyncio.gather(
                    *(self._guarded_payload(sem, r, query, guard) for r in fresh_results)
                ):
                    if payload is not None and len(payloads) < limit:
                        payloads.append(payload)
        finally:
            if self._meter:
                await self._meter.settle(token, calls)
        return payloads, next_cursor

    async def _guarded_payload(
        self, sem: asyncio.Semaphore, res: dict[str, Any], query: str, guard: FetchGuard
    ) -> SourcePayload | None:
        async with sem:
            return await self._payload(res, query, guard)

    async def _payload(
        self, res: dict[str, Any], query: str, guard: FetchGuard
    ) -> SourcePayload | None:
        url = str(res["url"])
        title = str(res.get("title") or "")
        html: bytes | None = None
        try:
            if self._fetch_page is not None:
                await guard.check(url)
                html = await self._fetch_page(url)
            else:
                html = await fetch_page_guarded(url, guard)
        except FetchBlocked:
            log.info("brave.result_blocked", url=url)
            return None
        except FetchDisallowed:
            html = None
        except Exception as exc:
            # One bad result page never fails the fetch: keep Brave's snippet instead.
            log.info("brave.page_failed", url=url, error=type(exc).__name__)
            html = None
        if html:
            text = _strip_html(html).decode("utf-8", errors="replace")
            return _make(url, f"{title}\n\n{text}", query, "full")
        extras = "\n".join(str(s) for s in res.get("extra_snippets") or [])
        snippet = f"{title}\n\n{res.get('description') or ''}\n\n{extras}".strip()
        return _make(url, snippet, query, "snippet")

    async def _default_search(self, q: str, params: dict[str, str]) -> dict[str, Any]:
        headers = {"Accept": "application/json", "X-Subscription-Token": self._api_key or ""}
        try:
            async with aiohttp.ClientSession(timeout=_SEARCH_TIMEOUT) as session:
                async with session.get(BRAVE_URL, params={"q": q, **params}, headers=headers) as r:
                    if r.status in (401, 403):
                        raise SearchNotConfigured(NOT_SET_UP)
                    if r.status >= 400:
                        raise BraveError(f"Brave Search answered {r.status}")
                    data: dict[str, Any] = await r.json(content_type=None)
                    return data
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise BraveError(str(exc)) from exc

    def _fake(self, query: str | None) -> list[SourcePayload]:
        base = f"https://fake.example.com/{self.kind}"
        return [
            _make(
                f"{base}/1",
                f"Fake {self.kind} 1\n\nFake tender notice: a hotel group awards "
                "its fit-out contract.",
                query,
                "full",
            ),
            _make(
                f"{base}/2", f"Fake {self.kind} 2\n\nAn unrelated announcement.", query, "snippet"
            ),
        ]


def _make(url: str, text: str, query: str | None, kind: str) -> SourcePayload:
    raw = text.encode("utf-8")
    return SourcePayload(
        url=url,
        fetched_at=datetime.now(tz=UTC),
        content_hash=_content_hash(url.encode("utf-8") + b"\n" + raw),
        payload_ref=None,
        language="en",
        raw=raw,
        query=query,
        content_kind="full" if kind == "full" else "snippet",
    )


async def _raw_get(url: str) -> tuple[int, str, str | None, bytes]:
    """One GET without following redirects; status 0 on a network error."""
    try:
        async with aiohttp.ClientSession(timeout=_PAGE_TIMEOUT) as session:
            async with session.get(url, allow_redirects=False) as resp:
                location = resp.headers.get("Location")
                if resp.status in _REDIRECT_STATUSES:
                    return resp.status, resp.content_type, location, b""
                body = await resp.content.read(_MAX_PAGE_BYTES + 1)
                return resp.status, resp.content_type, location, body
    except (aiohttp.ClientError, TimeoutError, UnicodeError, ValueError):
        return 0, "", None, b""


async def fetch_page_guarded(
    url: str, guard: FetchGuard, *, get: RawGet | None = None
) -> bytes | None:
    """The page body when it is a 2xx HTML response under 2 MB; otherwise None.

    Redirects are followed by hand (at most 3) and every hop is checked by the guard, so a
    result that redirects to a private address raises FetchBlocked like a direct one.
    """
    getter = get or _raw_get
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        await guard.check(current)
        status, content_type, location, body = await getter(current)
        if status in _REDIRECT_STATUSES and location:
            current = urljoin(current, location)
            continue
        if 200 <= status < 300 and "html" in content_type and len(body) <= _MAX_PAGE_BYTES:
            return body
        return None
    return None


async def targeted_search(
    connector: BraveSearchConnector, name: str, terms: list[str], guard: FetchGuard
) -> list[SourcePayload]:
    """One narrow search for an entity whose verdict is Unverified (M2/M5): 5 results."""
    query = f'"{name}" ' + " ".join(terms[:3])
    payloads, _ = await connector.fetch({}, [query.strip()], {}, guard, 5)
    return payloads


class PgSearchMeter:
    """S6 metering for Brave calls over app.reserve_budget / app.settle_reservation."""

    def __init__(self, pool: AsyncConnectionPool, ctx: CallContext) -> None:
        self._pool = pool
        self._ctx = ctx
        self._price: Decimal | None = None

    async def _per_call(self) -> Decimal:
        if self._price is None:
            async with self._pool.connection() as conn:
                cur = await conn.execute(
                    "select usd_per_million from public.model_price"
                    " where model = %s and unit = 'fetch'",
                    (PRICE_MODEL,),
                )
                row = await cur.fetchone()
            self._price = Decimal(str(row[0])) / Decimal(1_000_000) if row else _FALLBACK_PRICE
        return self._price

    async def reserve(self, n_calls: int) -> object | None:
        if n_calls <= 0:
            return None
        ctx = self._ctx
        estimate = await self._per_call() * n_calls
        approval = ctx.job_args.get("approval_id")
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "select app.reserve_budget(%s, %s, %s::numeric, %s, %s::jsonb, %s)",
                (
                    str(ctx.workspace_id),
                    str(ctx.brief_id) if ctx.brief_id else None,
                    str(estimate),
                    ctx.job_type,
                    json.dumps(ctx.job_args, default=str),
                    str(approval) if approval else None,
                ),
            )
            row = await cur.fetchone()
        result: dict[str, Any] = row[0] if row else {}
        if not result.get("ok"):
            raise BudgetPaused(
                UUID(str(result["approval_id"])),
                str(result.get("period")),
                Decimal(str(result.get("cap", 0))),
                Decimal(str(result.get("spent", 0))),
            )
        return UUID(str(result["reservation_id"]))

    async def settle(self, token: object | None, calls: int) -> None:
        if token is None:
            return
        actual: dict[str, Any] | None = None
        if calls > 0:
            cost = await self._per_call() * calls
            actual = {
                "capability": "search",
                "provider": "brave",
                "model": PRICE_MODEL,
                "latency_ms": 0,
                "status": "ok",
                "job_id": self._ctx.job_id,
                "lines": [{"unit": "fetch", "quantity": calls, "cost_usd": str(cost)}],
            }
        try:
            async with self._pool.connection() as conn:
                await conn.execute(
                    "select app.settle_reservation(%s, %s::jsonb)",
                    (str(token), json.dumps(actual) if actual is not None else None),
                )
        except Exception as exc:
            log.warning("brave.settle_failed", reservation_id=str(token), error=str(exc))
