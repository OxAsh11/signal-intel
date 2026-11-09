"""Tests for connector implementations.

Tasks 4 and 5 will ADD further tests to this file.
"""

import io
import csv
import pytest
from signal_core.connectors.csv_upload import CsvConnector
from signal_core.connectors.base import FetchGuard


@pytest.mark.asyncio
async def test_csv_produces_one_payload_per_data_row(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"], ["Beta Inc", "beta.io"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, cursor = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert len(payloads) == 2
    assert payloads[0].url.startswith("csv:")
    assert payloads[0].content_hash != payloads[1].content_hash


@pytest.mark.asyncio
async def test_csv_header_only_returns_zero_payloads(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("name,domain\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    assert payloads == []


@pytest.mark.asyncio
async def test_csv_deduplicates_identical_rows(tmp_path):
    path = tmp_path / "dupe.csv"
    path.write_text("name,domain\nAcme,acme.com\nAcme,acme.com\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    hashes = [p.content_hash for p in payloads]
    assert len(set(hashes)) == len(hashes)  # all unique hashes (deduped at upload layer)


@pytest.mark.asyncio
async def test_csv_cursor_is_always_empty_dict(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    _, cursor = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert cursor == {}


@pytest.mark.asyncio
async def test_csv_source_type():
    conn = CsvConnector()
    assert conn.source_type == "csv_upload"
