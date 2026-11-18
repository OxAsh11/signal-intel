# Engine test study: FlytBase seller Brief, 2026-11-17

What the end-to-end engine test showed: each problem with its evidence, root cause in the code, the PRD rule it breaks, and the fix options. No fixes have been made yet, except the CSV upload (PR #13).

## How the test was run

- **Brief:** "FlytBase sells an enterprise drone autonomy platform…" (`e956b785`), in the user's own account, created through the live site and started as a pilot.
- **Worker:** a local copy of the production worker, running the same jobs, prompts and validation against the live database. Only the chat model changed: Claude Code CLI stood in for the Anthropic API, which has no credit. Embeddings used the real Voyage API.
- **Data:** news search was blocked (problem 9), so 19 real web findings went in as a CSV radar. 15 were buyer signals (utilities hiring drone leads, police drone programmes, security firms using docks). 4 were non-buyers (DJI, Infinit, Skydio, a market report).
- **Hand repairs needed to get a result:**
  - added 4 predicates through the Brief page;
  - queued re-extraction;
  - merged 28 duplicate companies;
  - queued verdicts again.

  A real user could do none of these, except adding predicates.

**Final result after the repairs:** 7 Confirmed, 4 Likely, 8 Unverified. All six drone vendors were Unverified, with no supporting facts and a quoted "sells drones" fact each. The market report was rejected as irrelevant. Two police departments were rejected wrongly (problem 4).

## Severity summary

| # | Problem | Effect on a real user | Severity |
|---|---|---|---|
| 1 | The AI draft creates no predicates | No company can ever be Confirmed or Likely | Blocks the product |
| 2 | Repeat sightings of a company go to manual review | Evidence never builds up; Review fills up | Blocks the product |
| 3 | Verdicts don't recompute when facts arrive later | Verdicts go stale after merges, backfill and research | Critical |
| 4 | The relevance filter ignores the Brief and rejects public bodies | Government and police buyers are dropped | Critical for public-sector Briefs |
| 5 | Values stored as JSON inside a string, with prompt and database disagreeing | Many extractions fail; facts lost | Critical |
| 6 | Claims written one by one and retried | Duplicate claims inflate evidence | High |
| 7 | Adding a predicate doesn't backfill | New columns stay empty for data already collected | High |
| 8 | Verdict is a free AI judgement, not the PRD's rubric | Verdicts can't be explained or tuned; reasoning not stored | High |
| 9 | News: GDELT burst-limited, errors swallowed, headline only | The only free radar returns nothing | High |
| 10 | Three radars need Brave; job posts and websites can't discover | 5 of the 8 drafted sources do nothing | High |
| 11 | Scoring reads hard-coded predicate names; observed date is the extraction date | Fit always 50, scale always 0, timing always "fresh" | High |
| 12 | `meeting.person_entity_id` has no foreign key | The Review page errors on every load | Medium |
| 13 | Discovery yield is calculated hourly | Discovery and Today disagree for up to an hour | Low |
| 14 | "def v1" shown under every stat tile | Looks like debug text | Low |
| 15 | Brief name is the shortened description | Hard to tell Briefs apart | Low |
| 16 | CSV upload: no bucket, errors hidden, 1 MB limit | Upload never worked | Fixed in PR #13 |

---

## 1. The AI draft creates no predicates

**What happened.** Extraction asked the AI only for `canary.pipeline_ok`, the only predicate in the database. 16 sources were read and 0 claims written, so all 19 verdicts were `Unverified (no_claims)`.

**Root cause.**
- [outputs.py:54](../../packages/signal_core/src/signal_core/ai_gateway/outputs.py#L54): `BriefDraft` has no field for predicates or evidence rules.
- [brief_intake/v2.md](../../packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v2.md) asks for the hypothesis as "a clear predicate statement", meaning a sentence, not typed predicates.
- [m01_brief.py](../../packages/signal_core/src/signal_core/modules/m01_brief.py) saves the draft and nothing else, so no `predicate` rows are ever created.
- Extraction only sees predicates that exist ([m03_extraction.py:256](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L256)).
- The Brief page says the facts are "Set for you; open only to change them" ([page.tsx:81](../../apps/web/src/app/(app)/briefs/[id]/page.tsx#L81)), but nothing sets them.

**PRD.** Hypothesis: "in plain language **and as predicate conditions**" (M1, required field). M3 extraction is "limited to predicates in scope for the linked Briefs".

**Fix options.**
- **A (recommended):** intake also drafts 3–6 typed predicates from the hypothesis (key, label, type, allowed values, extraction hint, decay). The user reviews them with the rest of the draft, and they're saved when the draft is saved.
- **B:** a fixed default set per workspace, such as vertical, size, geography and hiring role. Simpler, but generic: it would not have caught "drone programme announced".

**Also needs:** predicates scoped to a Brief. Today they're workspace-wide, so two Briefs in one workspace would share and mix them. The PRD says "workspace-scoped `predicate` rows" are owned by M1, so per-workspace is allowed, but extraction should send only the predicates of the Briefs linked to the radar.

## 2. Repeat sightings of a company go to manual review

**What happened.** Re-extraction created a new provisional company for every mention. Identity matching found the existing company with the same name and scored it **89**. That's below the 90 needed to attach automatically, so it queued 19 items in Review. The new facts stayed on duplicate records until I merged them.

**Root cause.**
- [m04_identity.py:137](../../packages/signal_core/src/signal_core/modules/m04_identity.py#L137): name matches score `min(89, 70 + similarity × 20)`, so even an identical name can never reach 90.
- [m04_identity.py:310](../../packages/signal_core/src/signal_core/modules/m04_identity.py#L310) attaches at 90 and above; [:351](../../packages/signal_core/src/signal_core/modules/m04_identity.py#L351) queues 40–89 for review.
- Most news and job text has no domain or registry ID, so name plus country is the usual match.

**This hits normal running, not only the re-run.** The second article about any known company without a website in the text goes to Review. Its facts don't count until someone clicks Merge, and even then the verdict doesn't update (problem 3).

**PRD.** M4 lists "normalised name with country" as an alias type, then fuzzy matching, then an AI tie-break. It sets 90+ attach and 40–89 review, but doesn't say a name match must stay below 90. The cap is an implementation choice.

**Fix options.**
- **A (recommended):** an exact normalised name match in the same country (or with no country on either side) scores 95 and attaches. Fuzzy matches stay capped at 89. Ambiguous cases (several exact matches, or a known name clash) go to the AI tie-break, which already exists.
- **B:** keep the cap and let the AI tie-break decide every name match. Costs an AI call per repeat mention.

**Also:** the name string includes the country only when there is one (`"duke energy|united states"` vs `"duke energy"`), so the same company with and without a country won't match well. The country should be compared separately.

## 3. Verdicts don't recompute when facts arrive later

**What happened.** After re-extraction (problem 7) and after merges, no `verdict.compute` ran. Verdicts stayed `Unverified` until I queued them by hand.

**Root cause.** A verdict is only requested from `_defer_verdict`, when a mention first attaches to or creates a company ([m04_identity.py:219](../../packages/signal_core/src/signal_core/modules/m04_identity.py#L219)). Nothing else triggers one:
- `app.create_claim` emits no `claim.created`. Only the canary emits it.
- `app.merge_entities` and `resolve_review_item` write an audit row, but no `entity.merged` event.
- The worker's event dispatcher has no case for `claim.created` or `entity.merged`.

So verdicts miss facts from merges (automatic or manual), backfill, and research write-back (M6 writes claims too).

**PRD.** M3 emits `claim.created`, and M4 emits `entity.merged`.

**Fix (recommended).**
- Emit `claim.created` from `create_claim` and `entity.merged` from `merge_entities`.
- The dispatcher queues one `verdict.compute` per company, then the score.
- The existing `queueing_lock` (`verdict:<entity>:<workspace>`) already collapses bursts.

## 4. The relevance filter ignores the Brief and rejects public bodies

**What happened.** Greeley Police and Milwaukee Police were marked irrelevant. The reason given: "describes a government agency… lacks identification of commercial business entities".

**Root cause.** [relevance_judge/v1.md](../../packages/signal_core/src/signal_core/ai_gateway/prompts/relevance_judge/v1.md) asks a generic question, with no Brief: does the text mention a *business* entity with facts of commercial value? It gets only the page text.

**PRD.** M3 step 1: "Relevance judge: is this record **about the hypothesis** at all?"

**Fix (recommended).**
- A new prompt version receives the Brief's hypothesis and examples (from the radar's Brief), and asks whether the record is evidence for or against it.
- Organisations include government bodies, agencies, municipalities and non-profits.
- A record found by a radar belongs to one Brief, so the context is known.
- Re-run the prompt evals (`ai_gateway/evals.py`) before switching versions.

## 5. Values stored as JSON inside a string, with prompt and database disagreeing

**What happened.** Three of 16 re-extractions failed after three attempts each. The errors:
- `claim value "true" does not match predicate drone_program.announced (boolean)` (database type check), several times;
- `claim value "100+" does not match predicate drone_program.dock_count (number)`;
- `claims.2.value: claim.value must be valid JSON` (gateway check on an unquoted text value).

**Root cause.**
- [m03_extraction.py:65](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L65): `value: str  # JSON string`. The model must write JSON inside a string, which structured outputs can't enforce.
- **The prompt teaches the wrong form for booleans:** `'"true"' for boolean` ([extraction/v1.md:16](../../packages/signal_core/src/signal_core/ai_gateway/prompts/extraction/v1.md#L16)). That is the JSON *string* "true", which `app.validate_claim` correctly refuses for a boolean predicate.
- The model is never told each predicate's type: the prompt gets only `key` and `hint` ([m03_extraction.py:256](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L256)).

When the model ignored the prompt and wrote `true`, the claim was stored. When it followed the prompt, the claim failed.

**PRD.** "Typed outputs… Invalid output is rejected, not coerced." Rejecting "100+" for a number is correct ("Magnitudes need their own quote"). Rejecting a correctly meant `true` is not.

**Fix (recommended).**
- New extraction prompt version: send each predicate's `value_type` and `allowed_values`, and make `value` a typed JSON value (`string | number | boolean`) in the schema instead of a JSON-in-string.
- Validate each claim against its predicate in Python before writing (`registry.validate_claim_value` already mirrors the database check). Drop only the invalid claim, with a log line, rather than failing the whole record.
- Numbers like "100+" are dropped as invalid, which matches the magnitude rule.

## 6. Claims written one by one and retried

**What happened.** Berkshire Hathaway Energy and QE Solar each got 3 identical claims from the same source. Berkshire's verdict shows "3 claims" when it has one fact.

**Root cause.**
- Mentions and claims are written in a loop, each on its own connection, outside a transaction ([m03_extraction.py:321](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L321)).
- When claim 3 failed (problem 5), claims 1–2 stayed. The job retried (`retry=2`) and wrote them again.
- `app.create_claim` has no duplicate check: there's no unique key on (entity, predicate, source record, value).

**Fix (recommended).**
- Write all mentions and claims for a record in one transaction.
- Give `create_claim` an idempotency rule: the same source record, predicate, value and entity updates the claim instead of inserting it.
- Combined with problem 5, one bad claim no longer fails the record.

**Also affects verdicts:** the verdict prompt calls "multiple independent claims" Confirmed. Three copies from one source must count as one, because independence means different sources.

## 7. Adding a predicate doesn't backfill

**What happened.** After I added 4 predicates through the Brief page, nothing re-read the 16 sources already extracted.

**Root cause.**
- `create_workspace_predicate` only inserts the row and an audit entry.
- `extract_backfill` exists but re-queues only *pending* records, and runs only when a version is published ([m03_extraction.py:407](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L407)).
- A re-run would also hit problems 2 and 6: a new provisional company each time, and duplicate claims.

**PRD.** "Adding a column… backfills it from existing source records (Redefine)". M3 backfill: "extraction re-runs over existing records **for that predicate only**." M1: no edit applies without a preview of its cost.

**Fix (recommended).**
- Adding a predicate emits an event, and the worker re-runs extraction for that predicate only over the Brief's extracted records, showing the cost first.
- Re-extraction must reuse the record's existing mentions and companies instead of creating provisional ones, so it needs problems 2 and 6 fixed first.

## 8. Verdict is a free AI judgement, not the PRD's rubric

**What happened.** Verdicts were sensible, but they come from one AI call with only the hypothesis and the claim list ([m05_verdict.py:201](../../packages/signal_core/src/signal_core/modules/m05_verdict.py#L201), [verdict/v2.md](../../packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/v2.md)).

**Gaps.**
- There's no rubric (predicate, value, minimum independent sources, acceptable source types, maximum age).
- The Brief's examples aren't given to the verdict, although M1 says they "calibrate… verdicts (M5)".
- The AI's one-line reasoning is requested but not stored: `inputs` keeps only prompt and version ids. So "why" is blank in the database.
- A vendor that "sells drones" ends up Unverified. Contradicted (the vendor is a competitor) would be more useful, and would feed M8's competitor gate.

**PRD.** M5: "The rubric in the Brief version defines each value as conditions over claims." "Every verdict stores the claim IDs and quotes it rests on" is met.

**Fix options.**
- **A (recommended for V1):** the intake drafts simple evidence rules (problem 1). A deterministic check applies them: required predicates, independent sources, maximum age. The AI judges only what the rules leave open, and its reasoning is stored.
- **B:** keep the AI judgement, but pass the examples and evidence rules into the prompt and store the reasoning. Less work, but still not reproducible as the PRD wants.

## 9. News: GDELT burst-limited, errors swallowed, headline only

**What happened.** The News radar ran for 150 seconds and stored 0 articles. After that, this computer's address was blocked by GDELT ("Please limit requests to one every 5 seconds"), returning HTTP 429.

**Root cause.**
- [news.py:94](../../packages/signal_core/src/signal_core/connectors/news.py#L94) sends all 10 queries back-to-back with no pacing.
- GDELT's limit message is plain text. `resp.json()` fails, and [news.py:175](../../packages/signal_core/src/signal_core/connectors/news.py#L175) catches the `ValueError` and returns `[]`. So a rate limit looks exactly like "no news", with no log line.
- Each payload is only GDELT's metadata (title, URL, domain, date). The article text is never fetched, so extraction judges a headline.
- The queries are sentence-like ("port operator deploys autonomous drone inspection"). GDELT treats every word as required, which likely matches little. This couldn't be checked because of the block.
- Language is hard-coded to `en`, while the queries include Spanish and French.

**PRD.** M2: "Nothing is dropped silently". Radars must show processing status and health.

**Fix (recommended).**
- Pace GDELT to one call every 6 s, read the limit message as a retryable error, and log it.
- Generate short keyword queries for GDELT (quoted phrases joined with OR).
- Fetch each article's text through the existing website connector, which already respects robots.txt, before extraction.

## 10. Three radars need Brave; job posts and websites can't discover

**What happened.**
- The Brief drafted 6 sources. 4 became radars.
- Events, Social and Tenders all need Brave Search: "Brave Search is not set up yet". So 3 of 4 radars stay in error, and Today shows "3 radars failing".
- Job postings and Company websites never became radars.

**Root cause.**
- `app.provision_radars` builds radars only for news, registry, tender, event, directory and social ([discovery_breadth.sql:286](../../supabase/migrations/20261113000033_discovery_breadth.sql#L286)).
- The job-posts connector reads only boards it's given (Greenhouse, Lever and Ashby tokens for named companies). The website connector reads only URLs it's given. Neither can discover new companies.
- The intake still offers both as sources, and the Brief page lists them under "Where we will look".

**Fix options.**
- Free discovery for job postings and tenders needs a search API. Brave costs about $5 per 1,000 calls and its free tier needs a card. Free alternatives to compare: public procurement feeds (e.g. TED for the EU, SAM.gov for the US), job aggregators with open APIs, and RSS from trade press.
- Until then, the intake should only offer sources that can run, and the Brief page should mark the rest "Not set up yet" instead of listing them as places it will look.

## 11. Scoring reads hard-coded predicate names; observed date is the extraction date

**What happened.** Every company has fit 50, and bands are mostly Low, even when Confirmed.

**Root cause.**
- [scoring_core.py:16](../../packages/signal_core/src/signal_core/scoring_core.py#L16): fit uses the predicates `vertical`, `size`, `geography` and `commercial_role`. Line 17: scale uses `sites`, `seats` and `units`. No workspace has these, so fit = 50 and scale = 0 always.
- The ICP gate ("outside the ICP → Excluded") never fires either.
- Even with those predicates, fit is the average *extraction confidence* of fit claims, not whether the vertical matches the Brief's ICP.
- `observed_at` is set to `now()` at extraction time ([m03_extraction.py:342](../../packages/signal_core/src/signal_core/modules/m03_extraction.py#L342)), not the date in the source. So timing counts every claim as recent, and claim decay starts from the wrong day.

**PRD.** M8 dimensions; F3 "claims age" from the observed date.

**Fix (recommended).**
- The intake creates the ICP predicates (vertical, size, geography) along with the hypothesis predicates (problem 1), and scoring reads which predicate plays which role from the Brief version, not from a hard-coded list.
- Fit = how well the claim values match the Brief's ICP lists.
- Extraction returns the source's date (article or posting date) as `observed_at`, falling back to the fetch date.

## 12. `meeting.person_entity_id` has no foreign key

**What happened.** The Review page shows: "Part of the queue could not be loaded: Could not find a relationship between 'meeting' and 'person_entity_id' in the schema cache."

**Root cause.**
- `meeting` has foreign keys for account, org and workspace, but not `person_entity_id → entity(id)`.
- `activity` has that key, so its join works.
- The Review query embeds `person:person_entity_id(display_name)` ([review/page.tsx:210](../../apps/web/src/app/(app)/review/page.tsx#L210)), which the data API can't resolve without a foreign key.

**Fix (recommended).** A migration adds the foreign key, after checking that no existing rows point to a missing company. Add an e2e check that Review loads with no error banner.

## 13. Discovery yield is calculated hourly

**What happened.** Discovery said "From 19 pages to 0 matches" while Today said 11.

**Root cause.** Radar yield comes from `app.compute_radar_yield()` in a job that runs at :45 each hour. Today reads live verdicts.

**PRD.** "The same metric shows the same value on every surface for the same filters."

**Fix options.**
- Recalculate the radar's yield when a verdict changes. The `verdict.changed` event already exists.
- Or have Discovery compute "Led to a match" live, the same way Today does.

## 14. "def v1" shown under every stat tile

The label is the metric definition version, passed as `version="def v1"`. It serves the PRD rule that metric definitions are versioned, but the PRD doesn't ask for it to be shown, and to a user it reads as debug text.

**Suggest:** move it into the tile's tooltip or the "how this is counted" link.

## 15. Brief name is the shortened description

The name is `nameFromDescription(description)` ([briefs.ts:31](../../apps/web/src/app/actions/briefs.ts#L31)): "FlytBase sells an enterprise drone autonomy platform:…".

**Suggest:** the intake also returns a short name ("Industrial sites automating drone ops"), which the user can edit.

## 16. CSV upload

Fixed in PR #13:
- the bucket is created;
- errors are shown;
- non-CSV files and files over 10 MB are refused;
- the server limit is raised to 11 MB.

**Still open:** a CSV row's source URL is stored as `csv:<file path>:<row>`, so evidence links point to the file, not the article. If the row has a `url` column, that should be the record's URL.

---

## Suggested order of work

1. **Problems 5, 6 and 4.** Typed claim values, one transaction per record, a Brief-aware relevance judge. This makes extraction trustworthy.
2. **Problems 2 and 3.** Exact name matches attach, and claim and merge events recompute verdicts. Evidence then builds up on its own.
3. **Problem 1, with problems 8 and 11.** The intake drafts predicates, ICP predicates and evidence rules. Verdicts and scores then read them.
4. **Problem 7.** Backfill for a new predicate (needs steps 1–2).
5. **Problems 9 and 10.** Free discovery: paced GDELT with article text, and a decision on search APIs.
6. **Problems 12–15.** Screen fixes. Problem 12 is small and could go first.

Each step needs the e2e and Python pipeline tests extended with the failing case first, as in PR #13.
