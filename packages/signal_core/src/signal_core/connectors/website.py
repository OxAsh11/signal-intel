"""Website connector — fetches pages and returns stripped-text payloads.

Each URL in ``config["urls"]`` is fetched once (15 s timeout, 1 concurrent,
≤ 2 req/s).  HTML tags are stripped via stdlib ``html.parser``; only text
nodes inside ``<body>`` survive.  The *content_hash* is computed from the
original HTML bytes so any page change is detectable even if the visible text
stays the same.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import aiohttp

from signal_core.connectors import SourcePayload
from signal_core.connectors.base import content_hash as _content_hash

if TYPE_CHECKING:
    from signal_core.connectors.base import FetchGuard

_TIMEOUT = aiohttp.ClientTimeout(total=15)
_CONCURRENCY = 1
_RATE_DELAY = 0.5  # 0.5 s between requests → 2 req/s max


# ---------------------------------------------------------------------------
# HTML → plain-text extraction
# ---------------------------------------------------------------------------


class _BodyTextExtractor(HTMLParser):
    """Collect text nodes from within ``<body>``, skipping ``<script>``/``<style>``."""

    def __init__(self) -> None:
        super().__init__()
        self._in_body: bool = False
        self._skip_depth: int = 0
        self._skip_tags: frozenset[str] = frozenset({"script", "style"})
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[Any]) -> None:
        tag = tag.lower()
        if tag == "body":
            self._in_body = True
        if tag in self._skip_tags:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "body":
            self._in_body = False
        if tag in self._skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._in_body and self._skip_depth == 0:
            stripped = data.strip()
            if stripped:
                self.parts.append(stripped)


def _strip_html(html_bytes: bytes) -> bytes:
    """Return plain-text bytes extracted from the HTML ``<body>``."""
    extractor = _BodyTextExtractor()
    extractor.feed(html_bytes.decode("utf-8", errors="replace"))
    return " ".join(extractor.parts).encode("utf-8")


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class WebsiteConnector:
    """Connector that fetches web pages and returns stripped-text payloads.

    ``config`` must contain:
      ``urls`` — list of page URLs to fetch.

    Returns one :class:`~signal_core.connectors.SourcePayload` per successfully
    fetched URL.  Cursor is always ``{"done": True}`` — pages are consumed in a
    single pass.
    """

    source_type: str = "website"

    async def fetch(
        self,
        config: dict[str, Any],
        queries: list[str],
        cursor: dict[str, Any],
        guard: FetchGuard,
        limit: int,
    ) -> tuple[list[SourcePayload], dict[str, Any]]:
        urls: list[str] = config["urls"]
        now = datetime.now(tz=UTC)
        sem = asyncio.Semaphore(_CONCURRENCY)
        payloads: list[SourcePayload] = []

        for url in urls[:limit]:
            await guard.check(url)
            async with sem:
                raw_html = await _fetch_url(url)
                if raw_html is None:
                    continue
                plain_text = _strip_html(raw_html)
                chash = _content_hash(raw_html)  # hash raw HTML to detect page changes
                payloads.append(
                    SourcePayload(
                        url=url,
                        fetched_at=now,
                        content_hash=chash,
                        payload_ref=None,
                        language="en",
                        raw=plain_text,
                    )
                )
            await asyncio.sleep(_RATE_DELAY)

        return payloads, {"done": True}


async def _fetch_url(url: str) -> bytes | None:
    """Fetch *url* and return the response body, or ``None`` on error."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status >= 400:
                    return None
                return await resp.read()
    except (aiohttp.ClientError, TimeoutError):
        return None
