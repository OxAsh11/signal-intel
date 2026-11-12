# Brief pilot, calibrate and edit preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draft → Pilot (20-account sample) → calibrate (right/wrong marks) → Active, and no new version of a published Brief without a preview of tier, account changes, cost and duration.

**Architecture:** State and guards live in SQL (`app.*` SECURITY DEFINER functions, one migration). The worker gains three jobs (`brief.preview`, `brief.rederive`, `brief.publish`) and a pilot-slot check in M5/M8. Scoring maths becomes a pure function shared by live scoring and the preview. The web adds a Pilot tab and a preview panel.

**Tech Stack:** Supabase Postgres + pgTAP, Python 3.13 (signal_core, Procrastinate worker, pytest), Next.js 16 server actions.

**Spec:** `docs/superpowers/specs/2026-11-12-brief-pilot-calibrate-preview-design.md`

## Global Constraints

- Writes only through `app.*`/`public.*` SECURITY DEFINER functions with `set search_path = ''`, role-checked with `app.has_workspace_role`, audited with `app.audit`.
- New migration file: `supabase/migrations/20261112000032_brief_pilot.sql`. Never edit applied migrations.
- Event catalogue change = `packages/contracts/schema/enums.json` + `pnpm codegen` + `alter type public.event_type add value`.
- Defaults: `pilot_sample_size` 20 (exists), `pilot_accuracy_target_pct` 80, `pilot_min_confirmed_pct` 20.
- Token assumptions: judge 2000 in / 300 out (sonnet); extract 3000 / 500 (sonnet); relevance 1000 / 50 (haiku). Expand: 10 queries × 20 records per new radar type. Redefine throughput 30 calls/minute; Expand duration at least 60 minutes.
- Every new job type gets a `job_type_config` row (kill switch).
- Prettier style: printWidth 100, singleQuote, trailingComma all; LF endings.

## Review Focus

1. Concurrent verdict jobs for a Pilot version must never exceed the sample size → advisory lock in `app.claim_pilot_slot`; pgTAP checks slot N+1 is refused.
2. User edits the draft after previewing → publish refused because `draft_hash` no longer matches; pgTAP test.
3. Brief with no budget caps → `over_budget` false, no division by zero; pytest.
4. ICP lists empty, or account has no ICP claims → never Excluded by `outside_icp`; pytest.
5. Marking the same pilot verdict twice → second mark replaces the hold, no crash on `verdict_review_one_pending_uq`; pgTAP test.

---

### Task 1: Database — pilot, preview and calibration functions

**Files:**
- Modify: `packages/contracts/schema/enums.json` (EventType += `brief.preview_requested`), then `pnpm codegen`
- Create: `supabase/migrations/20261112000032_brief_pilot.sql`
- Create: `supabase/tests/database/19_brief_pilot.test.sql`

**Interfaces (Produces):**
- Tables: `pilot_sample(brief_version_id, entity_id, slot int, org_id, workspace_id, created_at)` unique (version, entity) and (version, slot); `brief_preview(id, org_id, workspace_id, brief_id, base_version_id, draft_hash text, draft_config jsonb, status 'pending'|'ready'|'failed', tier edit_tier, rejudge boolean, result jsonb, estimate_usd numeric(12,4), duration_minutes int, over_budget boolean, requested_by, created_at, completed_at)`. RLS read: Viewer.
- `workspace_settings.pilot_accuracy_target_pct int default 80`, `pilot_min_confirmed_pct int default 20`.
- `verdict_review.reason` check adds `'pilot'`.
- `app.start_pilot(p_brief uuid) returns uuid` + `public.start_pilot` wrapper.
- `app.go_live(p_brief uuid) returns void` + public wrapper.
- `app.claim_pilot_slot(p_version uuid, p_entity uuid) returns boolean` (service_role only).
- `public.request_brief_preview(p_brief uuid) returns uuid` (emits `brief.preview_requested` with `{brief_id, preview_id}`).
- `app.complete_brief_preview(p_id, p_tier, p_rejudge, p_result, p_estimate, p_minutes, p_over_budget)`, `app.fail_brief_preview(p_id)` (service_role).
- `app.publish_brief_approved(p_brief uuid, p_preview uuid) returns uuid` (service_role; same checks as publish_brief, approval required).
- `public.request_publish_approval(p_preview uuid) returns uuid` → `budget_approval(job_type 'brief.publish', job_args {brief_id, preview_id})`.
- `app.publish_brief(p_brief, p_change_note)` replaced: accepts Pilot; for a Brief that already has a version requires a ready preview with `draft_hash = md5(draft_config::text)`; over budget requires an approved approval whose `job_args->>'preview_id'` matches; sets `edit_tier`; copies the previous version's verdicts when `rejudge = false`.
- `public.mark_pilot_verdict(p_verdict uuid, p_action text, p_value verdict_value default null, p_add_example boolean default false) returns void`.
- `update_brief_state` adds Pilot → Archived.
- `job_type_config` rows: `brief.preview`, `brief.rederive`, `brief.publish`.

- [ ] **Step 1:** Write suite 19 (fixture pattern from suite 07) asserting: start_pilot Draft→Pilot with version 1; Viewer cannot start_pilot; start_pilot refuses missing examples; with `pilot_sample_size` = 2, claim_pilot_slot is true for two entities, true again for an entity holding a slot, false for a third; publish of a Pilot Brief with draft changes and no preview raises; ready preview but changed draft raises; matching ready preview succeeds and version 2 has `edit_tier`; rejudge=false copies verdicts to version 2; over-budget preview without approval raises; go_live Pilot→Active; go_live refused with a pending draft; mark_pilot_verdict 'correct' writes one feedback row and holds the verdict; a second mark on the same verdict succeeds; add_example appends the entity name to `draft_config.examples.negative`; Beta cannot read Alpha's brief_preview.
- [ ] **Step 2:** `pnpm db:check` → suite 19 fails.
- [ ] **Step 3:** Write the migration and enums change; `pnpm codegen`.
- [ ] **Step 4:** `pnpm db:check` → all suites pass, suite 07 unchanged.
- [ ] **Step 5:** Commit `feat(db): brief pilot, preview and calibration functions`.

### Task 2: Pure scoring with ICP gate

**Files:**
- Create: `packages/signal_core/src/signal_core/scoring_core.py`
- Modify: `packages/signal_core/src/signal_core/modules/m08_scoring.py`
- Test: `packages/signal_core/tests/test_scoring_core.py`

**Interfaces (Produces):**
- `ScoreInputs(claims: list[tuple], verdict: str, valid_contacts: int, excluded_by_list: bool)` — claim columns as m08 `_CLAIMS_SQL`.
- `ScoreResult(fit, evidence, scale, timing, reachability: int, priority: float, band: str, gates: list[dict], explanation: dict, labels: list[tuple[str, str]])`.
- `score_account(config: dict, weights: dict, inputs: ScoreInputs, now: datetime) -> ScoreResult`.
- ICP gate: `vertical→icp.verticals`, `geography→icp.geographies`, `size→icp.size_bands`; list non-empty, account has ≥1 claim for that predicate, none match (case-insensitive) → gate `{"reason": "outside_icp", "predicate": key}`, band `Excluded`. Order: Contradicted, exclusion list, outside_icp, thresholds.

- [ ] **Step 1:** Tests: same priority/band as the old maths for a hand-computed fixture; `outside_icp` for geography "France" vs icp ["Germany"]; no gate when icp empty; no gate without a geography claim; case-insensitive match.
- [ ] **Step 2–4:** Fail, implement (move maths out of `_score_for_brief`, which keeps IO only), pass; full Python suite green.
- [ ] **Step 5:** Commit `refactor(scoring): pure score_account with ICP gate`.

### Task 3: Pilot sample in M5, M8 and activation

**Files:** `briefs.py` (`ActiveBrief.state`), `modules/m05_verdict.py`, `modules/m08_scoring.py`, `modules/m09_activation.py`; test `packages/signal_core/tests/test_pilot_sample.py`.

**Interfaces:**
- Produces `holds_pilot_slot(pool, version_id: UUID, entity_id: UUID, claim: bool) -> bool` in `briefs.py`. M5 `_verdict_for_brief` returns None for a Pilot Brief without a slot (claim=True); `compute_verdict` returns the first non-None. M8 skips (claim=False). M9 uses `CASE WHEN b.state = 'Active' THEN b.current_version_id END`.

- [ ] **Step 1:** Tests (fake pool): Pilot + refused slot → no AI call, no upsert; Active unaffected; activation for a Pilot Brief returns without pushing.
- [ ] **Step 2–4:** Fail, implement, pass.
- [ ] **Step 5:** Commit `feat(derive): cap pilot verdicts and scores at the sample; no activation in pilot`.

### Task 4: Edit tier and estimates

**Files:** create `packages/signal_core/src/signal_core/modules/m01_preview.py`; test `packages/signal_core/tests/test_m01_preview.py`.

**Interfaces (Produces):**
- `classify_edit(old: dict, new: dict) -> tuple[str | None, bool, list[str]]` (tier, rejudge, changed fields).
- `estimate_cost(tier: str | None, prices: dict[tuple[str, str], Decimal], counts: dict[str, int]) -> tuple[Decimal, int]`; counts keys `rejudge_verdicts`, `reextract_records`, `new_radar_types`.
- `async build_preview(pool, preview_id: UUID) -> None`: old vs new `score_account` over the workspace's accounts → `{"entering", "leaving", "band_changes", "totals"}` (lists capped at 200); estimate; over_budget vs `budget` minus `usage_ledger` spend in the period; `app.complete_brief_preview`, or `app.fail_brief_preview` and re-raise.

- [ ] **Step 1:** Tests: `icp` only → ("Narrow", False); `examples` → ("Redefine", True); `predicates` → ("Redefine", False); `sources`+`icp` → ("Expand", False); `hypothesis` → ("Expand", True); identical → (None, False, []); 100 rejudges → $0.70, 4 minutes; 1 new radar type → $2.225, ≥60 minutes; no budget rows → not over budget.
- [ ] **Step 2–4:** Fail, implement, pass.
- [ ] **Step 5:** Commit `feat(briefs): edit tier classifier and cost preview`.

### Task 5: Worker jobs

**Files:** `services/worker/src/worker/app.py`; test `services/worker/tests/test_worker.py`.

- `brief.preview(preview_id)` → `build_preview`; dispatched on `brief.preview_requested` (lock `preview:{id}`).
- `brief.rederive(brief_version_id)`: per account entity, `score.compute` when the version holds a verdict for it, else `verdict.compute`; Pilot: stop deferring verdicts once the sample is full. Dispatched on `brief.version_published` (lock `rederive:{version_id}`).
- `brief.publish(brief_id, preview_id, approval_id=None)` → `app.publish_brief_approved(brief, preview)` (service role; requires the approved approval).

- [ ] **Step 1:** Tests: dispatch defers `brief.preview` and `brief.rederive`; rederive picks score vs verdict per entity.
- [ ] **Step 2–4:** Fail, implement, pass.
- [ ] **Step 5:** Commit `feat(worker): brief preview, rederive and approved publish jobs`.

### Task 6: Web — Start pilot, Calibrate, Preview

**Files:** `app/actions/briefs.ts` (+ `startPilot`, `goLive`, `requestPreview`, `requestPublishApproval`, `markPilotVerdict`); `briefs/[id]/brief-editor.tsx`; `state-controls.tsx` (Pilot: Go live, Archive); `page.tsx`; create `briefs/[id]/pilot/page.tsx`, `pilot/mark-buttons.tsx`, `briefs/[id]/preview-panel.tsx` (polls every 3 s while pending).

Copy: "Start pilot", "Go live", "Preview changes", "Publish version {n}", "Request approval", "Right", "Wrong"; warning "Few Confirmed accounts in the pilot ({pct}%). This industry may leave little public trace."; "Accuracy {acc}% of {marked} marked · target {target}%".

- [ ] **Step 1:** `pnpm --filter web typecheck && pnpm --filter web lint && pnpm --filter web build` green.
- [ ] **Step 2:** Commit `feat(web): pilot, calibrate and edit preview in Brief Studio`.

### Task 7: Verify and ship

- [ ] `pnpm codegen:check && pnpm lint && pnpm typecheck && pnpm test && pnpm db:check && pnpm build`; `pnpm py:check`; `uv run pytest services/worker/tests/test_pipeline_e2e.py`.
- [ ] Push, open PR, CI green, report.
