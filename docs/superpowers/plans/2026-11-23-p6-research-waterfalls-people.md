# P6 AI research, waterfalls and People Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development, lean process (memory
> `lean-build-process`):
> - sonnet builders, except the database, security and money tasks (opus);
> - a per-task review only for Tasks 1, 2 and 3;
> - one combined final review and completion audit at the end;
> - Playwright and screenshots through CI.
>
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** Columns can be filled by an AI research agent or by paid data providers, and people become a first-class
table that is always linked to an account.
- **AI research columns:** a column answers a plain-English question per account from its evidence and the web.
- **Work email:** found through a waterfall, LeadMagic → Findymail → Prospeo, and validated.
- **Company facts:** headcount, industry, HQ and location come from **People Data Labs (PDL)**.
- **Find people:** runs per account through PDL Person Search, and a person in another office is flagged.
- **People page:** the operating table over people.

**Architecture:**
- **Fill methods:** P3's `predicate.fill_method` already allows `provider` and `research`. This plan makes the P3
  column executor run them.
- **Providers:** P1 M7 has an `EnrichmentProvider` protocol and a Hunter-only waterfall. This plan replaces it with
  one adapter interface (`signal_core/providers/`).
  - Each adapter wraps one vendor HTTP API.
  - Keys come from environment variables.
  - A provider with no key reports "Not set up yet" and is skipped (memory `oauth-deferred-placeholders`).
- **Spend:**
  - every paid call reserves budget first (P1 S6 `BudgetGuard`) and settles at the real cost;
  - every call is billed to its run (P3 `usage_ledger.predicate_run_id`);
  - every provider attempt is recorded, so a waterfall cell can show which provider paid off.
- **People:**
  - they are `entity_type = 'person'` entities with a `works_at` link to the company (P1 M7);
  - P3 `view_entities` and P5 `account_table` gain a person mode, so the People page reuses the operating table.
- **AI research:** it uses the existing AI gateway (Capability.RESEARCH) and Brave search (P2 discovery).
  - The prompt is a new frozen file, `research_column/v1.md`.
  - The run's transcript (queries, pages read, answer and cited claim ids) is stored as the run's receipt.
  - Live evals need AI credit, which isn't available, so they are not run; the PR says so.

**Tech Stack:**
- Python 3.13, psycopg 3 async, Procrastinate, pydantic, httpx, pytest, `respx` for HTTP fixtures;
- Postgres / pgTAP on PGlite;
- Next.js App Router and React 19, with `@signal/ui`;
- Playwright through CI.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md):
- D3 (2–3 email providers + 1 firmographic provider, no marketplace) and D4 (find people per account, one
  provider);
- T8 (people match through the account entity; a location mismatch is flagged, never silently added);
- §8 (`alias_type` + email, phone; `contact_point` reused) and §9 (cost before action, failures visible);
- the status block: People has its own menu, and **every person is connected to an account** (no orphans; a person
  without an account goes to Review).

Page detail is in [05 §3.3 (AI research and waterfall columns) and §4.6 People](../../research/clay/05-ia-and-pages.md).
User decisions on 2026-11-22: **People Data Labs** for both firmographics and people search; the email waterfall is
LeadMagic → Findymail → Prospeo.

## Global Constraints

- **Branch:** `feat/research-waterfalls-people` from `main` (at `dcfa457` or later). One PR.
  - New migration: `supabase/migrations/20261123000044_research_providers_people.sql`. Never edit a released
    migration.
- **Providers** (one adapter each, in `packages/signal_core/src/signal_core/providers/`):

  | key | Vendor and endpoint | Env var | Used for | Cost per call (seeded `model_price`, editable) |
  |---|---|---|---|---|
  | `leadmagic` | LeadMagic email finder | `LEADMAGIC_API_KEY` | work email | $0.012 |
  | `findymail` | Findymail find email + verify | `FINDYMAIL_API_KEY` | work email, validation | $0.015 find / $0.004 verify |
  | `prospeo` | Prospeo email finder | `PROSPEO_API_KEY` | work email | $0.010 |
  | `pdl_company` | People Data Labs Company Enrichment | `PDL_API_KEY` | headcount, industry, HQ city/region/country, founded, website, LinkedIn | $0.10 |
  | `pdl_person` | People Data Labs Person Search | `PDL_API_KEY` | find people at an account by persona titles | $0.28 per person returned |

  - The implementer confirms each endpoint, request shape and response shape from the vendor's public API docs, and
    records the doc URL in the adapter docstring.
  - The prices are placeholders the owner edits in `model_price`. Estimates always read the table, never constants.
- **Adapter contract:**
  - `async lookup(input: ProviderInput) -> ProviderResult`, where the result is `{status: 'found' | 'not_found' |
    'error', value, confidence 0–100, raw_ref, cost_usd}`.
  - The adapter never raises for a vendor "not found". HTTP 4xx/5xx and timeouts give `status 'error'` with a short
    reason.
  - **Timeouts:** 15 s; one retry on 429/5xx with backoff.
  - **Secrets:** a key is never logged, never stored in a run transcript, and never sent to the web app.
- **Work-email waterfall:**
  - It tries the configured, keyed providers in order (`leadmagic → findymail → prospeo` by default).
  - It stops at the first `found` result that **passes validation**: Findymail verify, when Findymail is keyed;
    otherwise the finder's own confidence must be ≥ 80.
  - Each attempt is recorded as `predicate_run_attempt (run_id, provider, status, cost_usd, error, ordinal)`.
  - A cell shows `✓ <provider>`, `No data (tried 3)` or `Error`, and the panel lists the attempts.
  - **Never pay twice:** a person with a `valid` work email verified in the last 30 days is `skipped` with the reason
    "Verified on <date>".
- **Work emails as contacts:** stored in `contact_point` (type `email`, `value_encrypted`, `provider`, `status`
  `valid` / `invalid` / `unknown`) and through P1 `encrypt_secret`. The table shows a masked form (`j•••@acme.com`);
  the full value appears only in the cell panel, for Editor+.
- **Firmographic columns:** a `provider` column with `fill_config {"provider": "pdl_company", "field":
  "<dotted path>"}`.
  - One PDL call per account fills **every** PDL company column that is due on that account. The call is cached per
    account for 30 days in `provider_cache`, so a second column costs $0.
  - The location fields also write an `entity_alias` of type `name_country` and a new `site` alias (city, region,
    country) for the future map.
- **AI research columns:** `fill_method 'research'`, with `fill_config {"question": text 10–500 chars, "inputs":
  [column keys], "web": boolean, "max_pages": 1–5}`.
  - **Answer typing:** the answer is typed by the column (P3 result-vs-type rules). An answer the model can't
    support from evidence gives `no_data`, never a guess.
  - **Receipt:** `{queries, pages: [{url, title}], answer, cited_claim_ids, reasoning}`, stored in
    `predicate_run.inputs`.
  - **Evidence:** the derived claim's quote is the model's supporting sentence, and its source is the first page
    read. Research claims count as **evidence**, unlike formula claims; R5 in P5 excluded only `derived:formula`.
  - **Cost:** at most one gateway call plus `max_pages` fetches per account. The estimate is the gateway token
    price × (3,000 in, 600 out) + the Brave search price.
- **Find people** (per account, PDL Person Search):
  - **Input:** the account's domain and the lens Brief's personas (titles), limit 1–10 people (default 5).
  - **Each person returned:**
    - becomes a `person` entity with aliases `email` (when present), `profile` (LinkedIn URL) and `name_country`;
    - gets a `works_at` link to the account's company entity;
    - starts with `contact_point`s of status `unknown` until the waterfall runs.
  - **Location check:** the person's country (or city) is compared with the account's country and sites. A mismatch
    flags the person `location_mismatch = true`, with the reason "Possibly a different office (Mumbai vs Delhi)".
    They are still linked, but shown flagged.
  - **Duplicates:** a person already present (same `profile` or email alias) is attached, not duplicated (P1
    exact-match rule).
- **Every person linked to an account (status block):**
  - a person entity without a `works_at` link to a company that has an `account` in the workspace creates a
    `review_item` of type `unlinked_person`;
  - Review offers **Link to account…** (search) or **Dismiss**;
  - the People page never lists unlinked people; its empty state says how many wait in Review.
- **People page** (`/people`, replacing the P4 placeholder): the P5 operating table in person mode.
  - **System columns:** Name, Role (title), Account (+ site), Work email (waterfall cell), LinkedIn, Location flag,
    Last touch.
  - **Columns:** person-scoped predicate columns are allowed. Work email is a built-in column.
  - **Bulk:** Find work emails (cost), Export. Add to sequence stays disabled ("Arrives with the Email engine").
  - **Phone:** cards.
- **Account page:** a new **People** tab on `/workspace/<ws>/accounts/<id>` lists that account's people and has a
  **Find people** button that shows its cost first.
- **Add column** (P5): enable **AI research** (question box, inputs as column chips, web on/off, max pages) and
  **Work email (waterfall)** for person columns, plus the PDL company fields for account columns.
  - The 5-row preview runs only after an explicit "Preview on 5 accounts (est. $X)" confirm.
  - A waterfall column header expands into per-provider sub-columns showing ✓ / ✗ / Skipped.
- **Settings → Providers** (read-only status; keys are never entered in the UI):
  - one row per provider: "Not set up yet" / "Connected" / "Failing (last error, when)", the price per call, and
    this month's spend;
  - the waterfall order, editable by Admins;
  - stored in `workspace_settings.provider_order jsonb`.
- **Phone layout:** the top-bar search must not crowd the user menu at 390 px (P5 leftover). It collapses to an icon
  button that opens the search.
- **Gates:**
  - `pnpm db:check`;
  - `uv run pytest -q` and the pipeline e2e;
  - `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`;
  - `pnpm typecheck`, `pnpm lint` (0 errors) and `pnpm --filter web build`;
  - CI Playwright, with screenshots looked at.
- **No live vendor calls in tests:** every adapter test uses recorded fixtures through `respx`. A missing key never
  fails CI.

## Review Focus

1. **A vendor returns an email that fails verification.** The waterfall goes on to the next provider. The bad email
   is never stored as `valid`, and every attempt shows in the panel with its cost. *Test in Task 3.*
2. **Two people with the same name at different offices of one company.** Each is linked to the account. The one in
   the other city is flagged "Possibly a different office" and never silently merged. *Test in Task 4.*
3. **A key is missing, or wrong (401).** The column cell shows `Error: <provider> not set up` (or "rejected the
   key"), the run ends in `error`, nothing is charged, and Settings → Providers shows "Failing". *Tests in Tasks 1
   and 3.*
4. **Budget runs out halfway through a waterfall.** The run stops with `error 'budget'`, no further provider is
   called, and the attempts so far are billed and listed. *Test in Task 3.*
5. **PII exposure.** A Viewer and the CSV export see masked emails. Full emails never appear in logs, transcripts or
   the network response of the table RPC. *Tests in Tasks 2 and 5.*

---

### Task 1: Provider adapters and the provider registry (opus, reviewed)

**Files:**
- Create: `packages/signal_core/src/signal_core/providers/__init__.py` (registry: `PROVIDERS`, `configured()`,
  `status()`)
- Create: `base.py` (`ProviderInput`, `ProviderResult`, `Provider` protocol, HTTP helper with timeout, retry and
  redaction)
- Create: `leadmagic.py`, `findymail.py` (find + `verify`), `prospeo.py`, `pdl.py` (`PdlCompany`, `PdlPerson`)
- Create: `packages/signal_core/tests/providers/` with recorded JSON fixtures per vendor (found, not found, 401, 429,
  500, timeout)
- Modify: `packages/signal_core/src/signal_core/config.py` (the five env vars as `SecretStr | None`) and
  `.env.example`
- Modify: `packages/signal_core/src/signal_core/modules/m07_enrichment.py` (use the registry; Hunter stays as an
  optional extra provider only if already configured)

**Interfaces (produced):**
- `Provider.key: str`
- `Provider.kind: Literal['email_finder', 'email_verifier', 'company', 'person_search']`
- `async Provider.lookup(ProviderInput) -> ProviderResult`
- `providers.configured(kind) -> list[Provider]` (keyed only, in workspace order)
- `providers.status() -> list[{key, kind, configured, last_error, last_error_at}]`

**Steps:**
- [ ] Write a failing test per adapter from its fixtures. Cover: found, not found, 401 (`error`, "rejected the
  key"), 429 then success (one retry), 500 twice (`error`), timeout (`error`), and the key never appearing in any
  log record (capture structlog).
- [ ] Implement the adapters from each vendor's API docs, with the doc URL in the docstring.
- [ ] Run `uv run pytest packages/signal_core/tests/providers -q`, then the full suite, ruff and mypy.
- [ ] Commit with `git commit -m "feat(providers): LeadMagic, Findymail, Prospeo and People Data Labs adapters with one contract"`.

### Task 2: Migration 0044 — attempts, provider cache, people and Review (opus, reviewed)

**Files:**
- Create: `supabase/migrations/20261123000044_research_providers_people.sql`
- Create: `supabase/tests/database/28_research_providers_people.test.sql`

**Interfaces (produced; jsonb shapes documented above each function):**
- **Attempts:** a `predicate_run_attempt` table with RLS read Viewer+, and `app.record_attempt(...)` (service role).
- **Provider cache:** a `provider_cache (workspace_id, provider, entity_id, payload_ref, fetched_at, expires_at)`
  table, with `app.cached_provider_payload(...)` / `app.store_provider_payload(...)`. The payload is stored as jsonb;
  no PII beyond what the vendor returned for a company.
- **Aliases:** `alias_type` gains `email`, `phone` and `site` (enum add values).
- **Person flags:** `entity_flag (entity_id, workspace_id, kind 'location_mismatch', reason text)`.
- **People in views:** `public.account_table` and `public.view_entities` accept `p_entity_type 'person'` and then
  return person rows:
  - each row carries the account it works at;
  - masked email for the table;
  - `location_mismatch`.
- **`public.person_email(p_ws, p_person)`:** Editor+; returns the decrypted work email for the panel.
  - The decryption uses the P1 crypto path, which runs in the worker. If the database cannot decrypt, the RPC
    returns a `contact_point` id and the web calls a worker-backed server action. Pick one and document it.
- **Unlinked people:** a person without an account link raises `app.queue_unlinked_person(p_person)` →
  `review_item` type `unlinked_person`. `public.link_person_to_account(p_ws, p_person, p_account)` is Editor+.
  `resolve_review_item` gains Link and Dismiss for this type.
- **`workspace_settings.provider_order jsonb`**, default `["leadmagic", "findymail", "prospeo"]`, with
  `public.set_provider_order(p_ws, p_order)` (Admin).
- **`public.provider_status(p_ws)`:** per provider, this month's spend and the last error from attempts. The key
  status is merged in by the web from a worker endpoint; see Task 7.
- **Seeded `model_price` rows** for the five providers.

**Steps:**
- [ ] Write a failing pgTAP suite. Cover:
  - the shapes;
  - person rows in `account_table`, with masked email only;
  - Viewer `42501` on `person_email` and the link/order writes;
  - an unlinked person raising a Review item;
  - Link resolving it;
  - attempts summed into the run cost;
  - the cache hit returning a payload within 30 days and nothing after it;
  - the provider order validated against known keys.
- [ ] Write the migration, re-appliable (`create or replace` / `if not exists`).
- [ ] Run `pnpm db:check`.
- [ ] Commit with `git commit -m "feat(db): provider attempts and cache, people in the operating table, unlinked people to Review"`.

### Task 3: Executor — provider and research fill methods (opus, reviewed)

**Files:**
- Modify: `packages/signal_core/src/signal_core/column_runtime.py` (dispatch on `fill_method`)
- Create: `packages/signal_core/src/signal_core/fill/provider_fill.py` and `fill/research_fill.py`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/research_column/v1.md`, plus its sha in
  `LOCK.json`
- Modify: the worker task registration, if new job types are needed
- Tests: `packages/signal_core/tests/test_provider_fill.py`, `test_research_fill.py`, and the pipeline e2e

**Behaviour:** Global Constraints for the work-email waterfall, firmographic columns and AI research columns.
Every paid call goes through `BudgetGuard.reserve` → `settle` / `release`, with `CallContext.predicate_run_id` set.

**Steps:**
- [ ] Write failing tests, all with fake providers and a fake gateway:
  - **Review Focus 1:** the waterfall stops at the first validated hit; a failed verify moves on.
  - **Review Focus 4:** budget paused mid-waterfall leaves the run in `error 'budget'`, with earlier attempts billed.
  - **Review Focus 3:** a missing or 401 key gives an `error` cell and no charge.
  - **Never pay twice:** a person verified in the last 30 days is skipped.
  - **One PDL call:** two PDL columns on one account make a single call, and the second column hits the cache at $0.
  - **Research, answered:** writes a typed claim with its quote and source; the receipt has no API key.
  - **Research, unsupported:** gives `no_data`.
  - **Research, wrong type:** a type mismatch is an `error`, using the P3 message.
  - **e2e:** a research column and a PDL column fill on a seeded account through the real executor, with fakes at the
    HTTP and gateway edges.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(runs): provider waterfalls and AI research columns, every attempt billed to its run"`.

### Task 4: Find people per account

**Files:**
- Create: `packages/signal_core/src/signal_core/people/find_people.py`
- Modify: the worker (a job `people.find`, lock `people:<account_id>`, kill-switch row in 0044)
- Modify: 0044 (`public.estimate_find_people(p_ws, p_account, p_limit)`, `public.request_find_people(...)` — Editor+,
  budget-checked with the P3 55000 message)
- Tests: `test_find_people.py` and the pipeline e2e

**Behaviour:** Global Constraints for Find people. Personas come from the lens Brief's `config.personas`
(buyer/user/influencer titles).

**Steps:**
- [ ] Write failing tests:
  - **Linked people:** 3 people returned are linked to the account and existing people are attached, not
    duplicated.
  - **Review Focus 2:** the other-office person is flagged.
  - **Unlinked:** a result whose company can't be matched to an account goes to Review as `unlinked_person`.
  - **Costs:** the estimate equals limit × the person price, and the budget refusal shows "first N".
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(people): find people per account with persona titles and a location check"`.

### Task 5: The People page and the account People tab

**Files:**
- Create: `apps/web/src/app/(app)/people/page.tsx`, replacing the P4 placeholder and reusing the P5 `_table`
  components in person mode
- Modify: `apps/web/src/app/(app)/workspace/[wsid]/accounts/[aid]/page.tsx` (People tab and Find people with cost)
- Create: `apps/web/src/app/actions/people.ts`
- Modify: the Review page (`unlinked_person` card with Link and Dismiss)
- Modify: `apps/web/src/lib/nav.ts` (People no longer a placeholder)
- Create: `apps/web/e2e/people.spec.ts`

**Steps:**
- [ ] Write failing e2e tests (CI):
  - **Rows:** people rows show their account and a masked email.
  - **Location flag:** a flagged person shows "Possibly a different office".
  - **Panel:** an Editor sees the full email in the panel; a Viewer sees only the masked one (Review Focus 5).
  - **Account tab:** Find people on the account page shows its cost, then the people appear.
  - **Review:** an unlinked person can be linked from Review.
  - **Empty state:** says how many wait in Review.
  - **Screenshots:** `people-table.png`, `people-phone.png`, `account-people-tab.png`.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): People page and account People tab, every person linked to an account"`.

### Task 6: Add column — AI research, waterfall and company fields

**Files:**
- Modify: `_table/add-column.tsx`, `column-menu.tsx`, `cell.tsx` (the waterfall sub-columns), `panel.tsx` (the
  attempts list and the research receipt)
- Modify: `apps/web/src/lib/briefs.ts` (column-spec fill configs) and P2's SQL column-spec validation (0044 `create or
  replace` of `app.validate_brief_columns` for the `research` and `provider` fill configs)
- Modify: `accounts.spec.ts`

**Steps:**
- [ ] Write failing tests (pgTAP for the validation, e2e for the UI):
  - **AI research:** an "AI research" column with a question and inputs saves, and its preview asks for a cost
    confirm.
  - **Waterfall:** a "Work email (waterfall)" column header expands to 3 provider sub-columns.
  - **Panel:** lists the attempts with cost, or for a research cell shows the receipt's pages and answer.
  - **Screenshots:** `add-column-research.png`, `waterfall-subcolumns.png`.
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(web): AI research and waterfall columns in Add column, attempts and receipts in the panel"`.

### Task 7: Settings → Providers, and the phone top bar

**Files:**
- Create: `apps/web/src/app/(app)/settings/providers.tsx`
- Modify: `settings/page.tsx`
- Create: `services/worker/src/worker/health.py` (an extension: `/providers/status` on the worker's health server
  returns `providers.status()` without secrets, protected by a shared `WORKER_STATUS_TOKEN`)
- Modify: `apps/web/src/components/search-box.tsx` and `shell.tsx` (phone: the search collapses to an icon)
- Create: `apps/web/e2e/settings.spec.ts`

**Steps:**
- [ ] Write failing tests:
  - **Providers section:** shows "Not set up yet" with no keys in CI; with a seeded attempt error it shows "Failing".
  - **Order:** an Admin can reorder the waterfall, and a Viewer sees it read-only.
  - **Phone search:** at 390 px the top bar has no overlap (the search icon and the user menu bounding boxes don't
    intersect).
  - **Worker endpoint:** refuses without the token and never returns key material (pytest).
- [ ] Implement.
- [ ] Run the gates.
- [ ] Commit with `git commit -m "feat(settings): provider status and waterfall order; phone top bar keeps search out of the way"`.

---

**After Task 7:**
1. Open the PR as a draft early so CI runs.
2. Run one combined final review and completion audit (opus) and one fix wave.
3. The controller looks at every screenshot.
4. The PR body states that live vendor calls and AI evals were not run (no keys or credit in CI), and lists the env
   vars the owner must set on the Railway worker: `LEADMAGIC_API_KEY`, `FINDYMAIL_API_KEY`, `PROSPEO_API_KEY`,
   `PDL_API_KEY` and `WORKER_STATUS_TOKEN` (also on web).
