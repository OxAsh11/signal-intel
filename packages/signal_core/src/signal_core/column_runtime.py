"""The column run executor (P3): fills one cell, for an extract or a formula column.

A run is a row of public.predicate_run. The executor acts on a queued run, or on a run left
running for more than 15 minutes (its worker died): it starts it (app.start_run: running,
started_at, inputs_hash from app.run_inputs_hash), fills the cell and finishes it (app.finish_run:
final status, finished_at and the run's cost from usage_ledger). A redelivered job for a run that
is finished, or running elsewhere right now, does nothing.

A formula's claim and its succeeded status are written in one statement (app.complete_formula_run).
A finish that fails is tried again, then recorded as an error. If even that cannot be written, the
job raises and the run stays running: the job's retry comes after 16 minutes (worker app), when
app.start_run takes a run running for more than 15 minutes back and runs it again. A run is
abandoned when it has been running for more than 15 minutes, or queued for more than 1 hour (its
job was skipped by the kill switch, or gave up before starting it). A request, auto-run or publish
that targets an abandoned cell closes the old run (error 'abandoned') and queues a new one, so a
cell is never blocked for good, even when the worker died and no retry comes.

A formula's claim is dated and weighted by its inputs (app.write_formula_claim: the newest
observed_at and the lowest confidence of the input claims the run used), and it is not evidence:
the verdict and the account card never read it, and scoring leaves it out of corroboration.

  * Run condition: when the column has one, the entity must match it (app.entity_matches, which
    compiles it with app.filter_where); otherwise the run ends condition_not_met.
  * Formula: parse, check the formula reads only its inputs, evaluate over the most recent active
    claim of each input and the system values, then check the result against the column type.
    Null is no_data (app.finish_run retracts the cell's older formula claim); a wrong type is an
    error; any other value is written by app.complete_formula_run (app.write_formula_claim, which
    reuses an active claim with the same value, so a re-run with unchanged inputs writes nothing
    and emits nothing, then the finish, in one transaction).
  * Extract: the P2 backfill (one extract call per extracted record of the entity, at most 20),
    metered against the run; afterwards an active claim means succeeded, none means no_data.

Any exception while filling ends the run in error with its message; the job is not retried for it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

import signal_core.modules.m03_extraction as _m03_extraction
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.formula import JsonScalar, check_result, evaluate, parse, references
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

# Records extracted per entity, newest first; the cost estimate caps the average at the same 20.
MAX_RECORDS_PER_RUN = 20

START_SQL = "select app.start_run(%s)"
CONDITION_SQL = "select app.entity_matches(%s, %s, %s, %s::jsonb)"
INPUTS_SQL = "select key, claim_id, value from app.run_input_values(%s)"
RECORDS_SQL = (
    "select sr.id from public.source_record sr"
    " where sr.workspace_id = %s and sr.extraction_status = 'extracted'"
    " and exists (select 1 from public.mention m"
    " where m.source_record_id = sr.id and m.entity_id = %s)"
    " order by sr.fetched_at desc, sr.id limit %s"
)
ACTIVE_CLAIM_SQL = (
    "select c.id from public.claim c"
    " where c.entity_id = %s and c.predicate_id = %s and c.status = 'active'"
    " order by c.observed_at desc, c.id desc limit 1"
)
FINISH_SQL = "select app.finish_run(%s, %s, %s, %s::jsonb, %s::jsonb, %s)"
COMPLETE_SQL = "select app.complete_formula_run(%s, %s::jsonb, %s::jsonb)"
# Tries at writing a run's outcome before it is recorded as an error instead.
FINISH_ATTEMPTS = 3


class _Outcome:
    def __init__(
        self,
        status: str,
        *,
        claim_id: UUID | None = None,
        inputs: list[str] | None = None,
        not_run_reason: dict[str, Any] | None = None,
        error: str | None = None,
        value: JsonScalar = None,
    ) -> None:
        self.status = status
        self.value = value  # a formula's result to write (status succeeded)
        self.claim_id = claim_id
        self.inputs = inputs
        self.not_run_reason = not_run_reason
        self.error = error


def _first(row: Any) -> Any:
    return row[0] if row else None


async def execute_run(
    pool: AsyncConnectionPool,
    gateway: AiGateway | None,
    run_id: UUID,
    *,
    today: date | None = None,
) -> str:
    """Fill the cell of a queued run and return its final status.

    A run that is not queued (nor abandoned while running) is left alone and its current status
    returned ("missing" when there is no such run)."""
    async with pool.connection() as conn:
        cur = await conn.execute(START_SQL, (run_id,))
        run: dict[str, Any] | None = _first(await cur.fetchone())
    if not run or run.get("started") is not True:
        status = str((run or {}).get("status") or "missing")
        log.info("column_run.skipped", run_id=str(run_id), status=status)
        return status
    if run.get("reclaimed"):
        log.warning("column_run.reclaimed", run_id=str(run_id))

    try:
        outcome = await _fill(pool, gateway, run_id, run, today or datetime.now(UTC).date())
    except Exception as exc:  # the run shows the failure; retrying would repeat it
        log.warning("column_run.error", run_id=str(run_id), error=type(exc).__name__)
        outcome = _Outcome("error", error=str(exc) or type(exc).__name__)

    final = await _finish(pool, run_id, outcome)
    log.info("column_run.finished", run_id=str(run_id), status=final)
    return final


async def _finish(pool: AsyncConnectionPool, run_id: UUID, outcome: _Outcome) -> str:
    """Write the outcome; after FINISH_ATTEMPTS failures, record an error instead. If that fails
    too the exception propagates (the run stays running; see the module docstring).

    SQLSTATE 55000 means the run is no longer running (finished, or closed as abandoned): no
    retry; app.finish_run, a no-op on such a run, returns its status."""
    last: Exception | None = None
    for _ in range(FINISH_ATTEMPTS):
        try:
            return await _write_outcome(pool, run_id, outcome)
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "55000":
                log.info("column_run.already_finished", run_id=str(run_id))
                return await _write_outcome(pool, run_id, _Outcome("error", error=str(exc)))
            last = exc
            log.warning("column_run.finish_failed", run_id=str(run_id), error=type(exc).__name__)
    fallback = _Outcome("error", error=f"the run's result could not be recorded: {last}")
    return await _write_outcome(pool, run_id, fallback)


async def _write_outcome(pool: AsyncConnectionPool, run_id: UUID, outcome: _Outcome) -> str:
    inputs = json.dumps(outcome.inputs) if outcome.inputs is not None else None
    async with pool.connection() as conn:
        if outcome.status == "succeeded" and outcome.value is not None:
            cur = await conn.execute(
                COMPLETE_SQL, (run_id, json.dumps(outcome.value, allow_nan=False), inputs)
            )
        else:
            cur = await conn.execute(
                FINISH_SQL,
                (
                    run_id,
                    outcome.status,
                    outcome.claim_id,
                    inputs,
                    json.dumps(outcome.not_run_reason)
                    if outcome.not_run_reason is not None
                    else None,
                    outcome.error,
                ),
            )
        return str(_first(await cur.fetchone()))


async def _fill(
    pool: AsyncConnectionPool,
    gateway: AiGateway | None,
    run_id: UUID,
    run: dict[str, Any],
    today: date,
) -> _Outcome:
    condition = run.get("run_condition")
    if condition is not None:
        async with pool.connection() as conn:
            cur = await conn.execute(
                CONDITION_SQL,
                (run["workspace_id"], run.get("brief_id"), run["entity_id"], json.dumps(condition)),
            )
            met = _first(await cur.fetchone())
        if not met:
            return _Outcome("condition_not_met", not_run_reason={"condition": condition})
    if run["fill_method"] == "formula":
        return await _fill_formula(pool, run_id, run, today)
    if run["fill_method"] == "extract":
        return await _fill_extract(pool, gateway, run_id, run)
    raise ValueError(f"a {run['fill_method']} column cannot be run yet")


async def _fill_formula(
    pool: AsyncConnectionPool, run_id: UUID, run: dict[str, Any], today: date
) -> _Outcome:
    node = parse(str((run.get("fill_config") or {}).get("formula") or ""))
    inputs = set(run.get("inputs") or [])
    outside = [k for k in references(node) if k not in inputs]
    if outside:
        raise ValueError(f'formula reads "{outside[0]}", which is not one of its inputs')

    values: dict[str, JsonScalar] = {}
    claim_ids: list[str] = []
    async with pool.connection() as conn:
        cur = await conn.execute(INPUTS_SQL, (run_id,))
        rows = await cur.fetchall()
    for key, claim_id, value in rows:
        values[str(key)] = value
        claim_ids.append(str(claim_id))
    claim_ids.sort()
    system = {"name": run.get("name"), "domain": run.get("domain")}
    result = evaluate(node, values, system, today)
    if result is None:
        return _Outcome("no_data", inputs=claim_ids)
    problem = check_result(result, str(run["value_type"]), run.get("allowed_values"))
    if problem is not None:
        return _Outcome("error", inputs=claim_ids, error=problem)
    # Written together with the finish (one transaction), in _write_outcome.
    return _Outcome("succeeded", inputs=claim_ids, value=result)


async def _fill_extract(
    pool: AsyncConnectionPool, gateway: AiGateway | None, run_id: UUID, run: dict[str, Any]
) -> _Outcome:
    if gateway is None:
        raise RuntimeError("AI is not set up")
    entity_id = UUID(str(run["entity_id"]))
    workspace_id = UUID(str(run["workspace_id"]))
    brief_id = run.get("brief_id")
    ctx = CallContext(
        org_id=UUID(str(run["org_id"])),
        workspace_id=workspace_id,
        brief_id=UUID(str(brief_id)) if brief_id else None,
        job_id=None,
        job_type="column.run",
        job_args={"run_id": str(run_id)},
        predicate_run_id=run_id,
    )
    async with pool.connection() as conn:
        cur = await conn.execute(RECORDS_SQL, (workspace_id, entity_id, MAX_RECORDS_PER_RUN))
        records = await cur.fetchall()
    for (record_id,) in records:
        await _m03_extraction.backfill_record(
            pool, gateway, UUID(str(record_id)), [str(run["key"])], ctx
        )
    async with pool.connection() as conn:
        cur = await conn.execute(ACTIVE_CLAIM_SQL, (entity_id, run["predicate_id"]))
        claim_id = _first(await cur.fetchone())
    if claim_id is None:
        return _Outcome("no_data")
    return _Outcome("succeeded", claim_id=claim_id)
