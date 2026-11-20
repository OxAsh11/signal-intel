"""The column run executor (P3) against a fake pool that answers each statement."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest

import signal_core.modules.m03_extraction as m03
from signal_core.column_runtime import (
    ACTIVE_CLAIM_SQL,
    COMPLETE_SQL,
    CONDITION_SQL,
    FINISH_SQL,
    INPUTS_SQL,
    RECORDS_SQL,
    START_SQL,
    execute_run,
)
from signal_core.metering import CallContext

RUN = uuid4()
ORG = uuid4()
WS = uuid4()
ENTITY = uuid4()
PREDICATE = uuid4()
BRIEF = uuid4()
TODAY = date(2026, 11, 19)


def _run(**over: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "started": True,
        "status": "running",
        "org_id": str(ORG),
        "workspace_id": str(WS),
        "entity_id": str(ENTITY),
        "predicate_id": str(PREDICATE),
        "brief_id": str(BRIEF),
        "key": "has_programme_and_sites",
        "fill_method": "formula",
        "fill_config": {"formula": "drone_programme and site_count > 0"},
        "value_type": "boolean",
        "allowed_values": None,
        "run_condition": None,
        "inputs": ["drone_programme", "site_count"],
        "name": "Volt Grid",
        "domain": "voltgrid.example",
    }
    run.update(over)
    return run


class FakeCursor:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    async def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class FakePool:
    """Answers the executor's statements from *answers* (statement -> rows or a callable)."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakePool]:
        yield self

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.calls.append((sql, params))
        answer = self.answers.get(sql, [])
        if callable(answer):
            answer = answer(params)
        if isinstance(answer, BaseException):
            raise answer
        return FakeCursor(answer)

    def sent(self, sql: str) -> list[tuple[Any, ...]]:
        return [p for s, p in self.calls if s == sql]


def _finish(params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    return [(params[1],)]


def _pool(run: dict[str, Any] | None, **answers: Any) -> FakePool:
    base: dict[str, Any] = {
        START_SQL: [(run,)],
        FINISH_SQL: _finish,
        COMPLETE_SQL: [("succeeded",)],
    }
    base.update({globals()[k]: v for k, v in answers.items()})
    return FakePool(base)


def _finished(pool: FakePool) -> dict[str, Any]:
    (params,) = pool.sent(FINISH_SQL)
    run_id, status, claim_id, inputs, reason, error = params
    assert run_id == RUN
    return {
        "status": status,
        "claim_id": claim_id,
        "inputs": json.loads(inputs) if inputs is not None else None,
        "not_run_reason": json.loads(reason) if reason is not None else None,
        "error": error,
    }


async def _execute(pool: FakePool, gateway: Any = None) -> str:
    return await execute_run(pool, gateway, RUN, today=TODAY)  # type: ignore[arg-type]


# ── Formula ──────────────────────────────────────────────────────────────────


async def test_formula_succeeds_and_writes_its_claim() -> None:
    dp, sc = uuid4(), uuid4()
    pool = _pool(
        _run(),
        INPUTS_SQL=[("drone_programme", dp, True), ("site_count", sc, 40)],
    )
    assert await _execute(pool) == "succeeded"
    assert pool.sent(INPUTS_SQL) == [(RUN,)]
    # The claim and the succeeded status are written by one statement (one transaction).
    assert pool.sent(COMPLETE_SQL) == [(RUN, "true", json.dumps(sorted([str(dp), str(sc)])))]
    assert pool.sent(FINISH_SQL) == []


async def test_formula_reads_system_values() -> None:
    pool = _pool(
        _run(
            fill_config={"formula": 'concat(@name, " / ", @domain)'},
            value_type="text",
            inputs=["drone_programme"],
        ),
    )
    assert await _execute(pool) == "succeeded"
    assert [p[1] for p in pool.sent(COMPLETE_SQL)] == [json.dumps("Volt Grid / voltgrid.example")]


async def test_formula_with_every_input_empty_is_no_data_and_writes_nothing() -> None:
    """Review Focus 2: coalesce(work_email, personal_email) with neither present."""
    pool = _pool(
        _run(
            fill_config={"formula": "coalesce(work_email, personal_email)"},
            value_type="text",
            inputs=["personal_email", "work_email"],
        ),
        INPUTS_SQL=[],
    )
    assert await _execute(pool) == "no_data"
    assert pool.sent(COMPLETE_SQL) == []
    assert _finished(pool) == {
        "status": "no_data",
        "claim_id": None,
        "inputs": [],
        "not_run_reason": None,
        "error": None,
    }


async def test_formula_result_of_the_wrong_type_errors_with_the_exact_message() -> None:
    pool = _pool(
        _run(fill_config={"formula": "site_count + 1"}, inputs=["site_count"]),
        INPUTS_SQL=[("site_count", uuid4(), 40)],
    )
    assert await _execute(pool) == "error"
    assert pool.sent(COMPLETE_SQL) == []
    assert _finished(pool)["error"] == "formula returned number, column is boolean"


async def test_formula_reading_a_column_outside_its_inputs_errors() -> None:
    pool = _pool(_run(inputs=["drone_programme"]))
    assert await _execute(pool) == "error"
    assert pool.sent(INPUTS_SQL) == []
    assert pool.sent(COMPLETE_SQL) == []
    assert _finished(pool)["error"] == 'formula reads "site_count", which is not one of its inputs'


async def test_condition_not_met_skips_the_fill() -> None:
    condition = {"field": {"system": "domain"}, "op": "is_not_empty"}
    pool = _pool(_run(run_condition=condition), CONDITION_SQL=[(False,)])
    assert await _execute(pool) == "condition_not_met"
    assert pool.sent(CONDITION_SQL) == [(str(WS), str(BRIEF), str(ENTITY), json.dumps(condition))]
    assert pool.sent(INPUTS_SQL) == []
    assert _finished(pool)["not_run_reason"] == {"condition": condition}


async def test_condition_met_fills_the_cell() -> None:
    condition = {"field": {"system": "domain"}, "op": "is_not_empty"}
    pool = _pool(
        _run(run_condition=condition),
        CONDITION_SQL=[(True,)],
        INPUTS_SQL=[("drone_programme", uuid4(), True), ("site_count", uuid4(), 0)],
    )
    assert await _execute(pool) == "succeeded"
    assert [p[1] for p in pool.sent(COMPLETE_SQL)] == ["false"]


# ── Extract ──────────────────────────────────────────────────────────────────


async def test_extract_backfills_each_record_with_its_key_and_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [uuid4(), uuid4()]
    claim = uuid4()
    seen: list[tuple[UUID, list[str], CallContext]] = []

    async def fake_backfill(
        pool: Any, gateway: Any, record: UUID, keys: list[str], ctx: CallContext
    ) -> int:
        seen.append((record, keys, ctx))
        return 1

    monkeypatch.setattr(m03, "backfill_record", fake_backfill)
    pool = _pool(
        _run(fill_method="extract", key="site_count", fill_config={}, inputs=[]),
        RECORDS_SQL=[(r,) for r in records],
        ACTIVE_CLAIM_SQL=[(claim,)],
    )
    gateway = object()
    assert await _execute(pool, gateway) == "succeeded"
    assert pool.sent(RECORDS_SQL) == [(WS, ENTITY, 20)]
    assert [(r, k) for r, k, _ in seen] == [
        (records[0], ["site_count"]),
        (records[1], ["site_count"]),
    ]
    ctx = seen[0][2]
    assert ctx.predicate_run_id == RUN
    assert (ctx.org_id, ctx.workspace_id, ctx.brief_id) == (ORG, WS, BRIEF)
    assert ctx.job_type == "column.run"
    assert _finished(pool)["claim_id"] == claim


async def test_extract_without_a_claim_afterwards_is_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_backfill(*_: Any) -> int:
        return 0

    monkeypatch.setattr(m03, "backfill_record", fake_backfill)
    pool = _pool(
        _run(fill_method="extract", key="site_count", fill_config={}, inputs=[]),
        RECORDS_SQL=[(uuid4(),)],
        ACTIVE_CLAIM_SQL=[],
    )
    assert await _execute(pool, object()) == "no_data"
    assert _finished(pool)["status"] == "no_data"


# ── Already handled, and failures ────────────────────────────────────────────


@pytest.mark.parametrize("status", ["running", "succeeded", "no_data", "error", "cancelled"])
async def test_a_run_that_is_not_queued_is_left_alone(status: str) -> None:
    """Review Focus 5: a redelivered job for a started or finished run does nothing."""
    pool = _pool({"started": False, "status": status})
    assert await _execute(pool) == status
    assert [s for s, _ in pool.calls] == [START_SQL]


async def test_an_abandoned_running_run_taken_back_by_start_run_is_filled() -> None:
    """start_run hands back a run left running for 15 minutes (reclaimed); the job fills it."""
    pool = _pool(
        _run(reclaimed=True),
        INPUTS_SQL=[("drone_programme", uuid4(), True), ("site_count", uuid4(), 3)],
    )
    assert await _execute(pool) == "succeeded"
    assert len(pool.sent(COMPLETE_SQL)) == 1


# A formula that is null (no_data) while site_count has no value.
_NO_DATA_RUN = _run(
    fill_config={"formula": "site_count + 1"}, value_type="number", inputs=["site_count"]
)


async def test_a_finish_that_fails_once_is_tried_again() -> None:
    attempts: list[int] = []

    def flaky(params: tuple[Any, ...]) -> Any:
        attempts.append(1)
        return RuntimeError("connection reset") if len(attempts) == 1 else [(params[1],)]

    pool = _pool(_NO_DATA_RUN, INPUTS_SQL=[], FINISH_SQL=flaky)
    assert await _execute(pool) == "no_data"
    assert [p[1] for p in pool.sent(FINISH_SQL)] == ["no_data", "no_data"]


async def test_a_formula_result_that_cannot_be_written_ends_the_run_in_error() -> None:
    pool = _pool(
        _run(),
        INPUTS_SQL=[("drone_programme", uuid4(), True), ("site_count", uuid4(), 3)],
        COMPLETE_SQL=RuntimeError("deadlock detected"),
    )
    assert await _execute(pool) == "error"
    assert len(pool.sent(COMPLETE_SQL)) == 3
    assert _finished(pool)["error"] == "the run's result could not be recorded: deadlock detected"


async def test_when_no_outcome_can_be_written_the_job_fails_for_a_retry() -> None:
    """The run stays running; the retried job takes it back after 15 minutes (start_run)."""
    pool = _pool(_NO_DATA_RUN, INPUTS_SQL=[], FINISH_SQL=RuntimeError("database gone"))
    with pytest.raises(RuntimeError, match="database gone"):
        await _execute(pool)
    assert len(pool.sent(FINISH_SQL)) == 4  # three tries, then the error record


async def test_a_missing_run_is_left_alone() -> None:
    pool = _pool({"started": False, "status": None})
    assert await _execute(pool) == "missing"
    assert [s for s, _ in pool.calls] == [START_SQL]


async def test_an_exception_ends_the_run_in_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: Any) -> int:
        raise RuntimeError("provider down")

    monkeypatch.setattr(m03, "backfill_record", boom)
    pool = _pool(
        _run(fill_method="extract", key="site_count", fill_config={}, inputs=[]),
        RECORDS_SQL=[(uuid4(),)],
    )
    assert await _execute(pool, object()) == "error"
    assert _finished(pool)["error"] == "provider down"


async def test_a_database_error_while_filling_ends_the_run_in_error() -> None:
    pool = _pool(_run(), INPUTS_SQL=RuntimeError("connection lost"))
    assert await _execute(pool) == "error"
    assert _finished(pool)["error"] == "connection lost"


async def test_a_formula_syntax_error_ends_the_run_in_error() -> None:
    pool = _pool(_run(fill_config={"formula": "drone_programme and"}))
    assert await _execute(pool) == "error"
    assert "(at character" in _finished(pool)["error"]


def test_statements_are_distinct() -> None:
    names = [
        START_SQL,
        CONDITION_SQL,
        INPUTS_SQL,
        COMPLETE_SQL,
        RECORDS_SQL,
        ACTIVE_CLAIM_SQL,
        FINISH_SQL,
    ]
    assert len(set(names)) == len(names)


async def test_a_run_finished_elsewhere_is_not_retried() -> None:
    """SQLSTATE 55000 (the run is no longer running): no retries; finish_run reports its status."""
    import psycopg.errors
    import structlog.testing

    pool = _pool(
        _run(),
        INPUTS_SQL=[("drone_programme", uuid4(), True), ("site_count", uuid4(), 3)],
        COMPLETE_SQL=psycopg.errors.ObjectNotInPrerequisiteState("run is error"),
        FINISH_SQL=[("error",)],
    )
    with structlog.testing.capture_logs() as logs:
        assert await _execute(pool) == "error"
    assert len(pool.sent(COMPLETE_SQL)) == 1
    assert len(pool.sent(FINISH_SQL)) == 1
    assert not [e for e in logs if e["event"] == "column_run.finish_failed"]
