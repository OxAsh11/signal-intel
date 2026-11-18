# P1 Engine integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extraction, identity and verdicts trustworthy, so the cells of the coming operating table hold one
typed, evidenced fact each and verdicts stay current. This fixes engine gaps #5 #6 #4 #2 #3 #12 #13 #14.

**Architecture:**
- **Database (migration 0037):**
  - claims become idempotent and announce themselves (`claim.created`);
  - merges announce themselves (`entity.merged`);
  - `meeting.person_entity_id` gets its foreign key.
- **Worker:**
  - extraction sends typed predicates, validates each claim in Python and writes a record in one transaction;
  - the relevance judge reads the Brief behind the record;
  - identity attaches exact name matches;
  - the event dispatcher recomputes verdicts and radar yield from events.
- **Web:** stops printing the metric definition version.

**Tech Stack:**
- Postgres / Supabase SQL migrations, with pgTAP run on PGlite;
- Python 3.13, psycopg 3 async, Procrastinate, pydantic, pytest;
- Next.js and React, with vitest for `packages/ui`.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md),
§11 step 1. Gap details: [engine test study](../../investigations/2026-11-17-engine-test-study.md) §2–6, 12–14.
Roadmap: [2026-11-18-redesign-roadmap.md](2026-11-18-redesign-roadmap.md).

## Global Constraints

- **Branch:** `feat/engine-integrity` from `main` (at `a5a4b9c` or later). One PR.
- **New migration:** `supabase/migrations/20261118000037_engine_integrity.sql`. Never edit an earlier migration.
- **Prompts:**
  - released prompt files are frozen (`tests/test_ai_boundaries.py::test_released_prompts_unchanged`);
  - new behaviour goes in new versions (`extraction/v2.md`, `relevance_judge/v2.md`), each with its sha256 added to
    `prompts/LOCK.json`.
- **Rejecting invalid values:**
  - invalid AI output is rejected, never coerced (PRD);
  - `"100+"` for a number is dropped, not parsed;
  - one invalid claim drops only itself, never the whole record.
- **Identity thresholds are unchanged:** ≥ 90 attaches, 40–89 goes to Review.
- **Locks:** verdict jobs keep their queueing lock `verdict:<entity_id>:<workspace_id>`.
- **Gates before calling the work done** (memory rule "verify against real schema"):
  - `pnpm db:check`;
  - `node scripts/check-pgtap.mjs`;
  - `uv run pytest` (the whole Python suite);
  - `uv run pytest services/worker/tests/test_pipeline_e2e.py`;
  - `pnpm --filter @signal/ui test`.
- **Live prompt evals** (`ai_gateway/evals.py`) need a paid AI key, which isn't available. Say so in the PR rather
  than claiming they ran.

## Review Focus

1. **Same name, different country.** "Fugro" (NO) must not attach to "Fugro" (IN). Same name with a missing
   country on one side may attach. *Test in Task 4.*
2. **One bad claim in a record.** A wrong-typed value (e.g. `"100+"` for a number) drops that claim with a log
   line. The record still ends `extracted` with its other claims. *Test in Task 2.*
3. **A record with no radar** (CSV row, targeted search). Relevance still gets Brief context from the workspace's
   active Briefs, and with no Brief it falls back to a neutral hypothesis line. *Test in Task 3.*
4. **A replayed event or a retried job.** Delivering the same claim twice leaves one claim and does not emit a
   second `claim.created`. *Test in Task 1.*
5. **A burst of claims for one company.** Ten `claim.created` events for one entity queue one verdict job per
   workspace, not ten. *Test in Task 5.*

---

### Task 1: Migration 0037: idempotent claims, claim and merge events, meeting foreign key

**Files:**
- Create: `supabase/migrations/20261118000037_engine_integrity.sql`
- Create: `supabase/tests/database/21_engine_integrity.test.sql`

**Interfaces:**
- Produces:
  - `app.create_claim(...)`: the signature is unchanged. It returns the existing claim id when an `active` claim
    has the same `(entity_id, predicate_id, source_record_id, value)`, and in that case keeps the higher
    confidence and fills a missing quote. On insert only, it emits
    `app.emit('claim.created', p_org, null, 'claim', v_id, {"entity_id", "predicate_id", "source_record_id"})`.
  - `app.merge_entities(p_winner, p_loser, p_actor)`: unchanged behaviour, plus at the end
    `app.emit('entity.merged', v_org, null, 'entity', p_winner, {"winner", "loser"})`.
  - FK `meeting_person_entity_fk`: `meeting.person_entity_id → entity(id)`.

- [ ] **Step 1: Write the failing pgTAP suite `21_engine_integrity.test.sql`.** Assertions:
  - calling `app.create_claim` twice with identical arguments returns the same id, and
    `count(*) from claim where source_record_id = <sr>` = 1;
  - the second call with confidence 90 (first was 60) leaves `confidence = 90`;
  - `count(*) from outbox where event = 'claim.created' and aggregate_id = <claim>` = 1 after both calls;
  - a different value for the same predicate creates a second claim;
  - `app.merge_entities(w, l, null)` leaves one `outbox` row with `event = 'entity.merged'` and
    `payload->>'loser' = l`;
  - `has_fk('public', 'meeting', 'meeting_person_entity_fk')`.

  Seed org, workspace, entity, predicate, source_record and extractor_version the way `20_discovery_breadth.test.sql` does.
- [ ] **Step 2: Run it and see it fail.** Run: `node scripts/check-pgtap.mjs 21_`. Expected: FAIL (two claims; no
  events; no FK).
- [ ] **Step 3: Write the migration.**
  - `create or replace` both functions. Start from the latest bodies: `create_claim` in `20261108000018`,
    `merge_entities` in `20261109000021`. Keep their grants.
  - Add a supporting index
    `claim_dedupe_idx on claim (entity_id, predicate_id, source_record_id) where status = 'active'`.
  - Before adding the FK, run `update public.meeting set person_entity_id = null where person_entity_id is not null
    and not exists (select 1 from public.entity e where e.id = meeting.person_entity_id)`, with a comment saying
    orphans are cleared, not invented.
- [ ] **Step 4: Run it and see it pass.** Run: `node scripts/check-pgtap.mjs 21_ && pnpm db:check`. Expected: PASS;
  check-sql clean.
- [ ] **Step 5: Commit.**
  `git commit -m "feat(db): idempotent claims, claim.created and entity.merged events, meeting person FK"`

### Task 2: Typed extraction with per-claim validation, one transaction, and source dates

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/extraction/v2.md`
- Modify: `packages/signal_core/src/signal_core/ai_gateway/prompts/LOCK.json`
- Modify: `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Test: `packages/signal_core/tests/test_m03_extraction.py`
- Modify: `services/worker/tests/test_pipeline_e2e.py` (FakeGateway `extraction` answer becomes typed: `True`,
  `"engineering"`, `12`)

**Interfaces:**
- Consumes: `registry.Predicate`, `registry.validate_claim_value(p, value) -> str | None`.
- Produces:
  - `EXTRACTION_PROMPT_VERSION = 2`.
  - `ClaimExtract.value: str | int | float | bool`. There is no JSON-string validator any more.
  - `ExtractionResult.source_date: str | None` (ISO date of the article or posting, else null).
  - `parse_source_date(value: str | None, fetched_at: datetime) -> datetime`: the ISO date at 00:00 UTC. It falls
    back to `fetched_at` when the value is missing, unparseable or in the future.
  - Predicates sent to the prompt as JSON objects
    `{"key", "label", "value_type", "allowed_values", "hint"}`.

- [ ] **Step 1: Write failing tests.**
  - `test_extract_sends_predicate_types`: the rendered `predicates` variable contains `"value_type": "boolean"`
    for a boolean predicate.
  - `test_extract_typed_boolean_written_as_json_true`: AI value `True` → `create_claim` called with `'true'`.
  - `test_extract_invalid_claim_dropped_record_still_extracted`: values `"100+"` for a number predicate and `True`
    for a boolean one. Exactly one `create_claim` call; final status `extracted`; a log event
    `extraction.claim_invalid`.
  - `test_extract_writes_in_one_transaction`: FakeConn records `transaction()` entry. All create_entity /
    create_mention / resolve_mention / create_claim calls happen on that one connection.
  - `test_extract_failure_rolls_back`: `create_claim` raises a DB error. The transaction exits with the exception,
    and status `failed` is written on a separate connection afterwards.
  - `test_parse_source_date`: `"2026-11-02"` → 2026-11-02T00:00Z; `None` → fetched_at;
    `"2027-01-01"` (future) → fetched_at; `"Nov 2026"` → fetched_at.
- [ ] **Step 2: Run them and see them fail.** Run:
  `uv run pytest packages/signal_core/tests/test_m03_extraction.py -v`. Expected: the new tests FAIL.
- [ ] **Step 3: Write `extraction/v2.md`.** Same structure as v1. It changes these things:
  - `value` is a JSON value of the predicate's `value_type`: `true`/`false` for boolean, a number for number
    (never a range or "100+"; omit instead), a string for text / enum (must be one of `allowed_values`) / date
    (`YYYY-MM-DD`);
  - it adds the top-level `source_date`;
  - organisations include government bodies, agencies, municipalities and non-profits.

  Add its sha256 to `LOCK.json`.
- [ ] **Step 4: Change `extract_structured`.**
  - Load `id, key, label, value_type, allowed_values, extraction_hint` for the workspace predicates.
  - After the AI call, validate each claim with `validate_claim_value`. Drop invalid ones (log
    `extraction.claim_invalid` with key and error).
  - Open one `pool.connection()` with `async with conn.transaction():` and do every entity, mention, resolve and
    claim write inside it.
  - `observed_at` = `parse_source_date(extraction.source_date, now)`.
  - Identity dispatch, status `extracted` and index deferral stay after the commit.
  - On an exception, mark `failed` on a fresh connection and re-raise.
- [ ] **Step 5: Update the e2e FakeGateway extraction answer to typed values.** Then run:
  `uv run pytest packages/signal_core/tests/test_m03_extraction.py services/worker/tests/test_pipeline_e2e.py packages/signal_core/tests/test_ai_boundaries.py -v`.
  Expected: PASS.
- [ ] **Step 6: Commit.**
  `git commit -m "fix(extraction): typed claim values, per-claim validation, one transaction, source dates"`

### Task 3: Brief-aware relevance judge

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/relevance_judge/v2.md`
- Modify: `prompts/LOCK.json`, `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Test: `packages/signal_core/tests/test_m03_extraction.py`. Also update the e2e FakeGateway `relevance_judge`
  branch so it asserts a `hypothesis` variable is present.

**Interfaces:**
- Produces:
  - `RELEVANCE_PROMPT_VERSION = 2`; prompt variables `{hypothesis}` and `{content}`.
  - `relevance_hypothesis(pool, source_record_id: UUID, workspace_id: UUID) -> str`:
    - the record's radar's Brief current-version `config.hypothesis` (`source_record.radar_id → radar.brief_id →
      brief.current_version_id`);
    - otherwise the hypotheses of `briefs.active_brief_versions(pool, workspace_id)` joined with `"\n- "`;
    - otherwise the constant `NO_BRIEF_HYPOTHESIS = "Any organisation of commercial interest to the user."`.

- [ ] **Step 1: Write failing tests.**
  - `test_relevance_uses_radar_brief_hypothesis`: FakeConn returns a hypothesis for the radar join. The gateway
    variables include it.
  - `test_relevance_falls_back_to_active_briefs`: no radar row; two active briefs → both hypotheses in the
    variable.
  - `test_relevance_no_brief_uses_neutral_line`: no radar and no active briefs → `NO_BRIEF_HYPOTHESIS`.
- [ ] **Step 2: Run them and see them fail.** Run: `uv run pytest packages/signal_core/tests/test_m03_extraction.py -k relevance -v`.
  Expected: FAIL.
- [ ] **Step 3: Write `relevance_judge/v2.md`.** The question: is this text evidence *for or against the
  hypothesis* about any organisation? Organisations explicitly include government bodies, police, agencies,
  municipalities and non-profits. Output stays `{relevant, reason}`. Add the sha to LOCK.json.
- [ ] **Step 4: Implement `relevance_hypothesis`.** Call it in `judge_relevance` and pass `{"hypothesis": …,
  "content": text}`.
- [ ] **Step 5: Run the tests.** Run:
  `uv run pytest packages/signal_core/tests/test_m03_extraction.py packages/signal_core/tests/test_ai_boundaries.py services/worker/tests/test_pipeline_e2e.py -v`.
  Expected: PASS.
- [ ] **Step 6: Commit.** `git commit -m "fix(relevance): judge records against the Brief's hypothesis"`

### Task 4: Exact name matches attach (repeat sightings)

**Files:**
- Modify: `packages/signal_core/src/signal_core/modules/m04_identity.py` (`_lookup_aliases` and `_create_new`)
- Test: `packages/signal_core/tests/test_m04_identity.py`

**Interfaces:**
- Produces:
  - `normalise_company_name(name: str) -> str`: NFKC, casefold, collapse whitespace, strip surrounding punctuation
    and quotes.
  - `name_alias_value(name: str, country: str | None) -> str`: `"<norm>|<country lower>"`, or `"<norm>"` when
    there's no country. This is the same format `_create_new` already stores, now built through
    `normalise_company_name`.
  - `EXACT_NAME_CONFIDENCE = 95`.

**Rule:**
- Before the fuzzy query, look for aliases of type `name_country` whose name part equals the normalised name. The
  name part is `split_part(value_normalised, '|', 1)`.
- Such a candidate scores 95 when the countries are compatible: equal, or missing on either side.
- A candidate with a different country is not a candidate.
- If exact candidates exist, return them and skip the fuzzy query. Several exact entities go through the existing
  AI tie-break.
- Fuzzy scores stay `min(89, …)`.

- [ ] **Step 1: Write failing tests.**
  - `test_exact_name_same_country_attaches_95`.
  - `test_exact_name_missing_country_attaches_95`: the alias `"acme"` matches the mention `"Acme", "IN"`, and the
    reverse also matches.
  - `test_exact_name_different_country_not_candidate`: alias `"fugro|no"` with mention `"Fugro", "IN"` → no
    95 candidate.
  - `test_normalise_company_name`: `"  ACME  Engineering, "` → `"acme engineering"`.
  - `test_resolve_mention_exact_name_returns_attached`: with the FakeConn alias row, `resolve_mention` returns
    `"attached"` and calls `app.resolve_mention` with confidence 95.
- [ ] **Step 2: Run them and see them fail.** Run: `uv run pytest packages/signal_core/tests/test_m04_identity.py -v`.
  Expected: the new tests FAIL.
- [ ] **Step 3: Implement the functions and rule above.** `_create_new` uses `name_alias_value`.
- [ ] **Step 4: Run the tests.** Run: `uv run pytest packages/signal_core/tests/test_m04_identity.py -v`. Expected:
  PASS.
- [ ] **Step 5: Commit.**
  `git commit -m "fix(identity): exact normalised name in a compatible country attaches at 95"`

### Task 5: Verdicts and yield recompute from events

**Files:**
- Modify: `services/worker/src/worker/app.py` (the dispatcher `_derive`, and the `verdict.changed` branch)
- Test: `services/worker/tests/test_worker.py`, `services/worker/tests/test_pipeline_e2e.py`

**Interfaces:**
- Consumes: the `claim.created` and `entity.merged` events from Task 1. Their outbox rows carry `org_id` and **no
  `workspace_id`**.
- Produces: `async def _recompute_verdicts(entity_id: str, org_id: str) -> int` inside `build_app`. It selects
  `workspace_id` from `public.account where entity_id = %s` and calls `_enqueue("verdict.compute",
  f"verdict:{entity_id}:{ws}", entity_id=…, workspace_id=ws, org_id=org_id)` per row. It returns the count.

**Changes:**
- In `_derive`, handle `claim.created` (entity = `payload["entity_id"]`) and `entity.merged` (entity =
  `payload["winner"]`) **before** the early return that requires `workspace_id`.
- In the `verdict.changed` branch, also `_enqueue("radar.yield_compute", f"yield:{workspace_id}",
  timestamp=<now epoch int>)` so the Discovery funnel matches Today (gap #13). The hourly cron stays as a
  backstop.

- [ ] **Step 1: Write failing tests in `test_worker.py`,** following
  `test_dispatch_brief_version_published_queues_backfill`'s fake pool pattern.
  - `test_dispatch_claim_created_queues_verdict_per_account_workspace`: two account rows → two `verdict.compute`
    jobs.
  - `test_dispatch_entity_merged_uses_winner`.
  - `test_dispatch_claim_burst_one_verdict_job`: ten `claim.created` for one entity → one `verdict.compute` job.
  - `test_dispatch_verdict_changed_queues_yield`.
- [ ] **Step 2: Run them and see them fail.** Run: `uv run pytest services/worker/tests/test_worker.py -k dispatch -v`.
  Expected: FAIL.
- [ ] **Step 3: Implement the dispatcher branches above.**
- [ ] **Step 4: Extend the e2e.** In `test_pipeline_end_to_end`, after the first account reaches its verdict:
  - fetch a second page naming "Acme Engineering", country "NL", **without a domain**;
  - assert no new review item for it;
  - assert the mention resolves to the first entity;
  - assert the entity's claim count for that source grows by the second page's claims only;
  - assert a `verdict.compute` ran after the merge (the verdict's `computed_at` advanced).

  Then run: `uv run pytest services/worker/tests -v`. Expected: PASS.
- [ ] **Step 5: Commit.**
  `git commit -m "feat(worker): claim and merge events recompute verdicts; verdict changes refresh radar yield"`

### Task 6: Metric tiles stop showing the definition version

**Files:**
- Modify: `packages/ui/src/components/actions.tsx` (MetricTile)
- Test: `packages/ui/test/ui.test.tsx`

**Interfaces:** `MetricTile` props are unchanged. `version` now renders only as the anchor's
`title="How this is counted: <version>"`, and the `.si-metric-ver` span is removed.

- [ ] **Step 1: Write a failing test.** `MetricTile with version renders no visible version text`:
  `container.textContent` doesn't contain `"def v1"`, and `a.getAttribute('title')` contains `"def v1"`.
- [ ] **Step 2: Run it and see it fail.** Run: `pnpm --filter @signal/ui test`. Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `pnpm --filter @signal/ui test && pnpm typecheck`. Expected: PASS.
- [ ] **Step 4: Commit.** `git commit -m "fix(ui): metric definition version moves to the tile tooltip"`

### Task 7: Whole-branch gates and PR

- [ ] **Step 1: Run every gate.** Expected: all PASS. Write down any skipped suite and why.

  ```bash
  pnpm db:check && node scripts/check-pgtap.mjs && uv run ruff check . && uv run ruff format --check . \
    && uv run mypy && uv run pytest && pnpm --filter @signal/ui test && pnpm typecheck
  ```
- [ ] **Step 2: Open the PR** `feat/engine-integrity`. In the body:
  - list gaps #2 #3 #4 #5 #6 #12 #13 #14 and how each was tested;
  - note that live prompt evals were not run (no AI credit).
