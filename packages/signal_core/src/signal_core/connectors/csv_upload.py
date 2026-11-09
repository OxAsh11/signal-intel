"""CSV upload connector.

Reads a local CSV file and returns one SourcePayload per unique data row.
The header row is consumed by csv.DictReader and skipped automatically.
Duplicate rows (same content_hash) are silently dropped — deduplication at
the connector level keeps downstream processing idempotent.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from signal_core.connectors import SourcePayload
from signal_core.connectors.base import content_hash as _content_hash

if TYPE_CHECKING:
    from signal_core.connectors.base import FetchGuard


class CsvConnector:
    """Connector that processes a previously-uploaded CSV file.

    ``config`` must contain:
      ``storage_path`` – filesystem path to the CSV file (or Supabase storage path
      resolved to a local temp file by the caller before invoking ``fetch``).
    """

    source_type: str = "csv_upload"

    async def fetch(
        self,
        config: dict,
        queries: list[str],
        cursor: dict,
        guard: "FetchGuard",
        limit: int,
    ) -> tuple[list[SourcePayload], dict]:
        """Read the CSV at ``config["storage_path"]`` and return one payload per unique row.

        The ``guard`` parameter is intentionally not called — CSV is a local file,
        not a network resource, so SSRF and robots.txt checks do not apply.

        Returns ``(payloads, {})`` — cursor is always empty (CSV is fully consumed
        in a single pass, no pagination).
        """
        storage_path: str = config["storage_path"]
        now = datetime.now(tz=timezone.utc)

        payloads: list[SourcePayload] = []
        seen_hashes: set[str] = set()

        with open(storage_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row_index, row in enumerate(reader):
                # Canonical bytes: JSON-encode with sorted keys so field order
                # in the CSV does not affect the hash.
                raw_bytes: bytes = json.dumps(row, sort_keys=True).encode("utf-8")
                chash = _content_hash(raw_bytes)

                if chash in seen_hashes:
                    # Duplicate row — skip to avoid re-processing.
                    continue
                seen_hashes.add(chash)

                if len(payloads) >= limit:
                    break

                url = f"csv:{storage_path}:{row_index}"
                payloads.append(
                    SourcePayload(
                        url=url,
                        fetched_at=now,
                        content_hash=chash,
                        payload_ref=None,
                        language="en",
                        raw=raw_bytes,
                    )
                )

        return payloads, {}
