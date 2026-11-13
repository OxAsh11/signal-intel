# Discovery Breadth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the M2 V1 discovery gaps:
- five Brave-backed radar types;
- the lexicon pre-filter;
- per-radar and per-query yield;
- targeted search for Unverified entities;
- radars created from a Brief's chosen sources.

**Architecture:**
- Migration 0033 adds columns, the targeted-search table and the RPCs.
- One `BraveSearchConnector(kind)` serves five `connector_type`s. Its HTTP calls are injectable for tests.
- Prompt `radar_queries` v2 returns queries plus a lexicon.
- `judge_relevance` applies the lexicon before any AI call.
- An hourly SQL function recomputes yield.
- The worker gets fetch tasks, a targeted-search task, provisioning in the publish dispatch, and a yield cron.

**Tech Stack:** Postgres/pgTAP (Supabase), Python 3.12 (psycopg, aiohttp, procrastinate, pydantic), Next.js (server components + server actions).

**Spec:** `docs/superpowers/specs/2026-11-13-discovery-breadth-design.md`

## Global Constraints

- **Migration:** `supabase/migrations/20261113000033_discovery_breadth.sql`. Every new function is `security definer set search_path = ''`, with execute revoked from public/anon/authenticated and granted explicitly.
- **New connector_types:** `registry`, `tender`, `event`, `directory`, `social_post`.
- **Brave:**
  - API: `GET https://api.search.brave.com/res/v1/web/search`, header `X-Subscription-Token`.
  - Params: `q`, `count` (max 20), `freshness`.
  - Results: `web.results[].{url,title,description,extra_snippets}`.
- **Env var:** `BRAVE_SEARCH_API_KEY`. Settings field `brave_search_api_key: SecretStr | None`.
- **Price row:** `model_price ('brave-search', 'brave', 'fetch', 5000)`, which is $0.005 per call.
- **Status values:**
  - Pre-filtered record: `extraction_status='irrelevant'`, `status_detail='prefilter'`.
  - `content_kind` is in (`full`, `snippet`).
- **Default social sites** (spec, verbatim): `linkedin.com`, `x.com`, `twitter.com`, `facebook.com`, `instagram.com`, `reddit.com`, `youtube.com`, `threads.net`, `mastodon.social`.
- **Error copy:**
  - Missing key: "Brave Search is not set up yet".
  - Throttle: once per (brief, entity) per 30 days.
- **Checks:** `pnpm db:check`, `pnpm py:check`, `pnpm lint typecheck test build` and CI stay green.

## Review Focus

1. **A radar's queries regenerated on republish.** Per-query counts and user queries survive, and a user-edited lexicon is not overwritten. *(Task 1 pgTAP, Task 4 pytest.)*
2. **A Brave result URL on a private IP.** It is dropped, never stored as a snippet. *(Task 3.)*
3. **A Brief republished repeatedly.** `provision_radars` never creates a second radar of the same type, and never resurrects a radar the user removed (paused). *(Task 1.)*
4. **Lexicon matching with accents or case** ("Ausschreibung" vs "AUSSCHREIBUNG", "société"). Casefold matching works; an empty lexicon passes everything. *(Task 5.)*
5. **A missing or invalid Brave key.** The job ends without retry, the radar shows "Brave Search is not set up yet", and no budget stays reserved. *(Task 3, Task 6.)*

---

### Task 1: Migration 0033 + pgTAP suite 20

**Files:**
- Create: `supabase/migrations/20261113000033_discovery_breadth.sql`
- Create: `supabase/tests/database/20_discovery_breadth.test.sql`

**Interfaces:**
- **Produces SQL:**
  - `app.create_source_record(p_org uuid, p_workspace uuid, p_connector_id text, p_source_type text, p_url text, p_fetched_at timestamptz, p_content_hash text, p_payload_ref text, p_language text, p_content text default null, p_radar uuid default null, p_radar_query uuid default null, p_content_kind text default 'full') returns uuid`. The old 10-arg version is dropped.
  - `app.upsert_radar_queries(p_radar uuid, p_queries jsonb, p_lexicon text[] default null) returns void`. The old 2-arg version is dropped.
  - `app.compute_radar_yield() returns int`: the number of radars updated.
  - `app.provision_radars(p_brief uuid) returns int`: the number created.
  - `app.brief_source_connector(p_source text) returns text`: immutable; null when unmapped.
  - `app.claim_targeted_search(p_brief uuid, p_entity uuid) returns boolean`.
  - `public.add_radar_query(p_radar uuid, p_text text, p_language text default 'en') returns uuid`.
  - `public.remove_radar_query(p_query uuid) returns void`.
  - `public.update_radar_lexicon(p_radar uuid, p_terms text[]) returns void`.
- **Produces columns:**
  - `radar.lexicon text[]`, `radar.lexicon_edited boolean`, `radar.prefiltered_count int`;
  - `radar_query.record_count`, `radar_query.prefiltered_count`, `radar_query.yield_count`;
  - `source_record.radar_id`, `source_record.radar_query_id`, `source_record.content_kind`;
  - table `targeted_search(id, org_id, workspace_id, brief_id, entity_id, searched_at, unique (brief_id, entity_id))`.

- [ ] **Step 1: Write suite 20.** Copy the fixture style of suite 19 (org, Editor J, Viewer K, other org B, brief + published version via `start_pilot` or a direct insert as service role). Tests:
  - `has_column` for each new column, and `has_table('targeted_search')`;
  - the `connector_type` check accepts `'tender'` and rejects `'foo'` (`throws_ok … '23514'`).
  - `create_source_record` with `p_radar`/`p_radar_query`/`'snippet'` stores them; `p_content_kind 'bogus'` throws `23514`.
  - **Upsert history:**
    1. Seed a generated query 'q1' plus a user query 'u1', and set `record_count = 3` on q1.
    2. `upsert_radar_queries(radar, '[{"query_text":"q1"},{"query_text":"q2"}]', '{a,b}')`.
    3. q1 keeps the same id and `record_count = 3`; q2 is added; u1 is kept; `lexicon = '{a,b}'`.
    4. With `lexicon_edited = true`, a second upsert with `'{c}'` leaves `'{a,b}'`.
  - **Editor RPCs:**
    - as J: `add_radar_query` returns an id with `source = 'user'`; `update_radar_lexicon(radar, '{x, y}')` sets `lexicon_edited`; `remove_radar_query` deletes;
    - as K: each throws `42501`;
    - an audit row exists for `update_radar_lexicon`.
  - **Mapping:** `brief_source_connector('tenders_procurement') = 'tender'`, `('public_social') = 'social_post'`, `('company_websites') = 'website'`, `('nope') is null`.
  - **Provisioning:**
    - the brief's draft and current version have sources `{news_publications, tenders_procurement, company_websites}`;
    - the first `provision_radars` returns 2 (news, tender; website skipped);
    - the second returns 0;
    - after pausing the tender radar, a third returns 0.
  - **Yield:**
    1. Insert a source_record with radar and query, and an active claim on entity E.
    2. Insert a verdict Confirmed for E on the brief's current version.
    3. Insert a second record with `irrelevant`/`prefilter`.
    4. `compute_radar_yield()`: the radar has `record_count = 2`, `prefiltered_count = 1` and `yield_count = 1`; the query has `yield_count = 1`.
  - `update_radar_cursor(radar, '{}', 5, 5)` leaves `yield_count` unchanged.
  - **Throttle:** `claim_targeted_search(b, e)` returns true then false; after `update targeted_search set searched_at = now() - interval '31 days'` it returns true.
  - **Permissions:** `authenticated` cannot execute `app.compute_radar_yield`, `app.provision_radars` or `app.claim_targeted_search` (`throws_ok … '42501'`).
  - The `job_type_config` rows for all 7 new job types exist.
- [ ] **Step 2: Run** `pnpm db:check`. Expected: suite 20 FAILs (missing columns/functions); suites 00–19 pass.
- [ ] **Step 3: Write the migration.** Specifics the tests don't pin:
  - `source_record.content_kind text not null default 'full' check (content_kind in ('full','snippet'))`.
  - **FKs:** `radar_id → radar on delete set null`, `radar_query_id → radar_query on delete set null`. `forbid_update_except` must let the FK `set null` through, so add `'radar_query_id','radar_id'` to the trigger's allowed list by recreating the trigger.
  - **Index:** `source_record (radar_id)` and `(radar_query_id)`.
  - **Yield definition** (spec D10): a record yields when `exists (select 1 from claim c join verdict v on v.entity_id = app.canonical_entity(c.entity_id) join brief b on b.current_version_id = v.brief_version_id where c.source_record_id = sr.id and c.status = 'active' and b.id = radar.brief_id and v.value in ('Confirmed','Likely'))`. `record_count` and `prefiltered_count` are recomputed from source_record, not accumulated.
  - `update_radar_cursor` keeps its signature and adds `p_fetched` to `record_count` only.
  - `provision_radars` creates radars named after the label: `News & publications`, `Public registries & permits`, `Tenders & procurement`, `Events`, `Directories & partner lists`, `Public social posts`. It writes no outbox row: the publish dispatch generates queries for active radars right after.
  - `add_radar_query`, `remove_radar_query` and `update_radar_lexicon` check `has_workspace_role(…,'Editor')` and call `app.audit` as the existing Editor RPCs do. Terms are trimmed, deduplicated, emptied entries dropped, and capped at 50.
  - `create_radar` is re-created to reject `config.sites` unless it is an array of ≤20 strings matching `^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$` (errcode `22023`).
  - Seed the `model_price` row and the `job_type_config` rows: `source.fetch_registry`, `source.fetch_tender`, `source.fetch_event`, `source.fetch_directory`, `source.fetch_social_post`, `source.targeted_search`, `radar.yield_compute`.
  - `targeted_search`: RLS read for workspace Viewer.
- [ ] **Step 4: Run** `pnpm db:check`. Expected: 21/21 suites pass.
- [ ] **Step 5: Commit** `feat(db): discovery breadth schema, yield and provisioning (0033)`.

### Task 2: Attribution through the Python write path

**Files:**
- Modify: `packages/signal_core/src/signal_core/connectors/__init__.py`, `modules/m02_radar.py`, `config.py`
- Test: `packages/signal_core/tests/test_connectors.py` (append), `services/worker/tests/test_pipeline_e2e.py` (later in Task 6)

**Interfaces:**
- **Produces:**
  - `SourcePayload` gains `query: str | None = None` and `content_kind: Literal["full","snippet"] = "full"`, both with defaults so existing connectors are unchanged.
  - `create_source_records(pool, org_id, workspace_id, connector_id, source_type, payloads, *, radar_id: UUID | None = None, query_ids: dict[str, UUID] | None = None) -> int`.
  - `RadarQuerySet` gains `lexicon: list[str] = []`.
  - `upsert_radar_queries(pool, radar_id, query_set)` passes `query_set.lexicon or None` as `p_lexicon`.
  - `load_query_ids(pool, radar_id) -> dict[str, UUID]` maps `query_text` to id.
  - `Settings.brave_search_api_key: SecretStr | None = None`.

- [ ] **Step 1: Test** `test_source_payload_defaults_keep_old_connectors`: building a `SourcePayload` with the six existing args gives `query is None` and `content_kind == "full"`. Run it; it FAILs with an AttributeError.
- [ ] **Step 2: Implement the dataclass fields, settings, `RadarQuerySet.lexicon`, the `create_source_records` keyword args, and `load_query_ids`.** `create_source_records` passes `radar_id` and `query_ids.get(payload.query)` and `payload.content_kind` as the 3 new SQL args.
- [ ] **Step 3: Run** `uv run pytest packages/signal_core/tests/test_connectors.py`. Expected: PASS.
- [ ] **Step 4: Commit** `feat(radar): carry query attribution and content kind to source records`.

### Task 3: BraveSearchConnector

**Files:**
- Create: `packages/signal_core/src/signal_core/connectors/brave.py`
- Modify: `connectors/website.py` (expose `strip_html` publicly, keeping the `_strip_html` alias)
- Test: `packages/signal_core/tests/test_brave_connector.py`

**Interfaces:**
- **Produces:**
  - `BRAVE_KINDS: dict[str, str]` maps connector_type to source_type: `{"registry":"registry","tender":"tender","event":"event","directory":"directory","social_post":"social"}`.
  - `class SearchNotConfigured(Exception)`.
  - `SearchFn = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]`: `(query, params) -> brave json`. It raises `SearchNotConfigured` on 401/403 and `BraveError` on 429/5xx.
  - `PageFn = Callable[[str], Awaitable[bytes | None]]`: HTML bytes, or None on a non-2xx/non-HTML/error response.
  - `class BraveSearchConnector`, with `__init__(self, kind: str, *, api_key: str | None, search: SearchFn | None = None, fetch_page: PageFn | None = None, meter: SearchMeter | None = None)`, `source_type = BRAVE_KINDS[kind]`, and `async fetch(config, queries, cursor, guard, limit) -> (payloads, next_cursor)`.
  - `class SearchMeter(Protocol)`: `async reserve(n_calls: int) -> object | None` and `async settle(token, calls: int) -> None`. A `PgSearchMeter(pool, ctx)` implementation reads the price from model_price and uses `app.reserve_budget` / `app.settle_reservation` like `m07_enrichment._reserve/_settle`. Unit is `fetch`, provider `brave`, model `brave-search`.
  - `def build_query(query: str, kind: str, sites: list[str] | None) -> str`.
  - `def freshness(cursor: dict, today: date) -> str`.
  - `async def targeted_search(connector, name: str, terms: list[str], guard) -> list[SourcePayload]`: 5 results, query `f'"{name}" ' + " ".join(terms[:3])`.

- [ ] **Step 1: Write the tests.** Use fake `search`/`fetch_page` callables and a `FetchGuard` subclass whose `check` is scripted per URL.
  - `test_freshness_first_run_is_past_year`: `freshness({}, date(2026,11,13)) == "py"`, and `freshness({"since":"2026-11-01"}, date(2026,11,13)) == "2026-11-01to2026-11-13"`.
  - `test_social_query_adds_default_sites`: `build_query("hotel opening", "social_post", None)` contains `"site:linkedin.com OR site:x.com"` and ends with `")"`; `build_query("q","tender",["ted.europa.eu"]) == "q (site:ted.europa.eu)"`; `build_query("q","tender",None) == "q"`.
  - `test_fetch_full_page_text`: one result → one payload with `content_kind == "full"`, `query == "q1"`, and raw containing the stripped page text.
  - `test_robots_blocked_keeps_snippet`: the guard raises `FetchDisallowed` → one payload with `content_kind == "snippet"` whose raw contains the title, description and every extra snippet.
  - `test_page_error_keeps_snippet`: `fetch_page` returns None → snippet.
  - `test_private_ip_result_dropped`: the guard raises `FetchBlocked` → 0 payloads.
  - `test_cursor_advances_to_today`: `next_cursor == {"since": today.isoformat()}`.
  - `test_missing_key_raises_before_any_call`: with `api_key=None`, `fetch` raises `SearchNotConfigured`, the search fake is never called, and `meter.reserve` is never called.
  - `test_meter_reserves_per_query_and_settles_actual`: 3 queries with a limit that stops after 2 → `reserve(3)`, `settle(token, 2)`.
  - `test_search_error_releases_reservation`: search raises `BraveError` → `settle(token, calls_made)` was called before the error propagates.
  - `test_duplicate_urls_across_queries_kept_once`.
  - `test_fake_mode_returns_fixtures_without_network`: `config={"fake": True}` → 2 payloads, one of them a snippet, with the queries attributed.
- [ ] **Step 2: Run** `uv run pytest packages/signal_core/tests/test_brave_connector.py`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `brave.py`.**
  - The default `search` uses aiohttp with a 20 s timeout, `count=min(20, remaining)`, `freshness`, and the `X-Subscription-Token` header.
  - The default `fetch_page` does a GET with a 15 s timeout and returns the body only when the status is 2xx and the content-type contains `html`, max 2 MB.
  - Raw for a full page = `title\n\n` + stripped text. Raw for a snippet = `title\n\ndescription\n\n` + the extra snippets joined.
  - The content hash covers the URL + raw.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(connectors): Brave Search connector for five radar types`.

### Task 4: Prompt v2 (queries + lexicon), brief_intake v2

**Files:**
- Create: `ai_gateway/prompts/radar_queries/v2.md`, `ai_gateway/prompts/brief_intake/v2.md`
- Modify: `ai_gateway/prompts/LOCK.json`, `modules/m02_radar_ai.py` (`PROMPT_VERSION = 2`), `modules/m01_brief.py` (brief_intake version 2), `apps/web/src/app/(app)/briefs/[id]/brief-editor.tsx` (`RADAR_TYPES` + `'public_social'`)
- Test: `packages/signal_core/tests/test_m02_radar_ai.py` (new)

**Interfaces:**
- **Consumes:** `RadarQuerySet.lexicon` (Task 2).
- **Produces:** `generate_radar_queries(...)`, unchanged signature, whose result carries `lexicon`. The prompt variables add `geographies` (comma-joined from the brief ICP); `generate_queries_job` passes them.

- [ ] **Step 1: Write the tests.**
  - `test_radar_queries_v2_renders_all_kinds`: `load_prompt("radar_queries", 2)` renders with every variable; the system text names each of `registry`, `tender`, `event`, `directory`, `social_post`, plus `lexicon`.
  - `test_generate_returns_lexicon`: a fake gateway returns `{"queries":[…],"lexicon":["tender","Ausschreibung"]}` → `result.lexicon == ["tender","Ausschreibung"]`.
  - `test_prompt_lock_matches`: the existing lock test covers the new files once LOCK.json is updated.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Write v2.** Rules per type, from spec D4: tenders "contract award" / "procurement notice"; events "exhibitor list" / "speakers"; directories "certified partners" / "reseller"; registries "registration" / "permit granted"; social: the plain phrasing people post. Also: "lexicon: 10–30 short terms or phrases whose presence marks a relevant record, including local-language variants for {geographies}". brief_intake v2 = v1 with `public_social` added to the list. Update LOCK.json with `file_sha256`.
- [ ] **Step 4: Run** `uv run pytest packages/signal_core`. Expected: PASS, including the lock test.
- [ ] **Step 5: Commit** `feat(prompts): radar queries v2 with lexicon; brief intake lists public social`.

### Task 5: Lexicon pre-filter

**Files:**
- Modify: `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Test: `packages/signal_core/tests/test_m03_extraction.py` (append)

**Interfaces:**
- **Produces:** `def passes_lexicon(text: str, lexicon: list[str]) -> bool`. `judge_relevance` loads `radar.lexicon` via `source_record.radar_id`. When the check fails, it calls `_update_status(pool, id, "irrelevant", "prefilter")`, returns False, and makes no gateway call.

- [ ] **Step 1: Write the tests.**
  - `test_passes_lexicon_casefold`: `passes_lexicon("AUSSCHREIBUNG für Hotels", ["ausschreibung"])` is true; `passes_lexicon("Société Générale", ["société"])` is true; `passes_lexicon("nothing here", ["tender"])` is false; `passes_lexicon("x", [])` is true.
  - `test_judge_relevance_prefilter_skips_ai`: uses the module's existing fake-pool pattern; the gateway fake records calls; afterwards the status is `('irrelevant','prefilter')` and the gateway has 0 calls.
- [ ] **Step 2: Run them.** Expected: FAIL.
- [ ] **Step 3: Implement.** Matching is `unicodedata.normalize("NFKC", s).casefold()` substring.
- [ ] **Step 4: Run** `uv run pytest packages/signal_core/tests/test_m03_extraction.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(extraction): lexicon pre-filter before the relevance judge`.

### Task 6: Worker wiring

**Files:**
- Modify: `services/worker/src/worker/app.py`, `packages/signal_core/src/signal_core/jobs.py`, `packages/signal_core/src/signal_core/modules/m05_verdict.py`
- Test: `services/worker/tests/test_worker.py`, `services/worker/tests/test_pipeline_e2e.py`, `packages/signal_core/tests/test_jobs.py`

**Interfaces:**
- **Consumes:** Tasks 1–5.
- **Produces:**
  - Tasks `source.fetch_registry|tender|event|directory|social_post`: queue `fetch`, retry 3, kwargs `radar_id, org_id, workspace_id, brief_id, approval_id=None`. The shared body is `_fetch_brave(kind, ...)`.
  - `source.targeted_search(entity_id, brief_id, workspace_id, approval_id=None)`: queue `fetch`, retry 2.
  - Periodic `radar.yield_compute` at cron `45 * * * *`.
  - `guarded` treats `SearchNotConfigured` like `AiNotConfigured`.
  - `compute_verdict` gets a module hook `targeted_search_job = None`, set by the app like `extract_relevance_judge_job`. On an Unverified result it defers with `queueing_lock=f"ts:{brief_id}:{entity_id}"`.

- [ ] **Step 1: Write the tests.**
  - `test_jobs.py::test_search_not_configured_ends_cleanly`: a job raising `SearchNotConfigured` returns None.
  - `test_worker.py`:
    - `test_brave_fetch_tasks_registered`: all 5 names plus `source.targeted_search` and `radar.yield_compute` are in `app.tasks`;
    - `test_version_published_provisions_radars`: the dispatch calls `app.provision_radars` before loading active radars (fake pool records its SQL);
    - `rederive_only` does not provision.
  - `test_pipeline_e2e.py`, a new step on the real schema:
    1. publish a brief with `tenders_procurement`;
    2. a radar of type `tender` exists;
    3. set `config={"fake":true}` and lexicon `["fake tender"]`;
    4. run `source.fetch_tender` → 2 records with `radar_id` and `radar_query_id`, one `content_kind='snippet'`;
    5. the relevance step pre-filters the record lacking the term (`status_detail='prefilter'`);
    6. `radar.yield_compute` runs without error and `prefiltered_count = 1`.
- [ ] **Step 2: Run** `uv run pytest services/worker packages/signal_core/tests/test_jobs.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
  - `_fetch_brave`:
    1. Load the radar, its queries and the query ids.
    2. Build the `PgSearchMeter` with `CallContext(org, ws, brief, None, f"source.fetch_{kind}", {radar_id…, approval_id})`.
    3. Run the connector.
    4. Call `create_source_records(..., radar_id=…, query_ids=…)` and `_update_cursor`.
  - On `SearchNotConfigured`, call `app.mark_radar_error(radar, 'Brave Search is not set up yet')`, then re-raise so `guarded` ends the job.
  - Fake mode (`config.fake`) needs no key.
  - Targeted search:
    1. `claim_targeted_search` false → return.
    2. Load the entity display name and the union of the brief's radar lexicons.
    3. Store with `connector_id=f"targeted:{brief_id}"` and `source_type="web_search"`.
  - `generate_queries_job` passes `geographies`.
- [ ] **Step 4: Run** `pnpm py:check`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(worker): Brave radars, targeted search, provisioning and yield cron`.

### Task 7: Screens

**Files:**
- Modify:
  - `apps/web/src/app/(app)/briefs/[id]/radars/radar-add-form.tsx` and `radars/page.tsx`;
  - `apps/web/src/app/(app)/discovery/page.tsx`;
  - `apps/web/src/app/actions/radars.ts`.
- Create: `apps/web/src/app/(app)/briefs/[id]/radars/radar-queries.tsx` (client: add/remove query, lexicon form)

**Interfaces:**
- **Consumes:** the Task 1 RPCs.
- **Produces:** server actions `addRadarQuery(_, form)` (fields `radar`, `text`), `removeRadarQuery(_, form)` (`query`) and `updateRadarLexicon(_, form)` (`radar`, `terms` newline-separated). Each returns `ActionState` and revalidates `/briefs/[brief]/radars`.

- [ ] **Step 1: Add-radar form.**
  - New options, labelled as in Task 1 provisioning.
  - For the five Brave types, a "Limit to sites (one per line, optional)" textarea. `createRadar` turns it into `config.sites`.
- [ ] **Step 2: Radars page.**
  - The select adds `lexicon, prefiltered_count, radar_query(id, query_text, source, record_count, yield_count)`.
  - Show: records · pre-filtered · yield N (P%), where P = yield/(records − prefiltered), shown as "—" when 0.
  - Under each radar, `<RadarQueries>` lists the queries with per-query records and yield, and offers add/remove (user rows) plus a lexicon textarea, for Editors only.
  - When `last_error` contains "not set up yet", show "Not set up yet: add BRAVE_SEARCH_API_KEY to the worker" in muted text instead of the error styling.
- [ ] **Step 3: Discovery page.** The radar rows show pre-filtered and yield %.
- [ ] **Step 4: Run** `pnpm lint && pnpm typecheck && pnpm test && pnpm build`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(web): radar types, query and lexicon editing, yield per query`.

### Task 8: Docs and env

**Files:**
- Modify: `.env.example` (`BRAVE_SEARCH_API_KEY=`) and `docs/deploy.md` (the worker keys line + the env table row)

- [ ] **Step 1: Edit both files.** Run `pnpm py:check` (the settings test reads .env.example if it does). Commit `docs: Brave Search key`.
