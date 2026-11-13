# Discovery breadth (M2): Brave Search radars, lexicon pre-filter, per-query yield

Status: approved 2026-11-13 (design presented 2026-11-12; user: "go and cover the discovery breadth from brave search").
Builds on PR #4 (migration 0032). PRD: M2 — Source and Radar Hub.

## Goal

Close the remaining M2 V1 gaps:

1. **Radar types.** Add public registries and permits, tenders and procurement, events, directories and partner lists, and public social posts. All of them run on one web-search provider, Brave Search, and cover every country.
2. **Pre-filter.** A cheap lexicon check runs before the paid AI relevance judge. Filtered records are counted, never silently dropped.
3. **Yield.** Yield is tracked per radar *and per query*.
4. **Targeted search.** An entity whose verdict is Unverified gets one narrow search.
5. **Brief to radars.** A Brief's chosen source types become radars.

CRM import (moved to piece C) and object-storage payloads are out of scope.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | A single `BraveSearchConnector(kind)` class serves five new `connector_type`s: `registry`, `tender`, `event`, `directory` and `social_post`. | Per-type names, kill switches and labels ("found by the tenders radar") with one implementation. |
| D2 | Brave returns snippets only, so each result URL is fetched through `FetchGuard` and turned into text with the website connector's HTML-to-text helper. Results are handled by why the page fetch failed:<br>• robots.txt disallows it, or the fetch fails (network error, non-2xx status, non-HTML) → the record keeps Brave's title, description and extra snippets, with `content_kind = 'snippet'`;<br>• the URL resolves to a private or reserved IP (`FetchBlocked`) → the result is dropped. | Decided 2026-11-12. |
| D3 | **Freshness.** Cursor `{"since": "YYYY-MM-DD"}` → Brave `freshness=<since>to<today>`. The first run uses `py` (past year). Duplicates are still dropped by content hash. | M2 freshness rule. |
| D4 | **Query styles.** Prompt `radar_queries` v2 has a rule per type. It also returns a `lexicon` of 10–30 terms and phrases, including local-language variants for the Brief's geographies. | M2 query generation + pre-filter in one cheap call. |
| D5 | **Sites.** `social_post` radars default to the public social domains below, joined as `(site:a OR site:b …)`. Any Brave radar may set `config.sites`, which replaces the default. | Public social is reachable only via `site:`. |
| D6 | **Lexicon.** `radar.lexicon text[]`. Regenerating queries overwrites it unless `radar.lexicon_edited` is true. | Editable, and user edits survive republish. |
| D7 | **Pre-filter rule.** It runs at the start of `judge_relevance`, on records from a radar whose lexicon is non-empty. If no term occurs (casefolded substring), the record becomes `irrelevant`, `status_detail = 'prefilter'`, with no AI call. An empty lexicon lets everything through. | Counted via the status detail, never silently dropped. |
| D8 | `source_record` gains `radar_id` and `radar_query_id` (both nullable, `on delete set null`) and `content_kind` (`full` / `snippet`), set once at insert. The first query that found a record owns it. | Per-query attribution without a join table. A duplicate hit from a second query is not re-attributed. |
| D9 | `upsert_radar_queries` keeps existing rows whose text is unchanged and deletes only generated rows that no longer appear. | Per-query history survives a republish. |
| D10 | **Yield.** A record yields when one of its active claims is on an entity that, after canonicalisation, has a Confirmed or Likely verdict on the radar's Brief's current version. `app.compute_radar_yield()` recomputes `record_count`, `prefiltered_count` and `yield_count` per radar and per query. It runs hourly at :45, in `radar.yield_compute`. `update_radar_cursor` stops adding to `yield_count`, which was "new records" until now. | PRD: records producing resolved entities that pass the Brief's gates. |
| D11 | **Metering (S6).** One budget reservation per fetch covers `n_queries × price`. It is settled with the actual number of calls, as unit `fetch`. The price is read from `model_price` (`model = 'brave-search'`, `unit = 'fetch'`, seeded at 5000 USD per million = $0.005 per call). Over a cap → `BudgetPaused`: the job ends cleanly and `budget.resume` re-runs it with `approval_id`. | Same contract as enrichment. |
| D12 | **Missing key.** `BRAVE_SEARCH_API_KEY` unset → `SearchNotConfigured`. The job ends without retrying, and the radar is marked `error` with the message "Brave Search is not set up yet". | Same pattern as AiNotConfigured; see the oauth-deferred placeholders decision. |
| D13 | **Brief ↔ connector names.** The Brief vocabulary is kept: it is stored in Briefs and in the brief_intake prompt. One mapping translates it: `news_publications→news`, `job_postings→job_post`, `public_registries→registry`, `tenders_procurement→tender`, `events→event`, `directories→directory`, `public_social→social_post` (new) and `company_websites→website`. | No data migration; one source of truth (`app.brief_source_connector`). |
| D14 | **Provisioning.** On `brief.version_published` (not `rederive_only`), `app.provision_radars(brief)` creates one radar for each mapped source type that runs from queries alone (`news` and the five Brave types) and has no live (non-paused) radar yet. The existing dispatch then generates queries for all active radars. `website`, `job_post` and `csv_upload` still need user configuration. | PRD: "turns a Brief into collection jobs". |
| D15 | **Targeted search.** When `compute_verdict` writes Unverified, it defers `source.targeted_search(entity_id, brief_id)`. That job:<br>• throttles via `targeted_search` (unique `(brief_id, entity_id)`, once per 30 days);<br>• runs one Brave call, `"<display name>"` plus up to 3 lexicon terms, 5 results;<br>• stores records with `connector_id = 'targeted:<brief_id>'` and `source_type = 'web_search'`. | M2/M5 targeted search. Budgeted and throttled, so there is no loop. |
| D16 | **Kill switches.** `source.fetch_{registry,tender,event,directory,social_post}`, `source.targeted_search` and `radar.yield_compute`. | One per job type. |

Default social domains: `linkedin.com`, `x.com`, `twitter.com`, `facebook.com`, `instagram.com`, `reddit.com`, `youtube.com`, `threads.net`, `mastodon.social`.

## Screens

- **Add radar form.** The five new types are added. Brave types get an optional "Limit to sites" field (one domain per line) → `config.sites`.
- **Radars page.** For each radar:
  - records, pre-filtered, and yield (count and %);
  - a query list with records and yield per query;
  - add or remove a user query;
  - lexicon editing (one term per line).
  - A Brave key error shows as "Not set up yet".
- **Discovery page.** Each radar row shows pre-filtered and yield %.
- **Brief editor.** `public_social` is added to the source types. brief_intake prompt v2 lists it.

## Database (migration 0033)

**Schema**
- `radar.connector_type` check extended to the five new types.
- New `radar` columns: `lexicon text[] default '{}'`, `lexicon_edited bool default false`, `prefiltered_count int default 0`.
- `radar_query` gains `record_count`, `prefiltered_count` and `yield_count` (int, default 0).
- `source_record` gains `radar_id`, `radar_query_id` and `content_kind`. The immutability trigger is unchanged: the columns are set at insert.
- New table `targeted_search`, with RLS read for Viewer.
- `model_price` seed row.
- `job_type_config` rows for D16.

**Functions**
- `app.create_source_record`: re-created with `p_radar uuid default null`, `p_radar_query uuid default null` and `p_content_kind text default 'full'`.
- `app.upsert_radar_queries`: D9 behaviour; also takes the lexicon, `p_lexicon text[] default null`, which is written only when `lexicon_edited` is false.
- `app.update_radar_cursor`: D10 behaviour.
- `app.compute_radar_yield()`, `app.provision_radars(uuid)`, `app.brief_source_connector(text)` and `app.claim_targeted_search(uuid, uuid)`. The last returns bool and throttles.

**Editor+, audited**
- `public.add_radar_query(p_radar, p_text, p_language)`
- `public.remove_radar_query(p_query)`
- `public.update_radar_lexicon(p_radar, p_terms text[])`, which sets `lexicon_edited`.
- `public.create_radar` now validates `config.sites`: an array of up to 20 host names.

## Errors

- A Brave error status (429/5xx) raises, and the job retries (retry=3) with the reservation released.
- 401/403 → `SearchNotConfigured`, handled as D12.
- A single result-page failure never fails the fetch: the record falls back to its snippet.

## Testing

- **pytest:**
  - the connector, with Brave and page fetches mocked: freshness parameter, site clause, snippet fallback on robots and on errors, private IP dropped, query attribution, metering reserve and settle, missing key;
  - the pre-filter;
  - lexicon parsing in prompt v2;
  - targeted search throttling;
  - worker dispatch: provisioning plus the new fetch tasks.
- **PGlite e2e:** a fake-mode Brave radar fetches → one record is pre-filtered, one is judged → yield compute counts it after a Confirmed verdict.
- **pgTAP suite 20:** schema, RPC permissions, upsert keeping history, provisioning, yield computation, throttle.
- **Web:** typecheck, lint and build.
- CI stays green.
