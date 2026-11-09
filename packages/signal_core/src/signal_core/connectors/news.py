"""News connector — GDELT DOC 2.0 API and RSS feeds.

Real mode
---------
* For every query string, hits the GDELT DOC 2.0 ``artlist`` endpoint and
  collects article URLs / titles.
* For every RSS feed URL in ``config.get("rss_feeds", [])``, fetches and parses
  the feed with stdlib ``xml.etree.ElementTree``.

Cursor: ``{"last_datetime": "YYYYMMDDHHMMSS"}`` — only items whose seendate is
strictly after the cursor value are returned; next cursor is the latest seendate
seen in this batch.

Fake mode
---------
When ``config.get("fake")`` is truthy, two deterministic fixture payloads are
returned immediately without any network I/O.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from urllib.parse import quote_plus

import aiohttp

from signal_core.connectors import SourcePayload
from signal_core.connectors.base import content_hash as _content_hash

if TYPE_CHECKING:
    from signal_core.connectors.base import FetchGuard

_GDELT_BASE = "https://api.gdeltproject.org/api/v2/doc/doc"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

# ---------------------------------------------------------------------------
# Deterministic fixture items for fake / test mode
# ---------------------------------------------------------------------------

_FAKE_ITEMS: list[dict] = [
    {
        "url": "https://news.example.com/article-1",
        "title": "Signal Intel Fake News 1",
        "source": "fake",
        "seendate": "20240101120000",
    },
    {
        "url": "https://news.example.com/article-2",
        "title": "Signal Intel Fake News 2",
        "source": "fake",
        "seendate": "20240101130000",
    },
]


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class NewsConnector:
    """Connector that fetches news via GDELT DOC 2.0 API and/or RSS feeds.

    ``config`` may contain:
      ``fake`` — if truthy, return 2 deterministic fixture payloads (no network I/O).
      ``rss_feeds`` — list of RSS/Atom feed URLs to poll in addition to GDELT.
    """

    source_type: str = "news"

    async def fetch(
        self,
        config: dict,
        queries: list[str],
        cursor: dict,
        guard: "FetchGuard",
        limit: int,
    ) -> tuple[list[SourcePayload], dict]:
        if config.get("fake"):
            return _build_payloads(_FAKE_ITEMS), {}

        now = datetime.now(tz=timezone.utc)
        last_datetime: str = cursor.get("last_datetime", "")
        payloads: list[SourcePayload] = []
        max_seen: str = last_datetime

        # --- GDELT per query --------------------------------------------------
        for query in queries:
            if len(payloads) >= limit:
                break
            items = await _fetch_gdelt(query, last_datetime, limit - len(payloads))
            for item in items:
                raw = json.dumps(item, sort_keys=True).encode("utf-8")
                payloads.append(
                    SourcePayload(
                        url=item.get("url", ""),
                        fetched_at=now,
                        content_hash=_content_hash(raw),
                        payload_ref=None,
                        language="en",
                        raw=raw,
                    )
                )
                seen = item.get("seendate", "")
                if seen > max_seen:
                    max_seen = seen

        # --- RSS feeds --------------------------------------------------------
        for feed_url in config.get("rss_feeds", []):
            if len(payloads) >= limit:
                break
            items = await _fetch_rss(feed_url, guard, limit - len(payloads))
            for item in items:
                raw = json.dumps(item, sort_keys=True).encode("utf-8")
                payloads.append(
                    SourcePayload(
                        url=item.get("url", ""),
                        fetched_at=now,
                        content_hash=_content_hash(raw),
                        payload_ref=None,
                        language="en",
                        raw=raw,
                    )
                )

        next_cursor: dict = {"last_datetime": max_seen} if max_seen else {}
        return payloads, next_cursor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_payloads(items: list[dict]) -> list[SourcePayload]:
    """Build SourcePayload list from a list of item dicts (used for fake mode)."""
    now = datetime.now(tz=timezone.utc)
    result: list[SourcePayload] = []
    for item in items:
        raw = json.dumps(item, sort_keys=True).encode("utf-8")
        result.append(
            SourcePayload(
                url=item["url"],
                fetched_at=now,
                content_hash=_content_hash(raw),
                payload_ref=None,
                language="en",
                raw=raw,
            )
        )
    return result


async def _fetch_gdelt(query: str, last_datetime: str, max_records: int) -> list[dict]:
    """Call GDELT DOC 2.0 and return a list of article dicts."""
    params = (
        f"query={quote_plus(query)}"
        f"&mode=artlist"
        f"&maxrecords={min(max_records, 250)}"
        f"&format=json"
    )
    if last_datetime:
        params += f"&startdatetime={last_datetime}"
    url = f"{_GDELT_BASE}?{params}"
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return []
                data = await resp.json(content_type=None)
                return data.get("articles", []) if isinstance(data, dict) else []
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return []


async def _fetch_rss(feed_url: str, guard: "FetchGuard", limit: int) -> list[dict]:
    """Fetch an RSS/Atom feed and return a list of item dicts (up to *limit*)."""
    await guard.check(feed_url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(feed_url) as resp:
                if resp.status >= 400:
                    return []
                xml_text = await resp.text()
    except (aiohttp.ClientError, TimeoutError):
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: list[dict] = []

    # RSS 2.0: <channel><item>…</item></channel>
    for item_el in root.iter("item"):
        if len(items) >= limit:
            break
        items.append(
            {
                "url": item_el.findtext("link") or "",
                "title": item_el.findtext("title") or "",
                "pubDate": item_el.findtext("pubDate") or "",
                "description": item_el.findtext("description") or "",
            }
        )

    # Atom 1.0: <feed><entry>…</entry></feed>
    if not items:
        atom_ns = "http://www.w3.org/2005/Atom"
        for entry_el in root.iter(f"{{{atom_ns}}}entry"):
            if len(items) >= limit:
                break
            link_el = entry_el.find(f"{{{atom_ns}}}link")
            href = link_el.get("href", "") if link_el is not None else ""
            items.append(
                {
                    "url": href,
                    "title": entry_el.findtext(f"{{{atom_ns}}}title") or "",
                }
            )

    return items
