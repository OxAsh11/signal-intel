"""Connector framework public API: SourcePayload dataclass and Connector protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from signal_core.connectors.base import FetchGuard


@dataclass
class SourcePayload:
    """A single fetched piece of content returned by a Connector."""

    url: str
    fetched_at: datetime
    content_hash: str
    payload_ref: str | None
    language: str
    raw: bytes


class Connector(Protocol):
    """Protocol that every connector plugin must implement."""

    source_type: str

    async def fetch(
        self,
        config: dict,
        queries: list[str],
        cursor: dict,
        guard: "FetchGuard",
        limit: int,
    ) -> tuple[list[SourcePayload], dict]:
        """Fetch content and return (payloads, next_cursor)."""
        ...
