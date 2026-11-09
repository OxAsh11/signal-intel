# Task 5 Report: M3 backfill job

**Status:** DONE  
**Commits:** e4d9302 (initial), fix commit (see below)

## What was built

### `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Added module-level `extract_relevance_judge_job: Any = None` handle (wired by worker/app.py at startup, same pattern as m04_identity)
- Added `extract_backfill(pool, brief_id, workspace_id) -> int`:
  - `SELECT id FROM public.source_record WHERE workspace_id = %s AND extraction_status = 'pending' ORDER BY created_at LIMIT 500`
  - Defers each row to `extract_relevance_judge_job.defer_async(source_record_id=...)`
  - Returns count of records queued

### `services/worker/src/worker/app.py`
- Added `import signal_core.modules.m03_extraction as _m03_extraction`
- Added `extract_backfill_job` (type="extract.backfill", queue="extract", retry=1)
- Wired `_m03_extraction.extract_relevance_judge_job = extract_relevance_judge_job` after job definition
- In `brief.version_published` dispatch handler:
  - Queries `public.brief` for `workspace_id` (payload only has `{brief_id, version}`, no workspace_id)
  - Dispatches `extract.backfill` with `queueing_lock=f"backfill:{brief_id}:{version}"` alongside `radar.generate_queries`

### Tests
- `packages/signal_core/tests/test_m03_extraction.py`: `test_extract_backfill_queues_pending_records` — verifies count=3, correct ids deferred
- `services/worker/tests/test_worker.py`: `test_dispatch_brief_version_published_queues_backfill` — verifies workspace_id read from DB not payload, correct queueing_lock, backfill job deferred

## Fix round 1 changes
- **Fix 1 (CRITICAL):** workspace_id now queried from `public.brief` table instead of `payload.get("workspace_id")` which was always None
- **Fix 2 (MAJOR):** queueing_lock now `f"backfill:{brief_id}:{version}"` (version-scoped)
- **Fix 3 (MINOR):** Added `ORDER BY created_at` to backfill SELECT
- **Fix 4:** Added dispatch-path test to `test_worker.py` (not test_m03_extraction.py — it tests the worker dispatch handler, so test_worker.py is the correct location)

## Test results
- signal_core: 9 passed, 0 failed
- worker: 21 passed, 0 failed
