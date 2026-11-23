# P8 Reports and Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development, lean process:
> - sonnet builders, except the database and money tasks (opus);
> - a per-task review only for Task 1;
> - one combined final review and completion audit;
> - Playwright and screenshots through CI.
>
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** Measure whether a hypothesis is working and what it costs, and put every workspace control in one Settings
page.
- **Reports** has five sections: Funnel, Coverage, Cost, Sources and Calibration. Every number links to the rows
  behind it.
- **Settings** has these sections:
  - Workspace;
  - Business context;
  - Mailboxes and Integrations, as placeholders until the Email engine and CRM rounds;
  - Providers (built in P6);
  - Budget & usage;
  - Data: labels, segments, the column library and the blocklist;
  - Health;
  - API & tokens.
- **Hand labels and segments** arrive, unblocking the P5 bulk **Label** action (P5 ruling R8).

**Architecture:**
- **Reports:** one read-only RPC per section in migration 0046, like P4's Home cards.
  - Each card loads and fails on its own.
  - Every count links to `/workspace/<ws>/accounts?…` with a P3 filter, or to `/people`, `/sources` or `/review`.
  - The existing M16 metric definitions (`m16_reporting.compute_metric`, `get_funnel`) stay the source for metric
    names; the funnel RPC reuses the same definitions.
- **Settings:** one page with sections (`/settings?section=`) and a left section list (tabs on a phone). The sections
  that exist today (members, invites, tokens, usage) move into it unchanged.
- **Labels and segments:**
  - `public.label` today holds derived labels per Brief version. Hand labels get their own table, `account_tag`, so
    derived labels stay pure.
  - `segment` (P2) gets its P3 filter AST editor and member counts.
- **Health:** this reads data that already exists:
  - radar health (P2);
  - Procrastinate queue depth and failed jobs (through a worker status endpoint, like P6's provider status);
  - provider errors (P6 attempts);
  - the outbox backlog.

**Tech Stack:**
- Postgres / pgTAP;
- Next.js App Router, React 19 and `@signal/ui`;
- Python worker (status endpoint);
- Playwright through CI.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md)
§9 (one budget with alerts at 50/80/100%, per-Brief caps, failures visible). Page detail is in
[05 §4.11 Reports and §4.12 Settings](../../research/clay/05-ia-and-pages.md). Roadmap row P8. The Email engine and
CRM stay placeholders (spec status block).

## Global Constraints

- **Branch:** `feat/reports-settings` from `main` after P7 merges. New migration:
  `supabase/migrations/20261125000046_reports_settings.sql`.
- **Reports** (`/reports`, lens-aware like Home; the old `/workspace/<ws>/reports` redirects here):
  1. **Funnel by Brief:** found → matched → Confirmed → contacted → replied → meeting → won.
     - Contacted onwards read from the existing `activity` and `meeting` tables, and show "—" with "Arrives with the
       Email engine" until messages are sent.
     - Each stage links to its rows.
  2. **Coverage:** per column:
     - the fill rate (cells with a value ÷ accounts in the lens);
     - the split by fill method (extracted / provider / AI / formula / manual);
     - stale and error counts;
     - this month's cost.

     Each count links to the table with a `{"run": key}` or column filter.
  3. **Cost:** this month and the previous 2 months, by Brief, by column, by provider and by AI capability. It also
     shows the cost per Confirmed account and per meeting ("—" when the denominator is 0). Totals reconcile with
     `app.period_spend` to the cent.
  4. **Sources:** per radar, over the last 30 days:
     - records fetched;
     - accounts matched;
     - Confirmed accounts it found;
     - cost;
     - cost per Confirmed.

     Sorted by Confirmed, descending.
  5. **Calibration:**
     - verdicts marked wrong, by reason (P7 pilot marks and P5 claim_wrong);
     - Confirmed accounts later marked wrong;
     - won/lost by verdict ("—" until deals exist; CRM round).
  - **Not enough data:** sections with no data show "Fills in after <event>" (for example "after your first
    accounts are found"), while sections that have data still render.
  - **Every number is a link,** and none is ever null: 0 or "—" with a reason.
- **Settings sections** (`/settings?section=<key>`), in this order:

  | key | Section | Contents |
  |---|---|---|
  | `workspace` | Workspace | Name (Admin edits), members & roles, invites. The existing components move here |
  | `business` | Business context | Company name, offer, proof points (at most 5 lines). Stored in `workspace_settings.business_context jsonb` and fed to every AI prompt that takes context (P7 generate, research columns). "Generate from domain" reuses P7's fetch and a small prompt `business_context/v1.md` |
  | `mailboxes` | Mailboxes | Placeholder: "Arrives with the Email engine" (no build) |
  | `integrations` | Integrations | Placeholder rows for Calendar, Slack, Resend and CRM export: "Not set up yet" |
  | `providers` | Providers | Built in P6; only linked here |
  | `budget` | Budget & usage | The monthly workspace cap and per-Brief caps (Admin edits `public.budget` through `public.set_budget`), alerts at 50/80/100%, the usage ledger (the existing usage component), and pending budget approvals with Approve/Deny (existing `budget_approval`) |
  | `data` | Data | Hand labels (create, rename, colour, delete), segments (name + P3 filter chips + member count), the column library (save a column spec as a reusable template; Add column can start from a template), and the blocklist (existing `exclusion_list`, org-wide) |
  | `health` | Health | Source runs (radar status, last run, last error), worker queue (depth, oldest job age, failed jobs in 24 h), provider errors (24 h), outbox backlog. Anything failing is listed first, in lava, with what to do |
  | `api` | API & tokens | The existing M13 tokens component |

- **Budget alerts:** when spend crosses 50%, 80% or 100% of a cap, the workspace's Admins get an in-app
  notification. It appears on Home's Needs you, plus a `budget.threshold_crossed` outbox event for the later
  Slack/Resend notifiers. Each threshold fires once per period per cap.
- **Hand labels:**
  - an `account_tag (workspace_id, name, colour)` table plus `account_tag_link (account_id, tag_id)`;
  - the P5 bulk **Label** action is enabled: apply or remove a tag for the selected accounts;
  - the filter syntax gains `{"system": "tag"}` with `one_of`, which needs P3 `app.filter_where` to be extended in
    0046 with `create or replace`;
  - the table shows tags as chips beside the name.
- **Column library:** a `column_template (workspace_id, name, spec jsonb, created_by)` table, where `spec` is a P2/P3/P6
  column spec without a key. Add column gets a "From library" tab.
- **Design:** the light system, `@signal/ui` and no purple. On a phone the Settings section list becomes a scrolling
  tab row (P4 pattern). The Reports cards stack.
- **Gates:**
  - `pnpm db:check`;
  - `uv run pytest -q`;
  - ruff, format and mypy;
  - `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`;
  - CI Playwright, with screenshots of every Settings section and every Reports section, desktop and phone, looked at.

## Review Focus

1. **Cost totals that don't reconcile.** The Cost section, Home's spend tile, the sidebar meter and
   `app.period_spend` must agree to the cent for the same period. *pgTAP in Task 1.*
2. **Alert thresholds firing twice** (two concurrent spends both cross 80%). Exactly one notification and one event
   per threshold per period. *pgTAP with two sessions in Task 1.*
3. **A Viewer opening Settings.** Every section is visible read-only: no edit controls, and the RPCs refuse with
   `42501`. Tokens and invites stay Admin-only, as today. *e2e in Task 4.*
4. **Deleting a hand label that's used in a saved view's filter.** The view shows "This filter couldn't be read"
   (P5), with Clear filter. It never errors, and the label's links are removed. *Tests in Tasks 1 and 3.*
5. **Reports on a brand-new workspace.** Every section shows its "Fills in after …" line, with no errors and no
   nulls. *pgTAP in Task 1 and e2e in Task 2.*

---

### Task 1: Migration 0046 — reports, budget alerts, labels, segments and templates (opus, reviewed)

**Files:**
- Create: `supabase/migrations/20261125000046_reports_settings.sql`
- Create: `supabase/tests/database/30_reports_settings.test.sql`

**Interfaces (Viewer+ reads, Admin/Editor writes as noted; every jsonb shape documented above its function):**
- **Reports:**
  - `public.report_funnel(p_ws, p_brief)`;
  - `public.report_coverage(p_ws, p_brief)`;
  - `public.report_cost(p_ws, p_brief, p_months int default 3)`;
  - `public.report_sources(p_ws, p_brief)`;
  - `public.report_calibration(p_ws, p_brief)`.
- **Budget and alerts:**
  - `public.set_budget(p_ws, p_brief uuid null, p_period, p_cap_usd)` (Admin; null cap removes it);
  - `app.check_budget_thresholds(p_ws)`, called from `settle_reservation` (`create or replace`, keeping the P3 change
    that stamps `predicate_run_id`). It writes `budget_alert (workspace_id, budget_id, period_start, threshold)`,
    unique, and emits `budget.threshold_crossed`;
  - `public.budget_alerts(p_ws)`.
- **Labels and segments:**
  - `account_tag`, `account_tag_link`, `public.save_tag`, `public.delete_tag`, `public.tag_accounts(p_ws, p_tag,
    p_account_ids, p_on)` (Editor+);
  - `app.filter_where` extended for `{"system": "tag"}`;
  - `public.save_segment(p_ws, p_id, p_name, p_filter)` (Editor+) and `public.segment_counts(p_ws)`.
- **Column library:** `column_template`, `public.save_column_template`, `public.delete_column_template`,
  `public.list_column_templates`.
- **Business context:** `public.set_business_context(p_ws, p_context jsonb)` (Admin; validated shape).

**Steps:**
- [ ] Write failing pgTAP tests:
  - every shape;
  - **Review Focus 1:** cost reconciliation;
  - **Review Focus 2:** one alert per threshold, including the concurrent case;
  - **Review Focus 4:** a deleted tag in a saved filter gives 22023 on compile, so the view shows its bad-filter
    state;
  - **Review Focus 5:** the empty workspace;
  - Viewer refusals;
  - tags in filters.
- [ ] Write the migration.
- [ ] Run `pnpm db:check`.
- [ ] Commit with `git commit -m "feat(db): reports, budget alerts, hand labels, segments and column templates"`.

### Task 2: The Reports page

**Files:**
- Rewrite: `apps/web/src/app/(app)/reports/page.tsx`
- Create: `apps/web/src/app/(app)/reports/_cards/` (one component per section, plus card-error)
- Modify: `next.config.ts` (redirect `/workspace/<ws>/reports` → `/reports`)
- Create: `apps/web/e2e/reports.spec.ts`

**Steps:**
- [ ] Write failing e2e tests (CI):
  - **Seeded workspace:** every section shows its numbers and links (each number's href is asserted).
  - **Review Focus 5:** an empty workspace shows the "Fills in after …" lines.
  - **Card failure:** revoking one report RPC shows that card's error while the rest render (the P4 pattern).
  - **Screenshots:** `reports-desktop.png`, `reports-phone.png`.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): Reports — funnel, coverage, cost, sources and calibration"`.

### Task 3: Labels in the table, segments, and the column library in Add column

**Files:**
- Modify: P5 `_table/bulk-bar.tsx` (Label enabled), `cell.tsx` / `table.tsx` (tag chips), `filter-builder.tsx`
  ("Tag" field), and `add-column.tsx` ("From library")
- Create: `apps/web/src/app/actions/tags.ts`
- Modify: `accounts.spec.ts`

**Steps:**
- [ ] Write failing e2e tests:
  - **Labels:** bulk Label applies and removes a tag, and the chips show.
  - **Filtering:** filtering by tag works.
  - **Review Focus 4:** deleting a used tag shows the bad-filter state on a view using it.
  - **Column library:** saving a column as a template and adding it from the library works.
  - **Screenshot:** `accounts-tags.png`.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): hand labels in the table and filters, column library in Add column"`.

### Task 4: Settings as one sectioned page

**Files:**
- Rewrite: `apps/web/src/app/(app)/settings/page.tsx` (section list + `?section=`)
- Create: `apps/web/src/app/(app)/settings/_sections/` with `workspace.tsx` (moving `members.tsx` and
  `invite-form.tsx`), `business.tsx`, `mailboxes.tsx` (placeholder), `integrations.tsx` (placeholders),
  `budget.tsx` (caps, alerts, the existing `usage.tsx`, approvals), `data.tsx` (tags, segments, column library,
  blocklist), `health.tsx`, and `api.tsx` (the existing `api-access.tsx` and `token-form.tsx`)
- Modify: `services/worker/src/worker/health.py` (extend P6's status endpoint with queue depth, oldest job age,
  failed jobs in 24 h, and the outbox backlog; token-protected)
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/business_context/v1.md` plus `LOCK.json`, and a
  small job `business.generate` reusing P7's site fetch
- Modify: `apps/web/src/lib/nav.ts` (Settings links to sections), Home Needs you (budget alerts)
- Create: `apps/web/e2e/settings.spec.ts` (extending P6's)

**Steps:**
- [ ] Write failing tests:
  - **Navigation:** each section loads.
  - **Review Focus 3:** a Viewer sees read-only sections.
  - **Budget:** an Admin sets a monthly cap, and the sidebar meter and the budget section agree.
  - **Alerts:** a seeded 80% crossing shows on Home's Needs you and in Budget alerts.
  - **Health:** lists a seeded failing radar first.
  - **Business context:** saves, and "Generate from domain" shows its steps (worker played through SQL).
  - **Worker status endpoint:** never returns secrets, and refuses without the token (pytest).
  - **Screenshots:** each section at desktop, plus Settings on a phone.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): Settings in one place — workspace, business context, budget, data, health, tokens"`.

---

**After Task 4:**
1. Open the PR as a draft early so CI runs.
2. Run one combined final review and audit, and one fix wave.
3. The controller looks at every screenshot.
4. The PR closes the redesign roadmap: P1–P8 done, with the Email engine and CRM still awaiting their study round.
