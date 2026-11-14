"""Pilot Briefs only judge and score the accounts in their sample, and never activate (M1)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

import signal_core.modules.m05_verdict as m05
import signal_core.modules.m08_scoring as m08
import signal_core.modules.m09_activation as m09
from signal_core.briefs import ActiveBrief
from signal_core.metering import CallContext


def _brief(state: str) -> ActiveBrief:
    return ActiveBrief(uuid4(), uuid4(), uuid4(), {"hypothesis": "h"}, {}, state)


def _ctx() -> CallContext:
    return CallContext(uuid4(), uuid4(), None, None, "test", {})


def _pool(fetchone: list[Any], fetchall: list[Any] | None = None) -> tuple[MagicMock, MagicMock]:
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(side_effect=fetchone)
    cur.fetchall = AsyncMock(return_value=fetchall or [])
    conn = MagicMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, cur


async def test_pilot_without_a_slot_writes_no_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot, active = _brief("Pilot"), _brief("Active")
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[pilot, active]))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    slot = AsyncMock(return_value=False)
    monkeypatch.setattr(m05, "holds_pilot_slot", slot)
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)
    pool, _ = _pool([("Acme",), *([None] * 4)])

    result = await m05.compute_verdict(pool, MagicMock(), uuid4(), uuid4(), _ctx())

    assert result == "Unverified"  # from the Active Brief
    assert [c.args[1] for c in upsert.call_args_list] == [active.brief_version_id]
    assert slot.call_args.kwargs["claim"] is True


async def test_pilot_with_a_slot_is_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    pilot = _brief("Pilot")
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[pilot]))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    monkeypatch.setattr(m05, "holds_pilot_slot", AsyncMock(return_value=True))
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)
    pool, _ = _pool([("Acme",), *([None] * 4)])

    assert await m05.compute_verdict(pool, MagicMock(), uuid4(), uuid4(), _ctx()) == "Unverified"
    assert upsert.call_count == 1


async def test_pilot_only_and_no_slot_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[_brief("Pilot")]))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    monkeypatch.setattr(m05, "holds_pilot_slot", AsyncMock(return_value=False))
    monkeypatch.setattr(m05, "upsert_verdict", AsyncMock())
    pool, _ = _pool([("Acme",), *([None] * 4)])

    assert await m05.compute_verdict(pool, MagicMock(), uuid4(), uuid4(), _ctx()) is None


async def test_pilot_score_skips_accounts_outside_the_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slot = AsyncMock(return_value=False)
    monkeypatch.setattr(m08, "holds_pilot_slot", slot)
    upsert = AsyncMock()
    monkeypatch.setattr(m08, "upsert_score", upsert)
    pool, _ = _pool([None, None])

    band = await m08._score_for_brief(pool, _brief("Pilot"), uuid4(), uuid4(), [], 0, _ctx())

    assert band is None
    upsert.assert_not_called()
    assert slot.call_args.kwargs["claim"] is False


async def test_activation_ignores_a_brief_in_pilot() -> None:
    pool, cur = _pool([None])
    with pytest.raises(ValueError):
        await m09.push_campaign_batch(pool, uuid4(), _ctx())
    sql = cur.execute.call_args_list[0].args[0]
    assert "b.state = 'Active'" in sql


# ── Re-derivation after publish ───────────────────────────────────────────────


async def test_rederive_scores_copied_verdicts_and_judges_the_rest() -> None:
    from signal_core.briefs import rederive_plan

    a1, e1, a2, e2, ws, org = (str(uuid4()) for _ in range(6))
    pool, _ = _pool([("Active", 20, 0)], [(a1, e1, ws, org, True), (a2, e2, ws, org, False)])
    version = uuid4()

    plan = await rederive_plan(pool, version)

    judge = {"entity_id": e2, "workspace_id": ws, "org_id": org, "brief_version_id": str(version)}
    assert plan == [
        ("score.compute", {"account_id": a1, "workspace_id": ws, "org_id": org}),
        ("verdict.compute", judge),
    ]


async def test_rederive_pilot_judges_only_the_free_slots() -> None:
    from signal_core.briefs import rederive_plan

    ws, org = str(uuid4()), str(uuid4())
    rows = [(str(uuid4()), str(uuid4()), ws, org, has) for has in (True, False, False)]
    pool, _ = _pool([("Pilot", 2, 1)], rows)

    plan = await rederive_plan(pool, uuid4())

    assert [kind for kind, _ in plan] == ["score.compute", "verdict.compute"]


async def test_verdict_for_one_version_judges_only_that_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edited, other = _brief("Active"), _brief("Active")
    monkeypatch.setattr(m05, "active_brief_versions", AsyncMock(return_value=[other, edited]))
    monkeypatch.setattr(m05, "_defer_account_card", AsyncMock())
    upsert = AsyncMock()
    monkeypatch.setattr(m05, "upsert_verdict", upsert)
    pool, _ = _pool([("Acme",), *([None] * 4)])

    await m05.compute_verdict(
        pool, MagicMock(), uuid4(), uuid4(), _ctx(), only_version=edited.brief_version_id
    )

    assert [c.args[1] for c in upsert.call_args_list] == [edited.brief_version_id]


async def test_rederive_judges_under_the_new_version_only() -> None:
    from signal_core.briefs import rederive_plan

    version = uuid4()
    ws, org = str(uuid4()), str(uuid4())
    pool, _ = _pool([("Active", 20, 0)], [(str(uuid4()), str(uuid4()), ws, org, False)])

    ((kind, kwargs),) = await rederive_plan(pool, version)

    assert kind == "verdict.compute"
    assert kwargs["brief_version_id"] == str(version)
