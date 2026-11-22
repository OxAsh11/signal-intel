# P5 Operating table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accounts becomes the operating table.
- **Rows and columns:** one row per account, with system columns and the Brief's columns.
- **Cells:** each cell shows its state and opens a panel with the value, evidence and history.
- **Columns:** added from a catalogue, with cost shown first.
- **Runs:** columns are run from the header or a selection, with the cost and the "first N within budget" option.
- **Views:** filters and views are saved and pinned in the sidebar with live counts.
- **Layouts:** a board grouped by stage or verdict, and a "By country" layout.
- **Export and search:** the view exports to CSV, and a search box finds any account.

**Architecture:**
- **Data:** one SQL RPC builds a page of the table: rows from P3 `view_entities` (filter, sort, lens) and cells from P3 `public.cells`. A second RPC gives one cell's panel.
- **Actions:** manual values, "mark wrong" and pinning are SECURITY DEFINER RPCs in migration 0043.
- **Pages:** the web table lives at `/workspace/<ws>/accounts`, still reached through `/accounts`, with the query forwarded since P4. It is a server-rendered page of rows, with client components for selection, keyboard navigation, the panel and the dialogs.
- **CSS first:** Task 1 fixes the unlayered design-system CSS (P4 ruling R5), so every later task can use Tailwind utilities on `si-*` elements.

**Tech Stack:**
- Next.js App Router and React 19, with `@signal/ui` and `@signal/formula`;
- Postgres / pgTAP on PGlite;
- Playwright through CI.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md):
- §5 rules T1–T9: cell states, the cell panel, Add column, run controls, filters, views, Enter-never-runs, cost before action;
- §3 pinned views;
- §9 failures visible and "stale is honest".

Page detail is in [05-ia-and-pages §3.1–3.6 and §4.4](../../research/clay/05-ia-and-pages.md). Roadmap row P5 is in
[2026-11-18-redesign-roadmap.md](2026-11-18-redesign-roadmap.md). It builds on P3's runtime (`cells`, `view_entities`,
`saved_view`, `estimate_column_run`, `request_column_run`) and P4's shell.

## Global Constraints

- **Branch:** `feat/operating-table` from `feat/shell-home`, so P5 is stacked on P4 (memory: stacked stages).
  - The PR's base is `feat/shell-home`; GitHub retargets it to `main` when P4 merges.
  - New migration: `supabase/migrations/20261121000043_operating_table.sql`. Never edit a released migration (0042 is
    unreleased until P4 merges, but leave it alone).
- **Table URL** (shareable; the URL is the state):
  - `/workspace/<ws>/accounts?brief=<id|all>&view=<preset|saved uuid>&f=<base64url filter AST>&sort=<key:dir,…>&layout=<table|board|country>&group=<stage|verdict>&page=<n>`;
  - `/accounts?…` forwards to it.
  - With no `f`, the view's own filter applies. An `f` given alongside a view **replaces** that view's filter until it
    is saved.
- **Columns:**
  - **System columns, in this order, pinned left:** Account (name + country), Verdict (`VerdictBadge`), Score (priority
    + `BandChip`), Stage (`StageBadge`), Owner, Last signal (date of the newest sourced claim).
  - **Predicate columns:** the lens Brief's columns, in spec order. Under "All Briefs" there are no predicate columns
    by default, but a saved view's `columns` can name any workspace column.
  - **Page size:** 50 rows.
- **Cell states:** the P3 nine states, rendered exactly as 05 §3.1:

  | State | Shows |
  |---|---|
  | `empty` | faint `—` |
  | `queued` | grey dot |
  | `running` | animated bar |
  | `value` | value + H/M/L confidence chip (H ≥ 80, M ≥ 50, else L) |
  | `derived` | value in the `DerivedValue` style |
  | `no_data` | grey "No data" |
  | `skipped` | grey "Skipped" |
  | `error` | lava dot + short reason |
  | `stale` | value + mustard corner mark |

  Values format by type:
  - boolean is "Yes" / "No";
  - number uses `Intl.NumberFormat('en')`;
  - date is `d MMM yyyy`;
  - enum and text are shown as is, truncated to 1 line with a tooltip.
- **Cell panel** (05 §3.2), a right-side drawer with:
  - **Value:** the value, confidence and fill label (`Found in sources` / `Formula` / `Entered by you`).
  - **Evidence:** each active claim's quote, source link (`source_record.url`) and date. A derived cell lists its input
    claims instead. Superseded values are shown struck through, with their date.
  - **History:** runs, newest first, with status, cost, Brief version, started and finished times, and the error.
  - **Actions:**
    - **Re-run** (shows the cost; formula is free; uses `request_column_run` mode `selected`);
    - **Edit** (writes a manual value; the old value stays visible as superseded);
    - **Mark wrong** (retracts that claim and opens a Review item with the reason).

  The panel never runs anything on open.
- **Keyboard (T7):** arrows move the active cell, Space toggles row selection, Enter or click opens the panel, and Esc
  closes it. **Enter never runs anything.**
- **Add column** (05 §3.3, scoped to this plan):
  - **Kinds:** `Found in sources` (extract) and `Formula`. AI research and provider waterfalls are listed but disabled,
    labelled "Arrives in P6".
  - **Needs a Brief:** under "All Briefs" the catalogue says "Choose a Brief in the lens to add a column".
  - **Saving:**
    1. append the spec to the lens Brief's draft config;
    2. run P2's preview;
    3. show its cost and tier;
    4. publish through the existing publish path (an over-budget preview shows the approval message and stops);
    5. then offer the three run choices: **Run on 10**, **Run on all in view**, **Save without running**. Each shows
       its estimate from `estimate_column_run` (mode `empty`); formula is $0.
  - **Live preview:** for a formula, a 5-row preview computed in the browser with `@signal/formula` over the visible
    rows' current values; for extract, "Fills from N records already collected" (the P2 backfill scope count).
- **Run controls (T4, T9):**
  - Each predicate column header has a menu: **Run empty**, **Run stale**, **Run errored**, **Pause auto-run** /
    **Resume auto-run** (formula only).
  - The bulk bar has **Run columns…**, which picks columns and uses mode `selected`.
  - Every run shows `N accounts · est. $X` before you confirm.
  - A 55000 budget refusal shows the server's message and offers **Run the first N** (re-requests with `p_limit = N`).
- **Bulk bar** (shown when rows are selected): Run columns…, Label, Export selected. Add to sequence, Find people and
  Merge are shown disabled with "Arrives with the Email engine" or "Arrives in P6".
- **Filters (T5):** a chip builder over the P3 filter AST.
  - **Field picker:** Verdict, Band, Stage, Label, Name, Domain, Created, any column (by label), and "Run state of …".
  - **Ops:** only those valid for the field (the P3 tables).
  - **Value editor:** suits the type.
  - Chips combine with `all`. An "Any of" group is allowed one level deep.
  - The plain-English box and Ask are **out of scope** (see Rulings in the ledger: no AI credit to evaluate a
    parser).
- **Views (T6):**
  - The view bar shows the five presets, then saved views (`list_views`).
  - **Save view** stores the current filter, columns, sort and layout as personal or workspace (Editor+ for
    workspace).
  - **Pin to sidebar** stores the pin per member; the sidebar lists pinned views under Accounts with live counts
    (the `total` from `view_entities`).
- **Layouts:**
  - **Board:** columns per stage (all stages in enum order) or per verdict, with account cards (name, verdict, band,
    top 2 column values). **Read-only:** stage is derived from activity (M11 `upsert_account_stage`), so moving a
    card waits for the CRM.
  - **By country:** groups by country with counts and the same cards.

  A real map waits for site coordinates (none exist in the schema).
- **Export:** `GET /workspace/<ws>/accounts/export?…` (same query) streams a CSV of the current view:
  - all matching rows, up to 10,000;
  - the columns as shown;
  - header row = the column labels;
  - values formatted as in cells;
  - optional `&evidence=1` adds one "<column> source" URL column per predicate column.

  Viewer+. RFC 4180 quoting. Cells that start with `=`, `+`, `-` or `@` get a leading `'`, so a spreadsheet won't run
  them as formulas.
- **Search:** a box in the top bar (`Ctrl/⌘ K` focuses it) finds accounts by name or domain in the workspace (top 8,
  case-insensitive contains) and opens the account. Ask is out of scope.
- **Adaptive density:**
  - **≥ 1024 px:** all columns, with the first column sticky.
  - **768–1023 px:** system columns plus 2 predicate columns, and a "+N columns" button that opens the rest in a
    drawer.
  - **< 768 px:** cards (name, verdict, band, top 2 values, stage).
- **States:**
  - **No accounts yet:** "Sources are running: X of Y active, Z records fetched", linking to `/sources`.
  - **Filter matches nothing:** "No accounts match", with **Clear filter**.
  - **Loading:** skeleton rows with the header visible.
  - **Error:** an inline banner with Retry. A cell error stays in its cell.
- **Design:** the light system, `@signal/ui`, no purple, and the colour families (mustard = stale/needs you, lava =
  error/cost). After Task 1, utilities on `si-*` elements work, so wrapper divs are no longer needed.
- **Gates:**
  - `pnpm db:check`;
  - `pnpm typecheck`, `pnpm lint` (0 errors) and `pnpm --filter web build`;
  - `pnpm --filter @signal/formula test`;
  - Playwright through CI, with screenshots of the table, panel, Add column, board and phone cards looked at by the
    controller.

## Review Focus

1. **A tampered `f` URL parameter** (bad base64, a huge AST, an unknown column, an injection attempt). The page shows
   "This filter couldn't be read", with **Clear filter**, and never errors. The server always compiles through
   `app.filter_where`. *Tests in Tasks 2 and 6.*
2. **CSV injection in export.** A company named `=HYPERLINK(...)` or `+cmd` exports with a leading `'`. Commas, quotes
   and newlines are quoted per RFC 4180. *Test in Task 7.*
3. **Enter on a focused cell** opens the panel and never queues a run. No keyboard path runs anything without a
   confirm dialog. *Test in Task 4.*
4. **Large workspaces.** 2,000 accounts × 12 columns: a page loads only its 50 rows' cells, and export pages through
   the RPC (no single 10,000 × cells call). *pgTAP and an e2e timing guard in Tasks 2 and 7.*
5. **Viewer role.** A Viewer can browse, filter, open panels, export and save personal views. They cannot run, edit,
   mark wrong, add columns or save workspace views: the controls are hidden, and the RPCs refuse with `42501`.
   *Tests in Tasks 2, 4 and 5.*

---

### Task 1: The design-system CSS goes into a cascade layer

**Files:**
- Modify: `apps/web/src/app/globals.css` (`@import '@signal/ui/components.css' layer(components);`, and the same
  for any other `@signal/ui` CSS that should lose to utilities)
- Modify: any element that changes look because a previously dead utility now applies, so it looks as intended:
  - every element carrying both an `si-*` class and a utility on the same property;
  - grep `apps/web/src` for `className=` containing `si-` plus a size, spacing, display or colour utility;
  - the known case is `sources/page.tsx` `si-h2 text-[20px]`.
- Modify: `apps/web/e2e/screens.spec.ts`, to take a screenshot of every route at 1440 and 390 px
  (`screens-<route>-<width>.png`, uploaded in the artifact).

**Behaviour:**
- After this task, a Tailwind utility beats an `si-*` rule on the same property everywhere, which is the intent
  wherever one is written.
- Compound `si-*` selectors keep their specificity. Preflight sits in `base`, so `si-*` still beats it.
- Remove the P4 workaround wrappers only where the plain element now works. Keep them where removing them adds risk.

- [ ] **Step 1:** before changing anything, list every element with an `si-*` class plus a conflicting utility, with
  the property in conflict and the intended look, in the report.
- [ ] **Step 2:** make the import change and fix each listed element.
- [ ] **Step 3:** run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`.
- [ ] **Step 4:** commit with `git commit -m "fix(web): design-system CSS in a cascade layer so utilities apply"`. The
  controller compares CI screenshots before and after for every route.

### Task 2: Migration 0043 — table page, cell panel and table actions

**Files:**
- Create: `supabase/migrations/20261121000043_operating_table.sql`
- Create: `supabase/tests/database/27_operating_table.test.sql`

**Interfaces (each SECURITY DEFINER with `search_path=''`; role and scope as noted; exact jsonb shapes documented in
a comment above each):**
- **`public.account_table(p_ws, p_brief, p_filter jsonb, p_sort jsonb, p_columns uuid[], p_limit int default 50,
  p_offset int default 0) returns jsonb`** (Viewer+):
  - `{total, rows: [{account_id, entity_id, name, country, domain, verdict, band, priority, stage, owner, last_signal,
    cells: {<predicate_id>: {state, value, confidence, claim_id, run_id, error}}}], columns: [{id, key, label,
    value_type, fill_method, auto_run}]}`;
  - rows come from P3 `view_entities` and cells from P3 `cells`, for this page only;
  - `p_columns` null means the Brief's columns (spec order), or none for all;
  - an invalid filter raises 22023 with the P3 path message (Review Focus 1);
  - `p_limit` ≤ 200.
- **`public.cell_detail(p_ws, p_entity, p_predicate) returns jsonb`** (Viewer+):
  - `{column, state, value, confidence, fill_label, claims: [{id, value, quote, source_url, source_type, observed_at,
    status, superseded_at}], inputs: [{column_label, value, claim_id}] (derived only), runs: [{id, status, cost_usd,
    brief_version, queued_at, started_at, finished_at, error}]}`;
  - at most 50 runs and 50 claims;
  - claims of other orgs are never visible.
- **`public.set_manual_value(p_ws, p_entity, p_predicate, p_value jsonb) returns uuid`** (Editor+):
  - validates the value against the predicate type (P1 `validate_claim_value` rules in SQL);
  - writes a claim from a per-workspace derived source record of kind `manual` (connector `derived:manual`, like P3's
    formula source), with confidence 100, `observed_at now()` and quote `Entered by <member display name>`;
  - supersedes earlier manual claims for the cell;
  - emits `claim.created`, so verdicts and formulas recompute.
- **`public.mark_claim_wrong(p_ws, p_claim, p_reason text) returns uuid`** (Editor+):
  - retracts the claim (status `retracted`) and emits `claim.retracted` (add the emitter);
  - creates a `review_item` of type `claim_wrong` carrying the reason and the claim id (extend the type check if
    needed);
  - returns the review item id.
- **`public.pin_view(p_ws, p_view text, p_pinned boolean)`** (Viewer+, per member):
  - a table `public.view_pin (member_id, workspace_id, view_ref text, position int, primary key (member_id,
    workspace_id, view_ref))`;
  - `view_ref` is a preset name or a saved view id visible to the member.
- **`public.pinned_views(p_ws) returns jsonb`**: `[{ref, name, count, href}]`, where count is from `view_entities`
  `total` under the member's current lens (pass `p_brief`).
- **`public.search_accounts(p_ws, p_q text) returns jsonb`** (Viewer+): top 8 accounts whose name or domain contains
  `p_q` (case-insensitive, min 2 chars, trimmed, `%` / `_` escaped), as `[{account_id, name, domain, href}]`.
- **`public.set_auto_run(p_ws, p_predicate, p_on boolean)`** (Editor+, formula columns only, audited).

- [ ] **Step 1: Write the failing pgTAP suite.** Seed 3 accounts, 2 columns (extract and formula), claims, runs, a
  derived cell and a stale cell. Assert:
  - every RPC's exact shape;
  - paging and total;
  - Brief columns versus `p_columns`;
  - a manual value (supersedes, type-checked, a `claim.created` row);
  - mark wrong (retracted, a `claim.retracted` row, a review item);
  - pins and their counts;
  - search (escaping `%`, the minimum length);
  - auto-run toggle;
  - a 22023 on a bad filter;
  - Viewer refusals (`42501`) for every write;
  - non-member refusals for every RPC.
- [ ] **Step 2:** run it and see it fail.
- [ ] **Step 3:** write the migration.
- [ ] **Step 4:** run `pnpm db:check` and see it pass.
- [ ] **Step 5:** commit with `git commit -m "feat(db): operating-table page, cell panel, manual values, mark wrong, pinned views, search"`.

### Task 3: The table — rows, cells, density and states

**Files:**
- Rewrite: `apps/web/src/app/(app)/workspace/[wsid]/accounts/page.tsx`
- Create: `apps/web/src/app/(app)/workspace/[wsid]/accounts/_table/` — `table.tsx` (client: selection, keyboard,
  sticky first column), `cell.tsx` (the nine states), `cards.tsx` (phone), `states.tsx`, `url.ts` (encode/decode
  the URL state, including base64url `f`)
- Delete: `accounts-filter.tsx`, once nothing imports it
- Create: `apps/web/e2e/accounts.spec.ts`

**Behaviour:** Global Constraints Columns, Cell states, Adaptive density and States; page size 50 with Prev/Next.
The URL is the state.

- [ ] **Step 1: Write failing e2e tests** (CI), seeding with `sql()`:
  - **Rows and order:** the table shows the seeded rows with system columns in order and the Brief's columns.
  - **States:** each of the nine cell states renders as specified, seeded through runs and claims as in pgTAP.
  - **Density:** at 900 px, "+N columns" appears; at 390 px, cards appear with no overflow.
  - **Empty states:** no accounts shows the Sources message; a filter matching nothing shows Clear filter.
  - **Bad filter (Review Focus 1):** `?f=` garbage shows "This filter couldn't be read".
  - **Screenshots:** desktop table and phone cards.
- [ ] **Step 2:** confirm by reading (CI).
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`.
- [ ] **Step 5:** commit with `git commit -m "feat(web): the operating table — rows, cell states, density, honest empty states"`.

### Task 4: Cell panel and keyboard

**Files:**
- Create: `apps/web/src/app/(app)/workspace/[wsid]/accounts/_table/panel.tsx`
- Create: `apps/web/src/app/actions/cells.ts` (rerun, edit, mark wrong)
- Modify: `table.tsx` (keyboard)
- Modify: `apps/web/e2e/accounts.spec.ts`

**Behaviour:**
- **Panel:** Global Constraints Cell panel. **Edit** shows a type-specific input with the same validation as SQL,
  and SQL errors show inline. **Mark wrong** asks for a reason, required, at most 500 characters. **Re-run** shows
  the estimate and confirms.
- **Keyboard:** Global Constraints Keyboard.
- **Viewers:** the actions are hidden from Viewers.

- [ ] **Step 1: Write failing e2e tests** (CI):
  - **Panel content:** clicking a value cell shows the quote, source link, date and history.
  - **Derived cell:** shows its inputs.
  - **Edit:** writes a manual value, the cell shows "Entered by you" and the old value appears struck through.
  - **Mark wrong:** creates a Review item (the sidebar Review count goes up by 1).
  - **Re-run:** confirms the cost, then the cell shows queued.
  - **Enter never runs (Review Focus 3):** arrows plus Enter open the panel, and `select count(*) from
    predicate_run` is unchanged.
  - **Viewer (Review Focus 5):** sees no action buttons.
  - **Screenshot:** the panel.
- [ ] **Step 2:** confirm by reading.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the gates.
- [ ] **Step 5:** commit with `git commit -m "feat(web): cell panel with evidence and history; Enter opens, never runs"`.

### Task 5: Add column, run controls and the bulk bar

**Files:**
- Create: `_table/add-column.tsx`, `_table/column-menu.tsx`, `_table/bulk-bar.tsx`, `_table/run-confirm.tsx`
- Create: `apps/web/src/app/actions/columns.ts` (add column through draft → preview → publish; run requests;
  auto-run toggle)
- Modify: `accounts.spec.ts`

**Interfaces:**
- **Consumes:**
  - P2's `save_brief_draft_ui`, `request_brief_preview`, the preview read and `publish_brief`;
  - P3's `estimate_column_run` and `request_column_run`;
  - Task 2's `set_auto_run`;
  - `@signal/formula` (`parse`, `references`, `evaluate`, `formulaProblem`), and P2/P3's column-spec helpers in
    `lib/briefs.ts` (`keyFromLabel`, `columnKeyProblem`).

**Behaviour:** Global Constraints Add column, Run controls and Bulk bar. Under "All Briefs", Add column is disabled
with its message. Viewers see no Add column, run menus or bulk Run.

- [ ] **Step 1: Write failing e2e tests** (CI):
  - **Formula column:** adding "Programme and sites" (`drone_programme and site_count > 0`) shows a 5-row live
    preview. Save → publish → the run choices with `$0.00`. After "Run on all in view", cells become derived.
  - **Extract column:** adding one shows the "Fills from N records" line and an estimate.
  - **Run empty:** shows `N accounts · est. $X` and queues N runs.
  - **Budget refusal:** a cap below the estimate shows the 55000 message and **Run the first N**, which queues N.
  - **Bulk run:** select 2 rows → Run columns → confirm queues 2 runs per chosen column.
  - **Viewer (Review Focus 5):** no Add column or run controls.
  - **Screenshots:** Add column and the run confirm.
- [ ] **Step 2:** confirm by reading.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the gates.
- [ ] **Step 5:** commit with `git commit -m "feat(web): Add column with live preview and cost, run controls, bulk bar"`.

### Task 6: Filters, views, pins and search

**Files:**
- Create: `_table/filter-builder.tsx` and `_table/view-bar.tsx`
- Create: `apps/web/src/components/search-box.tsx`
- Modify: `apps/web/src/components/nav-links.tsx` (pinned views under Accounts) and `shell.tsx` (search box)
- Create: `apps/web/src/app/actions/views.ts`
- Modify: `accounts.spec.ts` and `nav.spec.ts`

**Behaviour:** Global Constraints Filters, Views and Search. Pinned views stream in their own Suspense, like the
Review count. Each one links to `?view=` and shows a plain count (a mustard count only for `errored` and `stale`).

- [ ] **Step 1: Write failing e2e tests** (CI):
  - **Filter chips:** building "Verdict is Confirmed, Likely" plus "<column> is not empty" updates the rows, and the
    URL `f` round-trips on reload.
  - **Save and pin:** Save view (personal) appears in the view bar. Pin to sidebar shows it under Accounts with the
    count, and unpinning removes it.
  - **Workspace views:** a Viewer cannot save one (the option is hidden).
  - **Presets:** each preset loads its filter.
  - **Search:** Ctrl+K focuses search, typing a seeded name shows it, and Enter opens it. `%` in the query matches
    literally.
  - **Screenshot:** the filter builder open.
- [ ] **Step 2:** confirm by reading.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the gates.
- [ ] **Step 5:** commit with `git commit -m "feat(web): filter chips, saved and pinned views with counts, account search"`.

### Task 7: Board, By country, and export

**Files:**
- Create: `_table/board.tsx`
- Create: `apps/web/src/app/(app)/workspace/[wsid]/accounts/export/route.ts`
- Modify: `accounts.spec.ts`

**Behaviour:** Global Constraints Layouts and Export. Export reads pages of 200 through `account_table` until total
or 10,000 rows, then streams.

- [ ] **Step 1: Write failing e2e tests** (CI):
  - **Board by stage:** shows every stage column with counts, and the seeded cards are in the right columns.
  - **Board by verdict:** works the same way.
  - **By country:** groups the seeded accounts.
  - **Export:** CSV header = the visible columns; the row count matches the view.
  - **CSV injection (Review Focus 2):** a company named `=HYPERLINK("x")` exports as `'=HYPERLINK(""x"")`, and a name
    with a comma and newline is quoted.
  - **Evidence:** `&evidence=1` adds the source URL columns.
  - **Large workspace (Review Focus 4):** 300 seeded accounts export in under 10 s.
  - **Screenshots:** board and By country.
- [ ] **Step 2:** confirm by reading.
- [ ] **Step 3:** implement.
- [ ] **Step 4:** run the gates.
- [ ] **Step 5:** commit with `git commit -m "feat(web): board and by-country layouts, CSV export of the current view"`.

---

**After Task 7:**
1. Open the PR as a draft early, with base `feat/shell-home`, so CI runs.
2. Run the whole-branch review, then the independent completion audit.
3. The controller looks at every screenshot.
4. The PR body names what moves on:
   - the real map (needs site coordinates);
   - board drag (needs the CRM);
   - the plain-English filter and Ask (needs AI credit);
   - AI research and waterfall columns, Find people and Merge (P6);
   - the account page tabs (P6 and P7).
