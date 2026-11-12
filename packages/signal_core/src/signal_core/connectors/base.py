"""Connector base utilities: FetchGuard, content_hash, save_payload."""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import time
import urllib.robotparser
from urllib.parse import ParseResult, urlparse

import aiohttp

_ROBOTS_TTL = 3600  # 1 hour in seconds
_USER_AGENT = "signal-intel"


class FetchBlocked(Exception):
    """Raised when the target URL resolves to a private / reserved IP address."""


class FetchDisallowed(Exception):
    """Raised when robots.txt disallows the user agent from accessing the path."""


def content_hash(raw: bytes) -> str:
    """Return the SHA-256 hex digest of *raw*."""
    return hashlib.sha256(raw).hexdigest()


def save_payload(raw: bytes, prefix: str) -> str | None:
    """Write *raw* to object storage and return the storage path.

    When the ``STORAGE_URL`` environment variable is not set (e.g. in tests),
    this function is a no-op and returns ``None``.
    """
    storage_url = os.environ.get("STORAGE_URL")
    if not storage_url:
        return None
    # TODO: implement actual S3 / object-storage upload when STORAGE_URL is set.
    raise NotImplementedError("Object-storage upload not yet implemented")  # pragma: no cover


class FetchGuard:
    """Guards outbound fetches against SSRF and robots.txt violations.

    * Private-IP check: resolves the hostname via :func:`socket.getaddrinfo`
      and rejects any address that :attr:`ipaddress.IPv4Address.is_private` /
      :attr:`ipaddress.IPv6Address.is_private` returns ``True`` for.
    * robots.txt check: fetches ``<origin>/robots.txt`` with a 10-second
      timeout, parses it with :mod:`urllib.robotparser`, and raises
      :class:`FetchDisallowed` when the path is disallowed for the
      ``signal-intel`` user agent.  robots.txt results are cached per origin
      for 1 hour.  A 404 or 5xx response is treated as "no rules".
    """

    def __init__(self) -> None:
        # {origin: (parser | None, fetched_at_monotonic)}
        self._robots_cache: dict[str, tuple[urllib.robotparser.RobotFileParser | None, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check(self, url: str) -> None:
        """Check *url* for SSRF risk and robots.txt compliance.

        Raises:
            FetchBlocked: if the hostname resolves to a private/reserved IP.
            FetchDisallowed: if robots.txt disallows access to the path.
        """
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise FetchBlocked(f"Cannot parse hostname from URL: {url!r}")

        self._check_private_ip(host)
        await self._check_robots(url, parsed)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_private_ip(host: str) -> None:
        """Raise FetchBlocked if *host* resolves to a private/reserved address."""
        try:
            results = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise FetchBlocked(f"Cannot resolve host {host!r}: {exc}") from exc

        for _family, _type, _proto, _canonname, sockaddr in results:
            addr_str = sockaddr[0]  # IPv4: (host, port); IPv6: (host, port, flow, scope)
            try:
                ip = ipaddress.ip_address(addr_str)
            except ValueError:
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise FetchBlocked(
                    f"URL resolves to private/reserved address {ip} — SSRF guard blocked"
                )

    async def _check_robots(self, url: str, parsed: ParseResult) -> None:
        """Raise FetchDisallowed if robots.txt blocks the path."""
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path or "/"

        parser = await self._get_robots_parser(origin)
        if parser is None:
            return  # no rules → allow
        if not parser.can_fetch(_USER_AGENT, path):
            raise FetchDisallowed(f"robots.txt at {origin} disallows {_USER_AGENT!r} from {path!r}")

    async def _get_robots_parser(self, origin: str) -> urllib.robotparser.RobotFileParser | None:
        """Return cached (or freshly fetched) robots parser for *origin*."""
        cached = self._robots_cache.get(origin)
        now = time.monotonic()
        if cached is not None:
            parser, fetched_at = cached
            if now - fetched_at < _ROBOTS_TTL:
                return parser

        parser = await self._fetch_robots(origin)
        self._robots_cache[origin] = (parser, now)
        return parser

    @staticmethod
    async def _fetch_robots(
        origin: str,
    ) -> urllib.robotparser.RobotFileParser | None:
        """Fetch and parse robots.txt; return None on 404/5xx (allow all)."""
        robots_url = f"{origin}/robots.txt"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(robots_url) as resp:
                    if resp.status == 404 or resp.status >= 500:
                        return None
                    text = await resp.text()
        except (aiohttp.ClientError, TimeoutError):
            return None  # treat network errors as "no rules"

        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        return parser
