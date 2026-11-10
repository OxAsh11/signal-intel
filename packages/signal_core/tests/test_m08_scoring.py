"""Tests for M8 scoring engine."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from signal_core.modules.m08_scoring import compute_score
from signal_core.metering import CallContext


def _make_ctx():
    return CallContext(uuid4(), uuid4(), None, None, "score.compute", {})


def _make_pool_no_bv():
    """Pool whose first fetchone returns None (no brief_version)."""
    pool = MagicMock()
    conn = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=None)
    cur.fetchall = AsyncMock(return_value=[])
    return pool


def _make_pool_with_data(bv_config=None, verdict="Confirmed", claims=None):
    """
    Pool that returns:
      - bv row (brief_version)
      - account row
      - verdict row
      - claims rows
      - contact_points rows (empty)
    Uses side_effect on fetchone to cycle through the rows.
    """
    bv_id = uuid4()
    org_id = uuid4()
    acct_id = uuid4()

    config = bv_config or {}

    # fetchone call sequence:
    # 1. brief_version row
    # 2. account row
    # 3. verdict row
    bv_row = (str(bv_id), str(org_id), config)
    acct_row = (str(acct_id), str(org_id))
    verdict_row = (verdict,)

    fetchone_side_effects = [bv_row, acct_row, verdict_row, None]  # None for exclusion_list

    pool = MagicMock()
    conn = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(side_effect=fetchone_side_effects)
    cur.fetchall = AsyncMock(return_value=claims or [])
    return pool, bv_id, acct_id


@pytest.mark.asyncio
async def test_compute_score_no_brief_version_returns_none():
    """When no brief_version exists, compute_score returns None."""
    pool = _make_pool_no_bv()
    ctx = _make_ctx()
    result = await compute_score(pool, uuid4(), uuid4(), ctx)
    assert result is None


@pytest.mark.asyncio
async def test_compute_score_unverified_verdict_caps_at_low(monkeypatch):
    """With Unverified verdict and high raw scores, band must be capped at Low."""
    # Claims with high confidence to push fit high
    claim_id = uuid4()
    src_id = uuid4()
    # Fit claims with confidence 100; created_at None
    claims = [
        (claim_id, "vertical", "enterprise", 100, None, src_id),
        (uuid4(), "size", "large", 100, None, None),
    ]
    pool, bv_id, acct_id = _make_pool_with_data(verdict="Unverified", claims=claims)

    upsert_score_mock = AsyncMock(return_value=uuid4())
    upsert_label_mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_score", upsert_score_mock)
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_label", upsert_label_mock)

    ctx = _make_ctx()
    result = await compute_score(pool, uuid4(), uuid4(), ctx)

    assert result == "Low"


@pytest.mark.asyncio
async def test_compute_score_contradicted_verdict_sets_excluded(monkeypatch):
    """Contradicted verdict always produces Excluded band."""
    pool, bv_id, acct_id = _make_pool_with_data(verdict="Contradicted")

    upsert_score_mock = AsyncMock(return_value=uuid4())
    upsert_label_mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_score", upsert_score_mock)
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_label", upsert_label_mock)

    ctx = _make_ctx()
    result = await compute_score(pool, uuid4(), uuid4(), ctx)

    assert result == "Excluded"
    upsert_score_mock.assert_called_once()
    # The band passed to upsert_score should be 'Excluded'
    call_args = upsert_score_mock.call_args[0]
    band_arg_index = 10  # positional: pool, acct_id, bv_id, fit, ev, scale, timing, reach, priority, readiness, band
    assert call_args[band_arg_index] == "Excluded"


@pytest.mark.asyncio
async def test_compute_score_valid_dimensions_returns_band(monkeypatch):
    """With Confirmed verdict and present claims, returns a valid band string."""
    claim_id = uuid4()
    src_id = uuid4()
    claims = [
        (claim_id, "vertical", "enterprise", 80, None, src_id),
        (uuid4(), "geography", "US", 70, None, None),
    ]
    pool, bv_id, acct_id = _make_pool_with_data(verdict="Confirmed", claims=claims)

    upsert_score_mock = AsyncMock(return_value=uuid4())
    upsert_label_mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_score", upsert_score_mock)
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_label", upsert_label_mock)

    ctx = _make_ctx()
    result = await compute_score(pool, uuid4(), uuid4(), ctx)

    assert result in ("High", "Medium", "Low", "Excluded")


@pytest.mark.asyncio
async def test_compute_score_calls_upsert_score_and_upsert_label(monkeypatch):
    """Both upsert_score and upsert_label are called at least once."""
    claim_id = uuid4()
    src_id = uuid4()
    claims = [
        (claim_id, "vertical", "enterprise", 80, None, src_id),
    ]
    pool, bv_id, acct_id = _make_pool_with_data(verdict="Confirmed", claims=claims)

    upsert_score_mock = AsyncMock(return_value=uuid4())
    upsert_label_mock = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_score", upsert_score_mock)
    monkeypatch.setattr("signal_core.modules.m08_scoring.upsert_label", upsert_label_mock)

    ctx = _make_ctx()
    await compute_score(pool, uuid4(), uuid4(), ctx)

    upsert_score_mock.assert_called_once()
    assert upsert_label_mock.call_count >= 1
