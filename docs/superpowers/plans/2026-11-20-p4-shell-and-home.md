# P4 Shell and Home Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The product reads as the journey it is.
- A new sidebar has the groups Find, Work, Engage and Measure, with **Email engine** and **CRM** as placeholder
  menus.
- A Brief lens scopes the pages to one Brief or to all of them.
- Old routes are folded into the new names.
- A new Home answers "what needs me, what changed, what's next".

**Architecture:**
- **One navigation model** (`apps/web/src/lib/nav.ts`) drives the sidebar, the phone row, the placeholder pages and
  the screens e2e. It replaces `lib/screens.ts`.
- **The Brief lens** lives in the URL (`?brief=`) and in a cookie (`si_brief`), and is read on the server by one
  helper.
- **Home** is built from small, separate RPCs, one per card, in migration 0042. Each card loads and fails on its
  own, and every number links to the rows behind it.
- **Visuals** use the existing `@signal/ui` components and the light system, following the approved mockup.

**Tech Stack:**
- Next.js (App Router, server components) and React 19, with `@signal/ui`;
- Postgres / Supabase, with pgTAP on PGlite;
- Playwright e2e, which runs in CI only (no Docker locally).

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md):
- §3 navigation, the Brief lens defaulting to the last Brief used, the phone layout and the old-route map;
- §4 Home: the areas table and the empty-workspace rule;
- §9 failures visible and one budget;
- the status block: People has its own menu item, and Email engine and CRM are placeholders.

Page detail is in [05-ia-and-pages §2 and §4.1](../../research/clay/05-ia-and-pages.md). The **mockup** is
[home-dashboard.html](../../research/clay/mockups/home-dashboard.html); open it in a browser. Roadmap:
[2026-11-18-redesign-roadmap.md](2026-11-18-redesign-roadmap.md), row P4.

## Global Constraints

- **Branch:** `feat/shell-home` from `main` (at `161fbd4` or later). One PR.
- **New migration:** `supabase/migrations/20261120000042_home.sql`. Never edit a released migration.
- **Navigation, exactly this tree** (labels verbatim; `→` is the route):
  ```
  Home                                  → /
  FIND      Briefs                      → /briefs
            Sources                     → /sources
  WORK      Accounts                    → /accounts
            People                      → /people            (placeholder: arrives in P6)
            Review                      → /review            (mustard count of pending decisions)
  ENGAGE    Email engine                → /email             (placeholder)
              Sequences                 → /email/sequences   (placeholder)
              Approvals                 → /email/approvals   (placeholder)
              Inbox                     → /email/inbox       (placeholder)
              Mailboxes                 → /email/mailboxes   (placeholder)
            CRM                         → /crm               (placeholder)
              Pipeline                  → /crm/pipeline      (placeholder)
              Meetings                  → /crm/meetings      (placeholder)
              Tasks                     → /crm/tasks         (placeholder)
  MEASURE   Reports                     → /reports
  Settings                              → /settings          (footer, with the budget meter)
  ```
- **Old routes:**
  - `/discovery` → `/sources`: a permanent redirect, so the Sources page is today's Discovery page moved and
    renamed;
  - `/campaigns` → `/email/sequences`;
  - `/inbox` → `/email/inbox`;
  - `/meetings` → `/crm/meetings`;
  - `/accounts` keeps redirecting to `/workspace/<ws>/accounts` (unchanged);
  - `/workspace/<ws>/verdicts` stays until the P5 table can show the "By verdict" view;
  - the existing workspace-scoped campaigns, inbox and meetings screens stay reachable (see Placeholders).
- **Placeholder pages** (D9 / spec status block): each one shows:
  - the H1 (the menu label);
  - one line on what the page will do;
  - a "Coming with the Email engine" or "Coming with the CRM" / "Coming in P6" tag (mustard `si-ftag`);
  - for Email engine Inbox and Sequences and for CRM Meetings and Tasks, a link to today's working screen (e.g. `Open
    today's inbox` → `/workspace/<ws>/inbox`), so no current capability is lost.

  They never show sample data. The `/email` and `/crm` index pages list their sub-pages as cards.
- **Brief lens:**
  - **Values:** a Brief id, or `all`.
  - **Resolution order:**
    1. `?brief=` in the URL;
    2. the `si_brief` cookie, if that Brief is still in the workspace;
    3. the most recently published Pilot or Active Brief in the workspace;
    4. `all`.
  - Choosing in the lens selector, or opening `/briefs/<id>`, sets the cookie for 90 days, scoped to the workspace
    (cookie value `<ws>:<brief|all>`).
  - In P4 the lens is honoured by Home. Other pages read it in later plans.
  - The selector shows "All Briefs" plus each non-archived Brief by name, and shows its state for Pilot and Paused
    Briefs.
- **Budget meter** (sidebar footer, every page):
  - shows `$<spent> / $<cap>` this month for the workspace budget, from `app.period_spend`, the sum the budget guard
    uses;
  - is mustard at 80% or more, lava at 100% or more, and shows `$<spent> · no cap` when no monthly cap is set;
  - links to `/settings`.
- **Review count:** pending `review_item` plus pending `verdict_review` for the workspace, shown as a mustard count
  only when it is above 0.
- **Home areas** (spec §4; the mockup shows the layout). Each is its own RPC and its own card:

  | Area | RPC (Viewer+, `p_ws uuid, p_brief uuid` where null = all) | Content |
  |---|---|---|
  | Header | `public.home_header` | lens Brief name, version, state, live since (first publish), sources running; for `all`: workspace name and count of Pilot/Active Briefs |
  | Journey | `public.home_journey` | 6 steps in order — `brief` (any Brief), `sources` (any active radar), `first_accounts` (≥ 1 account), `first_confirmed` (≥ 1 Confirmed verdict under the lens), `contacts` (≥ 1 valid contact_point), `first_email` (always pending: "Comes with the Email engine") — each `{key, done, href}`; hidden on the page when all except `first_email` are done |
  | Number tiles | `public.home_metrics` | accounts found (pool), Confirmed (mint), replies and meetings held this month (grass, from the existing reply and meeting tables), spend this month vs cap (lava); each with an `href` to its rows |
  | Needs you | `public.home_needs_you` | Review decisions (count, `/review`), replies to classify (count, today's inbox), stale accounts (count + re-run estimate, using P3 `estimate_column_run` mode `stale` summed over the lens's columns), errored accounts (count) |
  | Accounts to act on | `public.home_accounts_to_act` | top 5 accounts under the `ready_to_contact` filter (P3 `view_entities`), sorted by priority: name, verdict, band, "why now" (the newest quoted claim that a rule cites, else the newest quoted claim), valid contacts, next action (open `next_step`), each with `href` |
  | What changed (last 7 days) | `public.home_changes` | at most 10 items, newest first, of kind `verdict` (entity, from → to, the cited claim's quote), `brief_version` (Brief, version, count of stale cells it caused), `new_accounts` (radar name, count) |
  | Email engine card | (no RPC) | placeholder card: "Sending, approvals and replies arrive with the Email engine", plus a link to `/email` |
  | CRM card | `public.home_crm` | accounts by stage (all stages, counts), tasks due today (count, today's tasks link) |
  | Sources card | `public.home_sources` | each radar in the lens: name, type, health (`ok` / `silent` / `incident` from status), records in the last 7 days; failures listed first |

- **Card failures:** a card whose RPC errors renders `This card could not load. <Retry>` (Retry is a plain link to
  the same URL). The other cards still render.
- **Empty workspace** (no Brief): only the journey strip and one card, "Describe who you sell to", linking to
  `/briefs/new`.
- **Every number is a link** to the rows behind it. A link to a page that P5 will filter carries the intended query
  (e.g. `/accounts?view=ready_to_contact`), even if the page ignores it until P5.
- **Design:**
  - the light Mixpanel-guideline system, `@signal/ui` components (`MetricTile`, `Card`, `VerdictBadge`, `BandChip`,
    `HealthIndicator`, `StageBadge`, `FamilyTag`), and `si-*` classes;
  - **colour families:** pool for discovery, mint for judgement, grass for engagement, mustard for needs you, lava
    for cost and alerts;
  - no purple;
  - at 390 px:
    - no horizontal page scroll;
    - the sidebar becomes one scrolling row of **top-level items only** (sub-items move into each page's tabs);
    - the budget meter is hidden from the row and shown on Settings.
- **Gates before calling the work done:**
  - `pnpm db:check`;
  - `pnpm typecheck` and `pnpm lint` (0 errors);
  - `pnpm --filter web build`;
  - `uv run pytest -q`, unaffected but run once;
  - Playwright through CI on the PR, with the `brief-screens` artifact extended to the shell and Home screenshots
    and looked at by the controller.

## Review Focus

1. **A user in two workspaces.**
   - The lens cookie for workspace A must not leak into B. Switching workspace resolves the lens again with the
     fallback order.
   - Every Home RPC refuses a workspace the caller isn't a member of (`42501`).

   *pgTAP in Task 2; e2e in Task 3.*
2. **A lens pointing at a Brief that was archived, deleted or belongs to another workspace** (an old cookie or a
   hand-edited URL). It falls back silently down the resolution order and never errors. *Test in Task 3.*
3. **A brand-new workspace with nothing in it.** Home shows only the journey strip and the "Describe who you sell
   to" card. No RPC errors on empty tables, and every count is 0, not null. *pgTAP in Task 2; e2e in Task 4.*
4. **One Home card fails** (e.g. its RPC raises). That card says so with Retry, and the rest of Home renders.
   *e2e in Task 4, by revoking one RPC in the test database.*
5. **Phone width.** At 390 px:
   - the sidebar is one scrolling row with no sub-items;
   - Home cards stack;
   - no page scrolls horizontally, Home and the placeholders included.

   *Screenshot and overflow tests in Tasks 1 and 4.*

---

### Task 1: Navigation model, sidebar, routes and placeholder pages

**Files:**
- Create:
  - `apps/web/src/lib/nav.ts`;
  - `apps/web/src/components/placeholder-page.tsx`;
  - `apps/web/src/app/(app)/sources/page.tsx`, moving the Discovery page;
  - `apps/web/src/app/(app)/people/page.tsx`;
  - `apps/web/src/app/(app)/email/page.tsx`, plus `sequences`, `approvals`, `inbox` and `mailboxes`, each with a
    `page.tsx`;
  - `apps/web/src/app/(app)/crm/page.tsx`, plus `pipeline`, `meetings` and `tasks`, each with a `page.tsx`.
- Modify:
  - `apps/web/src/components/shell.tsx`;
  - `apps/web/src/components/nav-links.tsx`;
  - `apps/web/next.config.ts` (the redirects);
  - `apps/web/e2e/screens.spec.ts`.
- Delete:
  - `apps/web/src/lib/screens.ts`;
  - `apps/web/src/components/screen-placeholder.tsx`;
  - the old `discovery`, `campaigns`, `inbox` and `meetings` top-level pages.

  Replace their imports.

**Interfaces:**
- Produces, in `lib/nav.ts`:
  - `type NavItem = { key: string; label: string; href: string; group: 'home' | 'find' | 'work' | 'engage' |
    'measure' | 'settings'; job: string; children?: NavItem[]; placeholder?: 'email' | 'crm' | 'p6';
    todayHref?: (ws: string) => string; todayLabel?: string }`;
  - `NAV: NavItem[]`, exactly the Global Constraints tree;
  - `GROUP_LABELS = { find: 'Find', work: 'Work', engage: 'Engage', measure: 'Measure' }`;
  - `navItem(href): NavItem`;
  - `allRoutes(): string[]`, every href including children.
- Produces `PlaceholderPage({ item })`, the Global Constraints placeholder content.
- `NavLinks` receives the serialisable `NAV` (no functions) plus `counts: { review?: number }` and `footer:
  ReactNode`.

**Behaviour:**
- **Groups:** headings are uppercase `si-label`, in the order Find, Work, Engage, Measure. Home is at the top and
  Settings is in the footer.
- **Sub-items:** Email engine and CRM sub-items are indented, and show when the parent or one of its children is
  active (always on desktop).
- **Active item:** a sub-item is active on its exact route; a parent is active when any of its children is.
  `aria-current="page"` goes on the active item.
- **Phone:** see Global Constraints.

- [ ] **Step 1: Write failing e2e tests.**
  - **`screens.spec.ts`:** iterate `allRoutes()` plus `/briefs/new` at 1440 and 390 px. Each page loads without a
    console error and with no horizontal overflow.
  - **New `nav.spec.ts`:**
    - **Groups:** the sidebar shows the four group labels in order, and every top-level label in the tree.
    - **Sub-items:** on desktop, all seven Email engine and CRM sub-items are visible.
    - **Redirects:** `/discovery` lands on `/sources` with the Discovery content (the heading "Sources"). The same
      check covers `/campaigns` → `/email/sequences`, `/inbox` → `/email/inbox` and `/meetings` → `/crm/meetings`.
    - **Placeholders:** `/email/inbox` shows "Coming with the Email engine" and an `Open today's inbox` link to
      `/workspace/<ws>/inbox`. `/people` shows "Coming in P6".
    - **Phone:** at 390 px the nav is one horizontal row whose links are only the top-level items, and the page
      has no overflow.
    - **Review count:** seed one pending `review_item` with `sql()`. The sidebar shows a Review count of 1. With
      none, no count is shown.
- [ ] **Step 2: Confirm by reading** (CI runs it), and confirm `pnpm typecheck` fails on the missing `lib/nav.ts`.
- [ ] **Step 3: Implement.**
  - **Review count:** read in `shell.tsx` (server) through a cheap head count, using the same two tables as
    Global Constraints. The budget-meter footer comes in Task 3. Leave a slot.
  - **Sources page:** move the Discovery page body unchanged, apart from its heading and its metadata title
    ("Sources").
  - **Redirects:** in `next.config.ts` `redirects()`, all `permanent: true`.
- [ ] **Step 4: Run the gates.** Run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(web): journey-ordered sidebar, Email engine and CRM placeholders, route consolidation"`

### Task 2: Migration 0042 — Home and sidebar RPCs

**Files:**
- Create: `supabase/migrations/20261120000042_home.sql`
- Create: `supabase/tests/database/26_home.test.sql`

**Interfaces:**
- **Consumes:**
  - P3: `public.view_entities`, `public.estimate_column_run`, `app.filter_where`, `app.cell_state`;
  - P1: `app.period_spend`;
  - existing tables: `brief`, `brief_version`, `radar`, `account`, `verdict`, `score`, `claim`, `contact_point`,
    `review_item`, `verdict_review`, `reply`, `meeting`, `task`, `next_step`, `source_record`, `budget`.
- **Produces:**
  - the nine `public.home_*` RPCs in the Global Constraints Home table, each `(p_ws uuid, p_brief uuid default
    null) returns jsonb`. Each is SECURITY DEFINER with `search_path=''` and Viewer+, raising `42501` otherwise.
    A `p_brief` outside the workspace is treated as null (Review Focus 2);
  - `public.sidebar_counts(p_ws uuid) returns jsonb {review int}`;
  - `public.budget_meter(p_ws uuid) returns jsonb {spent_usd, cap_usd (null when no monthly cap), pct (null when no
    cap)}`.
- **Return shapes:** every RPC returns jsonb with exact key names, documented in a comment above each function. The
  web task will rely on them. Counts are never null; they are 0. Hrefs are relative paths using the Global
  Constraints routes.

- [ ] **Step 1: Write the failing pgTAP file.** Seed two workspaces and a Brief with a published version, radars
  (one active, one error), accounts with verdicts and scores, claims with quotes, contact points, review items, a
  reply, meetings, tasks due today, next steps, a budget with spend at 85%, and a P3 formula or extract column with
  one stale and one errored cell. Assert:
  - **Header:** `home_header` with the Brief has the right name, version and state; with null it has the workspace
    and Brief count. A foreign Brief id behaves as null.
  - **Journey:** `home_journey` has 6 steps in order, with the right `done` flags. `first_email` is never done.
  - **Metrics:** each `home_metrics` number matches the seed, and the spend's cap matches the budget.
  - **Needs you:** `home_needs_you` has the right review count, the replies-to-classify count, stale count 1
    with an estimate equal to `estimate_column_run(... 'stale' ...).cost_usd`, and errored count 1.
  - **Accounts to act on:** `home_accounts_to_act` returns at most 5, ordered by priority, only Confirmed/Likely
    and High/Medium. "Why now" is the quoted claim.
  - **What changed:** `home_changes` shows only the last 7 days, at most 10 items, newest first, and covers each
    kind.
  - **CRM:** `home_crm` has every stage with counts and the tasks due today.
  - **Sources:** `home_sources` lists the error radar first.
  - **Sidebar and budget:** `sidebar_counts` and `budget_meter` (pct 85; a workspace without a cap gives null cap
    and pct).
  - **Empty workspace (Review Focus 3):** every RPC returns zeros and empty arrays, with no nulls and no errors.
  - **Access (Review Focus 1):** a non-member gets `42501` from every RPC.
- [ ] **Step 2: Run it and see it fail.** Run `node scripts/check-pgtap.mjs`.
- [ ] **Step 3: Write the migration.** Reuse the P3 functions; do not copy their logic.
- [ ] **Step 4: Run the gates.** Run `pnpm db:check`. Expected: all suites pass.
- [ ] **Step 5: Commit.** `git commit -m "feat(db): Home and sidebar RPCs, one per card"`

### Task 3: Brief lens and budget meter in the shell

**Files:**
- Create:
  - `apps/web/src/lib/brief-lens.ts`;
  - `apps/web/src/components/brief-lens.tsx` (client selector);
  - `apps/web/src/components/budget-meter.tsx`;
  - `apps/web/src/app/actions/lens.ts`.
- Modify:
  - `apps/web/src/components/shell.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/page.tsx` (sets the cookie on open);
  - `apps/web/e2e/nav.spec.ts`.

**Interfaces:**
- **Consumes:** `public.budget_meter`, `public.sidebar_counts` (Task 2) and `NAV` (Task 1).
- **Produces:**
  - `getBriefLens(searchParams?: Record<string, string | string[] | undefined>): Promise<{ briefId: string | null;
    briefName: string | null; source: 'url' | 'cookie' | 'latest' | 'all' }>` in `lib/brief-lens.ts`
    (server-only). It applies the Global Constraints resolution order and verifies the Brief is in the current
    workspace and not archived;
  - `setBriefLens(formData)`, a server action that writes `si_brief = <ws>:<briefId|all>` for 90 days with
    `sameSite: 'lax'` and `httpOnly`;
  - `<BriefLens briefs={...} value={...} />`, which submits the server action and keeps the current path.

**Behaviour:**
- **Placement:** the lens sits at the top of the sidebar, under the brand. It shows "All Briefs" or the Brief name.
  It is reachable by keyboard and labelled "Brief lens".
- **Budget meter:** in the sidebar footer next to Settings, per Global Constraints.
- **Review count:** moves to `sidebar_counts`.
- **Opening a Brief:** opening `/briefs/<id>` sets the lens to that Brief.

- [ ] **Step 1: Write failing e2e tests** (in `nav.spec.ts`):
  - **Default:** with no cookie and two published Briefs, the lens shows the most recent one.
  - **Choosing:** picking "All Briefs" persists across navigation.
  - **Opening a Brief:** opening another Brief's page sets the lens to it.
  - **Bad URL value (Review Focus 2):** `?brief=<a foreign or archived id>` falls back without error.
  - **Two workspaces (Review Focus 1):** a user in two workspaces switches workspace, and the lens resolves within
    the new workspace (the old cookie value is ignored there).
  - **Budget meter:**
    - a seeded 85% spend shows `$<spent> / $<cap>` in mustard;
    - with no cap it shows `· no cap`;
    - it is hidden at 390 px, and its value is visible on `/settings`.
- [ ] **Step 2: Confirm by reading** (CI).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates.** Run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(web): Brief lens across pages and the always-visible budget meter"`

### Task 4: The new Home

**Files:**
- Modify: `apps/web/src/app/(app)/page.tsx` (rewrite; the H1 becomes "Home")
- Create: `apps/web/src/app/(app)/home/` with one component per card (e.g. `header-card.tsx`, `journey.tsx`,
  `metrics.tsx`, `needs-you.tsx`, `accounts-to-act.tsx`, `changes.tsx`, `email-card.tsx`, `crm-card.tsx`,
  `sources-card.tsx`) plus `card-error.tsx`. These are private components; keep them out of routing with a `_`
  folder or a `components` path, per Next conventions.
- Create: `apps/web/e2e/home.spec.ts`
- Modify: the screenshot spec (add Home, desktop and phone)

**Interfaces:**
- **Consumes:** the nine `home_*` RPCs (Task 2) and `getBriefLens` (Task 3).
- **Produces:** the Home page. Each card is an async server component inside its own `<Suspense>` with a skeleton
  fallback, wrapped so an RPC error renders `card-error`.

**Behaviour:**
- **Layout:** follow the mockup and spec §4:
  - header;
  - journey strip;
  - number tiles row;
  - two columns on desktop, Needs you plus Accounts to act on beside What changed plus the Email engine, CRM and
    Sources cards;
  - one column under 768 px.
- **Copy:**
  - plain language; numbers formatted with `Intl.NumberFormat('en')`;
  - money as `$12.40`;
  - each verdict change reads `Acme Energy: Likely → Confirmed · "quote…"`.
- **Empty workspace:** follow Global Constraints.

- [ ] **Step 1: Write failing e2e tests** in `home.spec.ts`. Seed with `sql()`, as `brief.spec.ts` does:
  - **Empty workspace (Review Focus 3):** only the journey strip and the "Describe who you sell to" card are shown.
  - **Populated workspace:** each card shows the seeded values, and each number's link points at its rows. Covers
    the header (name and version), the journey (done steps checked, "Comes with the Email engine" on the last),
    the tiles, Needs you (Review count, stale with its `$` estimate), the top account row with its verdict, band
    and why-now quote, a verdict-change line, the Email engine placeholder card, CRM stage counts and the error
    source listed first.
  - **Lens:** switching to "All Briefs" changes the header to the workspace form.
  - **One card fails (Review Focus 4):** revoke execute on `public.home_changes` from `authenticated` inside the
    test, then restore it in `finally`. That card shows "This card could not load", and the tiles still render.
  - **Screenshots:** Home populated at 1440 and 390 px, plus an empty Home at 390 px, with the overflow check
    (Review Focus 5).
- [ ] **Step 2: Confirm by reading** (CI).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates.** Run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(web): Home answers what needs me, what changed and what's next"`

---

**After Task 4:**
1. Open the PR as a draft early so CI runs.
2. Do the whole-branch review, then the independent completion audit.
3. The controller looks at every screenshot (sidebar, placeholders and Home, on desktop and phone) before the PR is
   marked ready.
4. The PR body states what stays for P5:
   - pinned views under Accounts;
   - the accounts table;
   - the "By verdict" route fold;
   - search and Ask.
