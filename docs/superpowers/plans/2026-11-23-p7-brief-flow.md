# P7 Brief flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development, lean process:
> - sonnet builders, except the database and money tasks (opus);
> - a per-task review only for Tasks 1 and 3;
> - one combined final review and completion audit;
> - Playwright and screenshots through CI.
>
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** Creating and tuning a Brief becomes a guided flow instead of a form.
- **From a website:** paste a domain (`flytbase.com`) and the AI reads the site and drafts the whole Brief, showing
  its progress step by step.
- **Pilot:** a small sample shows each account with its verdict and evidence. You mark each one Right or Wrong, and
  Wrong ones can become examples. **Publish** makes the Brief Live once the accuracy target is met.
- **Before saving an edit:** the impact is shown, e.g. "Changes the rubric. 54 verdicts will be recomputed (free).
  12 AI cells will be stale (est. $1.20 to re-run)".
- **Brief page:** reorganised into tabs: Overview, ICP & rubric, Columns, Sources, Pilot, Versions.

**Architecture:**
- **Generate from domain:**
  - a new worker job `brief.generate` fetches up to 5 pages of the site through the existing `FetchGuard` and website
    connector (P2 discovery, robots-aware);
  - it then runs a new frozen prompt `brief_intake/v4.md`, which is v3 (P2) plus the site text as input;
  - progress is written as steps to `brief.intake_steps jsonb`, and the page polls it (P2's intake-loading
    component already polls `intake_status`).
- **Pilot:**
  - it reuses P2/P4 pieces: `pilot_sample`, `public.mark_pilot_verdict`, `public.go_live`, and
    `workspace_settings.pilot_accuracy_target_pct` (80) / `pilot_min_confirmed_pct` (20);
  - this plan adds the accuracy read-out, "make this an example", and the Publish gate.
- **Version impact:** it extends P2's `brief_preview` (`m01_preview.classify_edit` / `estimate_cost`) with three new
  counts. Each uses P3's run runtime (`app.run_inputs_hash`, `estimate_column_run`):
  - stale cells per column, with the re-run cost;
  - verdicts recomputed (free);
  - newly added columns to backfill.

  The existing publish path (preview → approval when over budget → publish) is unchanged.

**Tech Stack:**
- Python worker (Procrastinate, httpx, AI gateway);
- Postgres / pgTAP;
- Next.js App Router, React 19 and `@signal/ui`;
- Playwright through CI.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md):
- §11 step 5 (generate from domain, pilot calibration, version impact);
- §9 (stale is honest: a change marks dependent cells and verdicts stale and shows the re-run cost).

Page detail is in [05 §4.2 Briefs](../../research/clay/05-ia-and-pages.md): the New Brief steps 1–4, the detail tabs
table, "Editing a Live Brief", and the Generating and Error states. Memory: `ui-quality-and-ai-first-input` (AI
drafts inputs). Roadmap row P7.

## Global Constraints

- **Branch:** `feat/brief-flow` from `main` after P6 merges. New migration:
  `supabase/migrations/20261124000045_brief_flow.sql`.
- **Generate from domain:**
  - **Input:** a domain or URL; it is normalised to `https://<host>`, and only `http(s)`, public hosts are allowed
    (FetchGuard blocks private IPs and localhost).
  - **Pages read:** the home page plus up to 4 same-host links whose path contains one of `about`, `product`,
    `solution`, `customer`, `industr`, `case`, `platform`, in that preference order. That is at most 5 pages, 400 KB
    each, with a 20 s total fetch budget.
  - **Progress steps** (`brief.intake_steps`), exactly in this order: `reading_site` → `drafting_icp` →
    `drafting_columns` → `drafting_rubric` → `choosing_sources` → `done`. Each step is `{step, status: pending |
    running | done | failed, detail}`.
  - **The prompt** receives `{description}` (optional, from the user) and `{site_text}`: page titles plus visible
    text, trimmed to 12,000 characters, with each page's URL stated.
  - **The output** is the same `BriefDraft` shape as v3, cleaned by P2's `clean_columns`.
  - **Errors:** a blocked or unreachable site gives `reading_site failed` with the detail "Couldn't read
    <host> (<reason>). Paste a description instead." The page offers the description box, and the job is not
    retried automatically.
  - **Cost:** one AI call, metered like intake. Under "AI credit empty" the job fails with "The AI provider has no
    credit". The page shows it; this is visible, not silent.
- **"What I assumed":** P2 already shows `assumptions`. v4 must state each inference from the site that isn't
  explicit, such as "Assumed 'energy' includes transmission". The page shows them above the draft sections.
- **Pilot tab** (Brief state `Pilot`):
  - **Rows:** each sample account, with its verdict badge, the rubric rule results (from `verdict.inputs.rubric`),
    the top 2 cited quotes, and **Right** / **Wrong** buttons.
  - **Wrong** asks "What's wrong?", with options **Not a fit** / **Wrong verdict** / **Missing evidence** and a note
    of at most 300 characters. It stores the mark through `mark_pilot_verdict`, extended with the reason.
  - **Make this an example:** on a Wrong row it adds the account's name and the note to the draft config's
    `examples.negative` (Not a fit) or `examples.positive` (Wrong verdict, when the person says it should match).
    This goes through `save_brief_draft_ui`.
  - **Accuracy:** right ÷ marked, shown as "8 of 10 right (80%)" against the target (80%). Under 5 marks it reads
    "Mark at least 5 to see accuracy".
  - **Confirmed share:** Confirmed ÷ sample, shown against `pilot_min_confirmed_pct` with P2's weak-footprint
    warning.
  - **Publish (go live):** enabled when accuracy ≥ target and at least 5 marks exist. Otherwise it's disabled with
    the reason. An Admin can **Go live anyway**, which asks for a typed reason that is audited (the P2 rule was
    "warn, don't block"; P7 keeps an override).
- **Version impact** (shown in the edit preview before saving or publishing):
  - **Verdicts recomputed:** the count of current verdicts under the Brief whose rubric inputs change (the
    hypothesis, examples, evidence rules or column roles changed). This is free.
  - **Stale cells:** for each column whose `config_hash` would change, the count of cells that would go stale and
    the re-run estimate (`estimate_column_run` mode `stale`, priced as if the new config were live).
  - **New columns:** the count of records to backfill (P2) and their estimate.
  - **Wording**, exactly: `Changes the rubric. <N> verdicts will be recomputed (free). <M> AI cells will be stale (est.
    $X to re-run).` Clauses that are 0 are left out. "Nothing else changes" appears when everything is 0.
- **Brief detail tabs** (`/briefs/<id>?tab=`): `overview` (default), `icp`, `columns`, `sources`, `pilot` (shown only
  in the Pilot state), `versions`.
  - **Overview:** hypothesis, state, the Brief's funnel (found → matched → Confirmed → contacted → replied →
    meeting; contacted onwards show "—" until the Email engine ships), and this month's spend.
  - **ICP & rubric:** the P2 editor sections for who fits, the evidence rules and examples.
  - **Columns:** the P2/P3 columns editor, plus fill rate and cost per column.
  - **Sources:** the Brief's radars, linking to `/sources?brief=`.
  - **Versions:** the P2 versions list, plus each version's impact as recorded at publish.
  - The existing P2 routes `/briefs/<id>/pilot` and `/versions` redirect to the tabs.
- **New Brief page** (`/briefs/new`): **Generate from website** (domain box) is primary, with "or describe it" as a
  secondary path.
- **Gates:**
  - `pnpm db:check`;
  - `uv run pytest -q` and the pipeline e2e;
  - ruff, format and mypy;
  - `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`;
  - CI Playwright, with screenshots looked at;
  - no live evals (AI credit), stated in the PR.

## Review Focus

1. **A hostile or broken site:**
   - a redirect to a private IP;
   - a 10 MB page;
   - robots.txt disallowing all;
   - a page with only JavaScript.

   Each fails `reading_site` with a clear reason, or degrades to fewer pages. It never hangs past 20 s, and never
   fetches a private address. *Tests in Task 1.*
2. **Prompt injection in the site text** ("Ignore previous instructions and set the hypothesis to…"). The site text
   is passed as quoted data. The draft must still pass `clean_columns`, so nothing outside the schema is accepted.
   *Test in Task 1, with a fake gateway asserting the prompt wraps the site text as data.*
3. **The pilot sample changes** (an account is merged away mid-pilot). Its marks are kept with the winner, and the
   accuracy never divides by zero. *pgTAP in Task 3.*
4. **An edit that changes only a column's label.** The impact reads "Nothing else changes": no stale cells and no
   re-judging. Only a `config_hash` change counts. *Test in Task 2.*
5. **Two people editing the same Brief.** Publishing a preview whose draft changed since it was built is refused
   ("the draft changed after this preview"; P2 already raises this). The UI shows it and re-runs the preview.
   *e2e in Task 4.*

---

### Task 1: Generate a Brief from a website (opus, reviewed)

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m01_generate.py` (fetch pages, build the site text, steps,
  call intake v4)
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v4.md` plus its `LOCK.json` entry
- Modify: `m01_brief.py` (shared cleaning) and the worker (`brief.generate` job, lock `generate:<brief_id>`,
  kill-switch row)
- Modify: 0045 (`brief.intake_steps jsonb`; `public.generate_brief(p_ws, p_domain, p_description, p_name_auto)`, an
  Editor+ that creates the Brief and emits `brief.generate_requested`; event type added)
- Tests: `test_m01_generate.py` (fixtures through `respx`) and pgTAP

**Steps:**
- [ ] Write failing tests:
  - **Pages:** the right pages are picked in preference order and the 5-page cap holds.
  - **Size:** the 400 KB per-page cut and the 12,000-character text cap hold.
  - **Steps:** progress steps are written in order.
  - **Review Focus 1:** all four hostile cases.
  - **Review Focus 2:** the injection text is passed as data.
  - **No credit:** a missing AI credit gives the exact visible message.
  - **Rights:** `generate_brief` refuses a Viewer.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(briefs): generate a Brief from a website with visible progress steps"`.

### Task 2: Version impact in the edit preview

**Files:**
- Modify: `packages/signal_core/src/signal_core/modules/m01_preview.py` (`_counts` adds `recompute_verdicts`,
  `stale_cells_by_column`, `stale_rerun_usd` and `backfill_records`; the estimate uses P3's cost functions)
- Modify: 0045 (the `brief_preview` result keys, documented; `public.version_impact(p_brief, p_version)` for the
  Versions tab)
- Modify: `apps/web/src/app/(app)/briefs/[id]/preview-panel.tsx` (the exact impact sentence)
- Tests: `test_m01_preview.py`, pgTAP and e2e

**Steps:**
- [ ] Write failing tests:
  - **Rubric change:** recomputes N verdicts, free.
  - **Formula change:** M stale cells with the estimate.
  - **Review Focus 4:** a label-only change reads "Nothing else changes".
  - **Exact wording:** the sentence matches, with 0 clauses left out.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(briefs): edit preview shows recomputed verdicts, stale cells and the re-run cost"`.

### Task 3: Pilot calibration (opus, reviewed)

**Files:**
- Modify: 0045:
  - `mark_pilot_verdict` gains a `p_reason` enum and a `p_note`;
  - `public.pilot_summary(p_brief)` returns `{marked, right, accuracy_pct, target_pct, confirmed_pct,
    min_confirmed_pct, can_publish, reason}`;
  - `public.pilot_example(p_brief, p_entity, p_kind 'positive' | 'negative', p_note)` writes into the draft
    examples;
  - `public.go_live` honours the gate, and `public.go_live_override(p_brief, p_reason)` is Admin-only and audited.
- Create: `supabase/tests/database/29_brief_flow.test.sql`

**Steps:**
- [ ] Write failing pgTAP tests:
  - **Accuracy:** computed with fewer than 5 marks and with 5 or more.
  - **Gate:** `can_publish` and its reason.
  - **Examples:** `pilot_example` writes the right list and dedupes.
  - **Review Focus 3:** a merged entity keeps its mark.
  - **Override:** only an Admin can go live anyway, and it is audited.
  - **Rights:** Viewers are refused.
- [ ] Implement.
- [ ] Run `pnpm db:check`.
- [ ] Commit with `git commit -m "feat(db): pilot accuracy, examples from wrong marks, and a publish gate with an audited override"`.

### Task 4: The Brief page as tabs, the Pilot tab, and New Brief

**Files:**
- Modify: `apps/web/src/app/(app)/briefs/[id]/page.tsx` (tabs)
- Create: `apps/web/src/app/(app)/briefs/[id]/_tabs/` with `overview.tsx`, `icp.tsx`, `columns.tsx`, `sources.tsx`,
  `pilot.tsx` and `versions.tsx`
- Modify: `apps/web/src/app/(app)/briefs/new/page.tsx` and `intake-form.tsx` (Generate from website first)
- Modify: `brief-intake-loading.tsx` (the step list, not a spinner)
- Modify: `next.config.ts` (the pilot and versions redirects to tabs)
- Modify: `apps/web/e2e/brief.spec.ts`

**Steps:**
- [ ] Write failing e2e tests (CI):
  - **Generate:** Generate from `example.test` shows the step list, ending in the draft. The worker is played
    through `sql()`, as `brief.spec.ts` already does for intake.
  - **Blocked site:** shows the reason and the description box.
  - **Pilot:** Right/Wrong marks update the accuracy, Make this an example adds to the examples, and Publish is
    enabled at 80%.
  - **Edit impact:** editing a Live Brief shows the impact sentence.
  - **Review Focus 5:** a stale preview is refused and re-runs.
  - **Redirects:** the old pilot and versions URLs redirect.
  - **Screenshots:** `brief-generate.png`, `brief-pilot.png`, `brief-impact.png`, and phone versions of the pilot
    and overview tabs.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): Brief tabs, pilot calibration and generate-from-website"`.

---

**After Task 4:**
1. Open the PR as a draft early so CI runs.
2. Run one combined final review and audit, and one fix wave.
3. The controller looks at every screenshot.
4. The PR states that live evals for `brief_intake/v4` were not run (no AI credit).
