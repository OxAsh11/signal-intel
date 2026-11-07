"""S1 jobs: every job type has a kill switch, checked before each run.

Kill switches live in `job_type_config.enabled`. A disabled type is skipped and logged, never run.
A job that pauses for budget (BudgetPaused) or lacks an AI key (AiNotConfigured) ends cleanly and is
not retried; every other exception propagates to Procrastinate's retry strategy.
"""

import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol

import procrastinate
from psycopg_pool import AsyncConnectionPool

from signal_core.ai_gateway.errors import AiNotConfigured
from signal_core.logging import get_logger
from signal_core.metering import BudgetPaused

log = get_logger(__name__)

JobFn = Callable[..., Awaitable[Any]]
SKIPPED = {"skipped": True, "reason": "kill_switch"}


class KillSwitch(Protocol):
    async def is_enabled(self, job_type: str) -> bool: ...


class DbKillSwitch:
    """Reads job_type_config with a short cache so a flip takes effect within `ttl` seconds."""

    def __init__(self, pool: AsyncConnectionPool, ttl: float = 10.0) -> None:
        self._pool = pool
        self._ttl = ttl
        self._cache: dict[str, tuple[bool, float]] = {}

    async def is_enabled(self, job_type: str) -> bool:
        hit = self._cache.get(job_type)
        now = time.monotonic()
        if hit and now - hit[1] < self._ttl:
            return hit[0]
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "select enabled from public.job_type_config where job_type = %s", (job_type,)
            )
            row = await cur.fetchone()
        # Unknown job types are disabled: every type must be registered (kill switch before launch).
        enabled = bool(row[0]) if row else False
        self._cache[job_type] = (enabled, now)
        return enabled


def guarded(job_type: str, kill_switch: KillSwitch, fn: JobFn) -> JobFn:
    @wraps(fn)
    async def runner(*args: Any, **kwargs: Any) -> Any:
        if not await kill_switch.is_enabled(job_type):
            log.warning("job.skipped", job_type=job_type, reason="kill_switch")
            return SKIPPED
        started = time.monotonic()
        try:
            return await fn(*args, **kwargs)
        except BudgetPaused as paused:
            # Not a failure: an approval request is open and budget.resume re-queues the job once
            # it is granted. Returning (not raising) means Procrastinate never retries it.
            log.warning("job.paused", job_type=job_type, approval_id=str(paused.approval_id))
            return None
        except AiNotConfigured:
            # A missing key will not appear by retrying; Settings shows "Not set up yet".
            log.warning("job.not_configured", job_type=job_type)
            return None
        finally:
            log.info(
                "job.finished", job_type=job_type, ms=round((time.monotonic() - started) * 1000)
            )

    return runner


def job(
    app: procrastinate.App,
    *,
    type: str,
    kill_switch: KillSwitch,
    queue: str = "default",
    retry: int = 5,
) -> Callable[[JobFn], Any]:
    """Register a job type with Procrastinate behind its kill switch."""

    def decorator(fn: JobFn) -> Any:
        return app.task(
            name=type,
            queue=queue,
            retry=procrastinate.RetryStrategy(max_attempts=retry, exponential_wait=2),
        )(guarded(type, kill_switch, fn))

    return decorator
