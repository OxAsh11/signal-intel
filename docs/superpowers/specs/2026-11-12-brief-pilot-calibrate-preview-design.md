# Brief Studio: pilot, calibrate, edit preview — design

Date: 2026-11-12. Approved in chat the same day. PRD: M1 steps 3–5, edit tiers, acceptance criteria; Part 10 risk "weak public footprint"; S6 cost previews.

## Goal

A Brief moves Draft → Pilot → Active. The pilot runs on a small sample, the user marks verdicts right or wrong, and no edit of a published Brief applies without a preview of its effect, cost and duration.

## Decisions

- Pilot sample = `workspace_settings.pilot_sample_size` (default 20) accounts per Brief version.
- Calibration accuracy target = new `workspace_settings.pilot_accuracy_target_pct` (default 80). Below target, Go live warns but does not block.
- Weak-footprint warning when fewer than 20% of the sample's verdicts are Confirmed (`pilot_min_confirmed_pct`, default 20).
- Pilot Briefs never activate (no pushes to destinations).

## 1. States and versions (database)

- `app.start_pilot(brief)`: Draft only, same required-field checks as `publish_brief`, freezes version 1 (`edit_tier` null), sets state `Pilot`, emits `brief.version_published`.
- `app.go_live(brief)`: Pilot → Active. Refused while the Brief has unpublished draft changes.
- `publish_brief` stays the path for new versions; it now also accepts `Pilot`. For a Brief that already has a version it requires a `brief_preview` for the exact current draft (`draft_hash = md5(draft_config::text)`, status `ready`). If the preview is over budget it also requires an approved `budget_approval` for that preview. It stores the preview's tier on the version, and copies the previous version's verdicts to the new one unless the preview says re-judging is needed.
- `update_brief_state` gains Pilot → Archived.
- The Draft publish path in the UI becomes "Start pilot". `publish_brief` on a Draft still works (existing callers and tests), but the UI no longer offers it.

## 2. Pilot sample

- New table `pilot_sample(brief_version_id, entity_id, slot, created_at)`, unique `(brief_version_id, entity_id)` and `(brief_version_id, slot)`.
- `app.claim_pilot_slot(version, entity) returns boolean`: takes a transaction advisory lock on the version, returns true if the entity already holds a slot or one is free (and takes it), else false.
- `ActiveBrief` gains `state`. In M5 `_verdict_for_brief` and M8 `_score_for_brief`, a Pilot Brief skips entities without a slot (M5 claims the slot; M8 only checks it). Research, enrichment and cards follow verdicts and scores, so they stay inside the sample.
- M9 activation treats a Pilot Brief as having no published version.

## 3. Re-derivation after publish

`brief.version_published` also queues `brief.rederive(brief_version_id)`. It walks the workspace's accounts: when the version carries copied verdicts it queues `score.compute`; otherwise `verdict.compute` (which then scores). A Pilot version stops once its sample is full. This also gives a brand-new pilot accounts the workspace already knows.

## 4. Calibrate (web, /briefs/[id]/pilot)

- Lists the sample: account name, verdict, band, priority, evidence quotes, current mark.
- Right / Wrong(+correct value) per row → `public.mark_pilot_verdict(verdict_id, action, value, add_as_example)`. It queues a `verdict_review` with new reason `pilot` and resolves it through `resolve_verdict_review`, so feedback, human hold and audit behave like the review queue. With `add_as_example` it appends the account name to the draft's positive (target) or negative (not a target) examples.
- Header: marked / sample size, accuracy = confirmed ÷ marked against the target, Confirmed share with the weak-footprint warning, and Go live.

## 5. Edit preview

- Tier from the fields that differ between the draft and the current version (highest wins):
  - Narrow: `icp`, `personas`, `scoring`, `budget`.
  - Redefine: `examples`, `predicates`, `evidence_rules`, `offer`, `research_template`.
  - Expand: `hypothesis`, `sources`, `seeds`, `keywords`.
  - Re-judging is needed when `hypothesis`, `examples` or `evidence_rules` change.
- `public.request_brief_preview(brief)`: Editor+, inserts `brief_preview(status 'pending', draft_hash, draft_config)` and emits new event `brief.preview_requested`. The worker job `brief.preview` fills it:
  - Narrow part, exact: the pure scoring function runs with the candidate config over each account's stored claims, verdict and contacts; result rows list accounts entering or leaving (Excluded ↔ not) and band changes.
  - Redefine part, estimate: verdicts to re-judge × judge-call cost, plus source records to re-extract when predicates are added × extraction cost. Duration at 30 calls/minute.
  - Expand part, estimate: new radar types × 10 queries × 20 records × (relevance + extraction cost). Duration in hours.
  - Per-call costs come from `model_price` with fixed token assumptions (judge 2,000 in / 300 out; extract 3,000 / 500; relevance 1,000 / 50 on the prejudge model).
  - `over_budget` when the estimate exceeds the remaining day or month cap for the Brief or workspace.
- ICP in scoring: an account whose claims for `vertical`, `geography` or `size` all fall outside the Brief's ICP lists gets gate `outside_icp` → Excluded. Unknown values never exclude. Live scoring and the preview use the same function.
- UI: on a Pilot/Active/Paused Brief with draft changes, "Preview changes" replaces "Publish". The preview panel shows tier, entering/leaving, band changes, cost and duration; "Publish version N" or, when over budget, "Request approval" (`budget_approval` with job type `brief.publish`; the worker's resumed `brief.publish` task publishes once it is approved).

## 6. Data and contract changes

One migration: `pilot_sample`, `brief_preview` (+RLS read for Viewer), settings columns, verdict_review reason `pilot`, event type `brief.preview_requested` (also in `enums.json` + codegen), the functions above, kill-switch rows for `brief.preview`, `brief.rederive`, `brief.publish`.

## 7. Testing

- pgTAP suite 19: start_pilot/go_live transitions and role checks; slot cap at N under concurrency-free calls; publish refused without a matching preview, refused over budget without approval; verdict copy on Narrow; mark_pilot_verdict writes feedback and examples; RLS on new tables.
- pytest: the pure scoring function returns the same results as before on existing fixtures; ICP gate; tier classifier; estimator arithmetic; Pilot skip in M5/M8; activation ignores Pilot.
- `pnpm db:check`, the PGlite pipeline e2e, typecheck, lint, build. Verified against the real schema before any claim of done.

## Out of scope

Comparing two arbitrary versions already exists (versions page). Shadow verdict re-judging in the preview (costs AI) — Redefine shows counts and cost only. Adding a column by typing a question (separate M1 item).
