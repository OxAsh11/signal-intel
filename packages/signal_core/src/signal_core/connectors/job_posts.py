"""Job posts connector — Greenhouse, Lever, Ashby public APIs.

For each board in ``config["boards"]`` (a list of ``{source, token}`` dicts),
the connector hits the board's public read API (no auth required) and returns
one :class:`~signal_core.connectors.SourcePayload` per job posting.

Supported sources
-----------------
* ``greenhouse`` — ``GET https://api.greenhouse.io/v1/boards/{token}/jobs``
* ``lever``      — ``GET https://api.lever.co/v0/postings/{token}``
* ``ashby``      — ``GET https://api.ashbyhq.com/posting-api/job-board/{token}``

Cursor
------
``{"{source}:{token}": "<max_updated_at>", …}`` — one entry per board.
On the next call only postings newer than the cursor value are considered.

Fake mode
---------
When ``config.get("fake")`` is truthy, two deterministic fixture payloads are
returned immediately without any network I/O.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

from signal_core.connectors import SourcePayload
from signal_core.connectors.base import content_hash as _content_hash

if TYPE_CHECKING:
    from signal_core.connectors.base import FetchGuard

_TIMEOUT = aiohttp.ClientTimeout(total=30)

# ---------------------------------------------------------------------------
# Deterministic fixture items for fake / test mode
# ---------------------------------------------------------------------------

_FAKE_JOBS: list[dict[str, Any]] = [
    {
        "url": "https://boards.greenhouse.io/example/jobs/1001",
        "title": "Fake Job 1",
        "company": "FakeCo",
        "source": "greenhouse",
    },
    {
        "url": "https://boards.greenhouse.io/example/jobs/1002",
        "title": "Fake Job 2",
        "company": "FakeCo",
        "source": "greenhouse",
    },
]


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class JobPostConnector:
    """Connector that fetches job postings from Greenhouse, Lever, and Ashby.

    ``config`` may contain:
      ``fake``   — if truthy, return 2 deterministic fixture payloads (no network).
      ``boards`` — list of ``{"source": "greenhouse"|"lever"|"ashby", "token": str}``
                   dicts describing which job boards to poll.
    """

    source_type: str = "job_post"

    async def fetch(
        self,
        config: dict[str, Any],
        queries: list[str],
        cursor: dict[str, Any],
        guard: FetchGuard,
        limit: int,
    ) -> tuple[list[SourcePayload], dict[str, Any]]:
        if config.get("fake"):
            return _build_payloads(_FAKE_JOBS), {}

        now = datetime.now(tz=UTC)
        boards: list[dict[str, Any]] = config.get("boards", [])
        payloads: list[SourcePayload] = []
        new_cursor: dict[str, Any] = {}

        for board in boards:
            if len(payloads) >= limit:
                break
            source: str = board.get("source", "")
            token: str = board.get("token", "")
            board_key = f"{source}:{token}"
            max_updated_at: str = cursor.get(board_key, "")

            jobs, latest = await _fetch_board(
                source, token, max_updated_at, limit - len(payloads), guard
            )
            if latest:
                new_cursor[board_key] = latest

            for job in jobs:
                raw = json.dumps(job, sort_keys=True).encode("utf-8")
                payloads.append(
                    SourcePayload(
                        url=job.get("url", ""),
                        fetched_at=now,
                        content_hash=_content_hash(raw),
                        payload_ref=None,
                        language="en",
                        raw=raw,
                    )
                )

        return payloads, new_cursor


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_payloads(jobs: list[dict[str, Any]]) -> list[SourcePayload]:
    """Build SourcePayload list from a list of job dicts (used for fake mode)."""
    now = datetime.now(tz=UTC)
    result: list[SourcePayload] = []
    for job in jobs:
        raw = json.dumps(job, sort_keys=True).encode("utf-8")
        result.append(
            SourcePayload(
                url=job["url"],
                fetched_at=now,
                content_hash=_content_hash(raw),
                payload_ref=None,
                language="en",
                raw=raw,
            )
        )
    return result


async def _fetch_board(
    source: str, token: str, max_updated_at: str, limit: int, guard: FetchGuard
) -> tuple[list[dict[str, Any]], str]:
    """Dispatch to the right board API and return ``(jobs, latest_updated_at)``."""
    if source == "greenhouse":
        return await _fetch_greenhouse(token, max_updated_at, limit, guard)
    if source == "lever":
        return await _fetch_lever(token, max_updated_at, limit, guard)
    if source == "ashby":
        return await _fetch_ashby(token, max_updated_at, limit, guard)
    return [], ""


async def _fetch_greenhouse(
    token: str, since: str, limit: int, guard: FetchGuard
) -> tuple[list[dict[str, Any]], str]:
    url = f"https://api.greenhouse.io/v1/boards/{token}/jobs"
    await guard.check(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return [], ""
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return [], ""

    jobs_raw: list[dict[str, Any]] = data.get("jobs", []) if isinstance(data, dict) else []
    jobs: list[dict[str, Any]] = []
    latest = ""
    for j in jobs_raw:
        updated_at: str = j.get("updated_at", "")
        if since and updated_at and updated_at <= since:
            continue
        if len(jobs) >= limit:
            break
        if updated_at > latest:
            latest = updated_at
        jobs.append(
            {
                "url": j.get("absolute_url", ""),
                "title": j.get("title", ""),
                "id": j.get("id", ""),
                "updated_at": updated_at,
                "source": "greenhouse",
                "token": token,
            }
        )
    return jobs, latest


async def _fetch_lever(
    company: str, since: str, limit: int, guard: FetchGuard
) -> tuple[list[dict[str, Any]], str]:
    url = f"https://api.lever.co/v0/postings/{company}"
    await guard.check(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return [], ""
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return [], ""

    jobs_raw: list[dict[str, Any]] = data if isinstance(data, list) else []
    jobs: list[dict[str, Any]] = []
    latest = ""
    for j in jobs_raw:
        created_at: str = str(j.get("createdAt", ""))
        if since and created_at and created_at <= since:
            continue
        if len(jobs) >= limit:
            break
        if created_at > latest:
            latest = created_at
        jobs.append(
            {
                "url": j.get("hostedUrl", ""),
                "title": j.get("text", ""),
                "id": j.get("id", ""),
                "created_at": created_at,
                "source": "lever",
                "company": company,
            }
        )
    return jobs, latest


async def _fetch_ashby(
    name: str, since: str, limit: int, guard: FetchGuard
) -> tuple[list[dict[str, Any]], str]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{name}"
    await guard.check(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    return [], ""
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return [], ""

    jobs_raw: list[dict[str, Any]] = data.get("jobPostings", []) if isinstance(data, dict) else []
    jobs: list[dict[str, Any]] = []
    latest = ""
    for j in jobs_raw:
        updated_at: str = j.get("updatedAt", "")
        if since and updated_at and updated_at <= since:
            continue
        if len(jobs) >= limit:
            break
        if updated_at > latest:
            latest = updated_at
        jobs.append(
            {
                "url": j.get("jobUrl", ""),
                "title": j.get("title", ""),
                "id": j.get("id", ""),
                "updated_at": updated_at,
                "source": "ashby",
                "name": name,
            }
        )
    return jobs, latest
