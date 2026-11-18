# P2 Brief → columns and rubric Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Brief drafts its own columns (typed predicates with a scoring role), its ICP lists and its evidence rules.
Verdicts follow those rules deterministically and call the AI only for open cases, with the reasoning stored.
Scores read predicate roles instead of hard-coded names. Adding a column backfills it over records already
collected. Fixes engine gaps #1 #8 #11 #7 #15.

**Architecture:**
- **One source of truth:** the Brief version's `config` holds `predicates` (column specs) and `evidence_rules`
  (spec D1, no second copy).
- **Database (migration 0038):**
  - validates column specs and rules on save and publish;
  - a trigger on `brief_version` insert syncs the specs into workspace `predicate` rows and emits
    `predicate.backfill_requested` for the keys a version adds;
  - Briefs get an AI-written, editable name.
- **Worker:**
  - intake v3 drafts the name, the columns, the rules and "What I assumed";
  - extraction sends only the columns of the record's Brief;
  - a backfill job extracts new columns from records already extracted, onto their existing mentions;
  - verdicts run a pure rubric first and the AI (verdict v3) only for open cases;
  - scoring reads roles.
- **Web:** the Brief page shows and edits Columns, Evidence rules and "What I assumed", and the Brief can be renamed.

**Tech Stack:**
- Postgres / Supabase SQL migrations, with pgTAP run on PGlite;
- Python 3.13, psycopg 3 async, Procrastinate, pydantic, pytest;
- Next.js and React, with Playwright e2e.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md)
§11 step 1 (gaps #1, #8, #11 → #7). Page behaviour: [05-ia-and-pages §4.2](../../research/clay/05-ia-and-pages.md).
Gap details: [engine test study](../../investigations/2026-11-17-engine-test-study.md) §1, 7, 8, 11, 15 (fix
option A in each). Roadmap: [2026-11-18-redesign-roadmap.md](2026-11-18-redesign-roadmap.md), row P2.

## Global Constraints

- **Branch:** `feat/brief-columns-rubric` from `main` (at `3cf7e02` or later). One PR.
- **New migration:** `supabase/migrations/20261119000038_brief_columns_rubric.sql`. Never edit an earlier migration.
- **Prompts:**
  - released prompt files are frozen;
  - new: `brief_intake/v3.md`, `verdict/v3.md`, `extraction_backfill/v1.md`, each with its sha256 in
    `prompts/LOCK.json`.
- **Column spec** (an item of `config.predicates`; legacy configs may hold plain key strings, which every reader
  must accept as "a key with no spec"):

  | Field | Rule |
  |---|---|
  | `key` | `^[a-z][a-z0-9_]{1,39}$`, unique within the Brief |
  | `label` | 1–80 chars |
  | `value_type` | `boolean` · `number` · `enum` · `text` · `date` |
  | `allowed_values` | enum only: 2–12 distinct strings; otherwise null |
  | `extraction_hint` | string, ≤ 300 chars, may be empty |
  | `decay_half_life_days` | null or 1–3650 |
  | `role` | `signal` · `fit.vertical` · `fit.geography` · `fit.size` · `fit.commercial_role` · `scale` · `info` |

  At most 12 columns per Brief.
- **Evidence rule** (an item of `config.evidence_rules`):

  | Field | Rule |
  |---|---|
  | `predicate` | the key of a column in the same config |
  | `kind` | `requires` · `disqualifies` |
  | `op` | boolean: `is_true` `is_false` `present`; number: `at_least` `present`; enum: `one_of` `present`; text: `contains` `present`; date: `present` |
  | `value` | `one_of`: non-empty list of the column's allowed_values; `at_least`: a finite number; `contains`: non-empty list of strings; otherwise null |
  | `min_sources` | integer 1–5, default 1 |
  | `max_age_days` | null or 1–3650 |

  At most 10 rules per Brief.
- **Role → ICP list:**
  - `fit.vertical` → `icp.verticals`;
  - `fit.geography` → `icp.geographies`;
  - `fit.size` → `icp.size_bands`;
  - `fit.commercial_role` → `icp.commercial_roles`.
- **Legacy configs** (no column specs, no rules):
  - verdicts keep the verdict v2 AI path, now with the reasoning stored;
  - scoring keeps the old key → role defaults:
    - `vertical`, `size`, `geography` and `commercial_role` map to their `fit.*` role;
    - `sites`, `seats` and `units` map to `scale`;
  - extraction keeps sending every workspace and global predicate.
- **Validation errors:**
  - SQL raises errcode `22023` for an invalid spec or rule, with a message that names the column, e.g.
    `column "drone_programme": enum needs 2–12 allowed values`;
  - a key that already exists in the workspace with a different `value_type` raises `23514`, with the message
    `column "<key>" already exists in this workspace as <type>; give it another key`.
- **AI output is cleaned, never coerced:** an invalid column or rule in the intake output drops only itself, and a
  rule that points at a dropped column drops too (same principle as P1).
- **Backfill:**
  - covers records with `extraction_status = 'extracted'` whose radar belongs to the Brief, or that have no
    radar;
  - newest first, capped at 500 per event; anything beyond the cap is logged as `extraction.backfill_capped`;
  - queueing lock: `backfill:<source_record_id>:<sorted keys joined by ",">`.
- **Locks:** verdict jobs keep `verdict:<entity_id>:<workspace_id>`.
- **Gates before calling the work done:**
  - `pnpm db:check`;
  - `node scripts/check-pgtap.mjs`;
  - `uv run ruff check .` and `uv run ruff format --check .`;
  - `uv run mypy`;
  - `uv run pytest` (the whole Python suite);
  - `uv run pytest services/worker/tests/test_pipeline_e2e.py`;
  - `pnpm typecheck`;
  - `pnpm --filter web e2e`;
  - Brief page screenshots on desktop and phone.
- **Live prompt evals** need a paid AI key, which isn't available. Say so in the PR rather than claiming they ran.

## Review Focus

1. **A column key that already exists in the workspace with another type** (a second Brief also drafts `size`,
   as text instead of number). Publishing fails with the `23514` message, and nothing is half-written. With the
   same type, the existing predicate is reused and its claims count for both Briefs. *pgTAP in Task 1.*
2. **The AI returns a half-valid draft:**
   - one column with a bad key or an enum with one value;
   - a rule that points at a key it never defined.

   The valid parts are saved, and the bad ones are dropped with a log line. *Test in Task 2.*
3. **Several sources, one origin.** Two claims for a `min_sources: 2` rule from two records with the same URL host
   count as one source. The rule is not met, so the verdict is not Confirmed. *Test in Task 4.*
4. **A Brief edited from no rules to rules, or the reverse.** The legacy AI path and the rubric path each produce a
   valid verdict. A version with rules never calls verdict v2. *Test in Task 4.*
5. **Backfill on a record whose mention's entity was merged away, or never resolved.** That mention is skipped.
   The other mentions still get their claims, and running the job again adds no duplicates. *Test in Task 3.*

---

### Task 1: Migration 0038 — columns, rules, predicate sync, AI-named Briefs

**Files:**
- Create: `supabase/migrations/20261119000038_brief_columns_rubric.sql`
- Create: `supabase/tests/database/22_brief_columns_rubric.test.sql`

**Interfaces:**
- Produces:
  - `app.validate_brief_columns(p_cfg jsonb) returns void`;
  - `app.assert_brief_publishable` (replaced; same signature, now also calls `validate_brief_columns`);
  - `public.save_brief_draft_ui` (replaced; validates before saving);
  - the trigger `brief_version_sync_predicates` (after insert on `public.brief_version`);
  - the event type `predicate.backfill_requested`, with payload
    `{brief_id, brief_version_id, org_id, workspace_id, predicate_keys: [text]}`;
  - the column `brief.name_auto boolean not null default false`;
  - `app.create_brief(p_ws uuid, p_name text, p_description text default null, p_name_auto boolean default false)`
    and its `public.` wrapper (drop the 3-argument versions first);
  - `app.apply_drafted_name(p_brief uuid, p_name text) returns text` (service role);
  - `public.rename_brief(p_brief uuid, p_name text) returns void` (Editor+).

- [ ] **Step 1: Write the failing pgTAP file** `22_brief_columns_rubric.test.sql`. Follow the setup style of
  `19_brief_pilot.test.sql`. Assertions:
  - `validate_brief_columns` accepts the FlytBase sample config below. It throws `22023` for each of these:
    - a bad key (`'Drone'`);
    - a duplicate key;
    - an enum with 1 value;
    - an unknown role;
    - 13 columns;
    - a rule on an unknown key;
    - `at_least` on a boolean column;
    - `one_of` with a value outside `allowed_values`;
    - `min_sources` 6.
  - a config without `predicates` / `evidence_rules` passes, so legacy configs stay valid;
  - `save_brief_draft_ui` with an invalid rule throws `22023`, and the draft is unchanged;
  - `start_pilot` on a Draft whose config has 3 columns creates 3 `predicate` rows with the specs' `label`,
    `value_type`, `allowed_values`, `decay_half_life_days` and `extraction_hint`, and emits one
    `predicate.backfill_requested` outbox row with all 3 keys;
  - publishing version 2 with one extra column emits a backfill event with only that key. Version 3 with the
    same columns emits none;
  - an existing workspace predicate `size` of type `text`, against a spec `size` of type `number`: publishing
    throws `23514`. No version row, no predicate change and no outbox row remain;
  - same key, same type, enum: `allowed_values` becomes the union. The existing row id is kept;
  - `apply_drafted_name`:
    - renames a Brief with `name_auto = true` and clears the flag;
    - appends ` 2` when the name is taken in the workspace;
    - leaves a Brief with `name_auto = false` unchanged and returns its current name;
  - `rename_brief`:
    - works for an Editor, sets `name_auto = false`, and writes a `brief.renamed` audit row;
    - throws `42501` for a Viewer;
    - throws `22023` for a blank name.

  Sample config, used here and in later tasks' fixtures. Put a copy in
  `packages/signal_core/tests/fixtures/flytbase_columns.json` and reuse it:

  ```json
  {"predicates": [
     {"key": "drone_programme", "label": "Runs or plans a drone programme", "value_type": "boolean",
      "allowed_values": null, "extraction_hint": "Announced, tendered or hiring for drone operations",
      "decay_half_life_days": 365, "role": "signal"},
     {"key": "industry", "label": "Industry", "value_type": "enum",
      "allowed_values": ["energy", "utilities", "public safety", "mining", "drone vendor"],
      "extraction_hint": "", "decay_half_life_days": null, "role": "fit.vertical"},
     {"key": "site_count", "label": "Industrial sites", "value_type": "number", "allowed_values": null,
      "extraction_hint": "Plants, substations or sites operated", "decay_half_life_days": 730, "role": "scale"}],
   "evidence_rules": [
     {"predicate": "drone_programme", "kind": "requires", "op": "is_true", "value": null,
      "min_sources": 1, "max_age_days": 730},
     {"predicate": "industry", "kind": "disqualifies", "op": "one_of", "value": ["drone vendor"],
      "min_sources": 1, "max_age_days": null}]}
  ```

- [ ] **Step 2: Run it and see it fail.** Run `node scripts/check-pgtap.mjs`. Expected: suite 22 fails (functions
  missing).

- [ ] **Step 3: Write the migration.** Order:
  1. `alter type public.event_type add value 'predicate.backfill_requested';`
  2. the `name_auto` column;
  3. `validate_brief_columns` (`immutable`, plpgsql, the Global Constraints tables as checks, messages naming the
     column);
  4. replace `assert_brief_publishable` and `save_brief_draft_ui`;
  5. the sync trigger function, `security definer`, `search_path = ''`. For each object spec, in key order:
     - insert a missing predicate with `app.audit(... 'predicate.created' ...)`;
     - for an existing key with a different `value_type`, raise `23514`;
     - for an existing enum key, union its `allowed_values`.

     Then compute the added keys: spec keys of `new.config` minus the keys of the same Brief's previous version
     (strings count as keys). If any were added, emit the backfill event;
  6. the name functions. Grants follow migration 0035: `public.*` to `authenticated`, `app.*` not granted.

- [ ] **Step 4: Run the gates.** Run `node scripts/check-pgtap.mjs` and `pnpm db:check`. Expected: all suites
  pass, including suites 19 and 21 unchanged.

- [ ] **Step 5: Commit.**
  ```bash
  git add supabase/migrations/20261119000038_brief_columns_rubric.sql supabase/tests/database/22_brief_columns_rubric.test.sql packages/signal_core/tests/fixtures/flytbase_columns.json
  git commit -m "feat(db): Brief column specs and evidence rules, predicate sync, AI-named Briefs"
  ```

### Task 2: Intake v3 drafts name, columns, rules and assumptions

**Files:**
- Create: `packages/signal_core/src/signal_core/brief_columns.py`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v3.md`
- Modify:
  - `packages/signal_core/src/signal_core/ai_gateway/outputs.py`;
  - `packages/signal_core/src/signal_core/modules/m01_brief.py`;
  - `prompts/LOCK.json`
- Test:
  - `packages/signal_core/tests/test_brief_columns.py`;
  - the existing m01 intake tests (find with `grep -rl run_brief_intake packages services`)

**Interfaces:**
- Consumes: `app.apply_drafted_name(p_brief, p_name)` from Task 1.
- Produces, in `signal_core.brief_columns`:
  - `ROLES: tuple[str, ...]`; `OPS_BY_TYPE: dict[str, tuple[str, ...]]`;
    `ICP_LIST_BY_ROLE: dict[str, str]`;
  - `class ColumnSpec(BaseModel)` and `class EvidenceRule(BaseModel)`, strict, mirroring the Global Constraints
    tables;
  - `clean_columns(raw_predicates: list[Any], raw_rules: list[Any]) -> tuple[list[ColumnSpec], list[EvidenceRule], list[str]]`.
    It returns the valid specs, the valid rules and the reasons dropped. It drops duplicates after the first, and
    keeps at most 12 columns and 10 rules;
  - `column_specs(config: dict[str, Any]) -> list[ColumnSpec]`, which ignores legacy strings and invalid items;
  - `predicate_keys(config) -> list[str]`, covering both strings and specs;
  - `evidence_rules(config) -> list[EvidenceRule]`.
- Produces, in `BriefDraft`:
  - `name: str = ""`;
  - `predicates: list[JsonValue] = []`;
  - `evidence_rules: list[JsonValue] = []`;
  - `assumptions: list[str] = []`.

  The lists stay lenient so that one bad item doesn't fail the whole draft.

- [ ] **Step 1: Write failing tests** in `test_brief_columns.py`:
  - `test_clean_keeps_valid_fixture`: the fixture JSON gives 3 specs, 2 rules and no drops;
  - `test_clean_drops_only_bad_column`: a fixture plus `{"key":"Bad Key",…}` gives 3 specs and 1 drop reason
    naming `Bad Key`;
  - `test_rule_on_dropped_column_drops`: a rule on `nonexistent` is dropped and the others are kept;
  - `test_op_must_fit_type`: `at_least` on `drone_programme` is dropped;
  - `test_caps`: 15 columns are cut to 12, 12 rules to 10;
  - `test_predicate_keys_accepts_legacy_strings`: `{"predicates": ["vertical", {spec}]}` gives
    `["vertical", "drone_programme"]`.

  Extend the m01 intake test with a fake gateway result that includes `name`, two valid columns, one invalid
  column, one rule and `assumptions`. Assert:
  - `save_brief_draft` received a config with the cleaned `predicates`, `evidence_rules` and `assumptions`;
  - `apply_drafted_name` was called with the drafted name;
  - the log has `brief_intake.column_dropped`.

  When `name` is blank, `apply_drafted_name` is not called.
- [ ] **Step 2: Run the tests and see them fail.** Run
  `uv run pytest packages/signal_core/tests/test_brief_columns.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement** `brief_columns.py`, the `BriefDraft` fields, the prompt `brief_intake/v3.md` and the
  changes to `m01_brief.run_brief_intake`:
  - **Prompt v3:** all of v2's fields, plus:
    - `name`, ≤ 60 chars, naming who is sought, not the seller;
    - `predicates`: 3–8 columns, at least one `signal` column, plus one `fit.*` column per non-empty ICP list;
    - `evidence_rules`: at least one `requires` rule on a signal column; a `disqualifies` rule when competitors or
      vendors are an obvious false positive;
    - `assumptions`: ≤ 5 short sentences;
    - the column and rule tables from Global Constraints, given verbatim;
    - an instruction to reuse an existing workspace key when the meaning matches.
  - **Prompt inputs:** `{description}` and `{existing_predicates}`. The second is a JSON list of `{key, label,
    value_type}` for the workspace's predicates.
  - **Intake run:**
    - set `PROMPT_VERSION = 3`;
    - load the existing predicates;
    - call the gateway;
    - run `clean_columns` and log one `brief_intake.column_dropped` per reason;
    - save the config with the cleaned lists, without `name`;
    - then call `apply_drafted_name` if the name is non-blank.
  - **Lock file:** add the v3 sha256 to `LOCK.json`.
- [ ] **Step 4: Run the tests.** Run `uv run pytest packages/signal_core -q` and
  `uv run pytest -k "released_prompts or ai_boundaries" -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(intake): v3 drafts the Brief name, typed columns with roles, evidence rules and assumptions"`

### Task 3: Brief-scoped extraction and column backfill

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/extraction_backfill/v1.md`
- Modify:
  - `packages/signal_core/src/signal_core/modules/m03_extraction.py`;
  - `packages/signal_core/src/signal_core/modules/m01_preview.py` (`_counts`);
  - `services/worker/src/worker/app.py` (dispatcher and two jobs);
  - `prompts/LOCK.json`
- Test:
  - `packages/signal_core/tests/test_m03_extraction.py`;
  - `services/worker/tests/test_worker.py`;
  - `services/worker/tests/test_pipeline_e2e.py`;
  - the m01 preview tests

**Interfaces:**
- Consumes:
  - `predicate_keys`, `column_specs` (Task 2);
  - the event `predicate.backfill_requested` (Task 1).
- Produces, in `m03_extraction`:
  - `BACKFILL_SCOPE_SQL: str`, with params `(workspace_id, brief_id, limit)`, implementing the Global Constraints
    scope;
  - `async scope_predicate_keys(pool, source_record_id: UUID, workspace_id: UUID) -> set[str] | None`. It returns
    the keys of the record's radar Brief's current version. For a record without a radar it returns the union
    over the active Briefs. It returns `None` when no Brief in reach has keys, which means the legacy "send all";
  - `async queue_predicate_backfill(pool, brief_id: UUID, workspace_id: UUID, keys: list[str]) -> int`;
  - `async backfill_record(pool, gateway, source_record_id: UUID, keys: list[str], ctx: CallContext) -> int`. It
    returns the number of claims written;
  - `_validate_claims(...)`, the per-claim validation moved out of `extract_structured` so both paths share it.
- Worker:
  - a task `extract.backfill_record(source_record_id, predicate_keys, org_id, workspace_id)`;
  - the dispatcher branch for `predicate.backfill_requested`, which calls `queue_predicate_backfill`.

- [ ] **Step 1: Write failing tests:**
  - **Scoping:** `test_extraction_sends_only_brief_columns`. A record whose radar Brief has keys
    `{drone_programme, industry}`, in a workspace that also has `canary.pipeline_ok` and `other_key`. The prompt's
    `predicates` JSON lists exactly those two keys.
  - **Legacy:** `test_extraction_legacy_sends_all` when no Brief has specs.
  - **Backfill writes:** `test_backfill_writes_claims_on_existing_mentions`. A record has two mentions: one
    resolved to entity A, and one whose entity is merged. The fake AI returns claims for mention indices 0 and 1.
    Only the claim on A is written; no new entity or mention is created; and `create_claim` gets an `observed_at`
    from `source_date`.
  - **Backfill rerun:** `test_backfill_twice_no_duplicates` asserts the claim count is unchanged.
  - **Backfill skip:** `test_backfill_skips_record_without_resolved_mentions`. No AI call is made, and the
    function returns 0.
  - **Queueing:** `test_queue_backfill_scope_and_cap`. Of three records:
    - the Brief's radar record, extracted, is queued;
    - another Brief's record is not;
    - a pending record is not.

    The queueing lock string matches the Global Constraints format. With the limit patched to 1, the log has
    `extraction.backfill_capped`.
  - **Worker:** `test_dispatch_backfill_event` in `test_worker.py`.
  - **Preview:** `reextract_records` counts the backfill scope, not every record in the workspace.
  - **e2e** (`test_pipeline_e2e.py`): run the pipeline with the Brief fixture until the record is extracted. Then
    publish a new version that adds `site_count`, deliver the event through the dispatcher, and run the jobs. The
    entity now has an active `site_count` claim.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.**
  - **Prompt `extraction_backfill/v1`:** inputs are `{mentions}` (a JSON list of `{index, entity_type, name}`),
    `{predicates}` and `{content}`. The output reuses `ExtractionResult`'s claim and `source_date` fields, with
    `mentions` ignored. Claims may only use the given indices.
  - **`backfill_record` writes:** all of a record's claims are written in one transaction, onto `mention.entity_id`
    for mentions whose entity is not `merged`.
  - **`backfill_record` events and metering:** no status change. `claim.created` events then drive verdicts (P1).
    The `ctx` `job_type` is `"extract.backfill"`.
- [ ] **Step 4: Run the tests.** Run `uv run pytest -q` and the e2e. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(extraction): send only the Brief's columns; backfill new columns over extracted records"`

### Task 4: Rubric verdicts, with the AI only for open cases

**Files:**
- Create: `packages/signal_core/src/signal_core/rubric.py`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/v3.md`
- Modify:
  - `packages/signal_core/src/signal_core/modules/m05_verdict.py`;
  - `prompts/LOCK.json`
- Test:
  - `packages/signal_core/tests/test_rubric.py`;
  - the m05 verdict tests;
  - `services/worker/tests/test_pipeline_e2e.py`

**Interfaces:**
- Consumes: `EvidenceRule`, `ColumnSpec`, `evidence_rules`, `column_specs` (Task 2).
- Produces, in `signal_core.rubric` (pure, no IO):
  - `@dataclass(frozen=True) class RubricClaim`, with fields:
    - `id: str`, `key: str`, `value: Any`;
    - `quote: str | None`, `confidence: int | None`;
    - `observed_at: datetime | None`;
    - `source: str` (the lower-cased URL host, or the source_record id when there is no URL);
  - `@dataclass(frozen=True) class RuleResult`, with fields `predicate`, `kind`, `op`, `met: bool`,
    `touched: bool`, `sources: int`, `claim_ids: list[str]`, plus `as_dict()`;
  - `@dataclass(frozen=True) class RubricOutcome`, with fields
    `value: Literal["Confirmed", "Contradicted", "Unverified"] | None` (None means open),
    `rules: list[RuleResult]` and `claim_ids: list[str]`;
  - `evaluate_rubric(rules: list[EvidenceRule], claims: list[RubricClaim], now: datetime) -> RubricOutcome`.
- `m05_verdict`:
  - `LIVE_CLAIMS_SQL` also selects `observed_at`, `sr.url` and `sr.id` (join `source_record`);
  - `class OpenVerdictOutput(BaseModel)`, with fields:
    - `value: Literal["Likely", "Unverified", "Contradicted"]`;
    - `reasoning: str`;
    - `supporting_claim_indices: list[int]`;
    - `conflicting_claim_indices: list[int] = []`.

**Rubric semantics** (no other reading):
- **A claim satisfies a rule when all of these hold:**
  - its key is the rule's predicate;
  - the op holds:
    - `is_true` / `is_false`: the value is `True` / `False`;
    - `present`: any value;
    - `at_least`: a number ≥ the value;
    - `one_of`: the case-folded value is in the case-folded list;
    - `contains`: any list item is a case-folded substring of the value;
  - `max_age_days` is null or `observed_at` is no older than that;
  - the quote is non-empty.
- **Met:** distinct `source` values among the satisfying claims ≥ `min_sources`.
- **Touched:** any claim on the rule's predicate.
- **Order:**
  1. any `disqualifies` rule met → Contradicted, citing its satisfying claims;
  2. otherwise, at least one `requires` rule exists and all are met → Confirmed, citing the union of their
     satisfying claims;
  3. otherwise, no rule touched → Unverified;
  4. otherwise → open.

- [ ] **Step 1: Write failing tests** in `test_rubric.py`, using the fixture rules:
  - `test_required_met_confirms`;
  - `test_disqualifier_wins_over_required` (both met gives Contradicted);
  - `test_no_rule_touched_unverified` (claims only on `site_count`);
  - `test_two_claims_same_host_count_once`: rule `min_sources: 2`, two satisfying claims from
    `https://news.example.com/a` and `https://NEWS.example.com/b` → open;
  - `test_claim_without_quote_does_not_satisfy` → open;
  - `test_too_old_does_not_satisfy`: `observed_at` 800 days back with `max_age_days` 730 → open;
  - `test_one_of_casefold` (`"Drone Vendor"` matches);
  - `test_contains_and_at_least` on ad-hoc text and number rules.

  m05 tests, each using a gateway that records its calls:
  - **Decided, no AI:** under rubric Confirmed, no AI call is made. The stored `inputs` has
    `method: "rubric"` and `rubric` (a list of rule dicts), and `claim_ids` equals the cited claims.
  - **Open:** verdict v3 is called once. Its prompt carries the hypothesis, the examples (positive and negative),
    the plain rules and the rule results. The stored `inputs` has `method: "rubric_ai"`, `reasoning`,
    `prompt_version: 3` and `rubric`.
  - **Legacy:** a config without rules calls verdict v2, as today. The stored `inputs` now includes
    `method: "ai"` and `reasoning`.
  - **Pydantic guard:** `OpenVerdictOutput` rejects `"Confirmed"`, so an AI returning it is `AiOutputInvalid`,
    which is re-raised and retried.
  - **Human override and conflicts:** the existing tests still pass.

  e2e: the fixture Brief gets a page saying the company "announced a drone programme", with a quote. The verdict
  is Confirmed, and the fake gateway shows no `verdict` call.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement** `rubric.py`, `verdict/v3.md` (its inputs are the hypothesis, examples, rules, rule
  results, entity and claims) and the branching in `_verdict_for_brief`:
  - with rules, run the rubric;
  - a decided outcome is upserted without the AI;
  - an open outcome calls v3;
  - without rules, use the v2 path.

  Keep for every path:
  - Confirmed needs a quote, otherwise it becomes Likely;
  - conflict review;
  - the human-override hold.
- [ ] **Step 4: Run the tests.** Run `uv run pytest -q` and the e2e. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(verdict): deterministic rubric from evidence rules; AI only for open cases, reasoning stored"`

### Task 5: Scoring reads predicate roles

**Files:**
- Modify: `packages/signal_core/src/signal_core/scoring_core.py`
- Test: the scoring_core and m08 tests (find with `grep -rl score_account packages services`)

**Interfaces:**
- Consumes: `column_specs`, `ICP_LIST_BY_ROLE` (Task 2).
- Produces, in `scoring_core`:
  - `LEGACY_ROLES: dict[str, str]`;
  - `predicate_roles(config) -> dict[str, str]`, which returns the specs' roles, or `LEGACY_ROLES` when the
    config has no specs;
  - `value_matches_icp(role: str, value: Any, entries: list[str]) -> bool | None`, where `None` means it can't
    tell;
  - `score_account`, with its signature unchanged.

**Semantics:**
- **Matching:**
  - text roles match when the case-folded value equals an entry, or either contains the other on word
    boundaries;
  - `fit.size` matches a numeric value inside any band parsed as `^(\d+)\s*-\s*(\d+)$` or `^(\d+)\+$`;
  - an unparsable value or band gives `None`.
- **Fit:** for each fit-role predicate with a non-empty ICP list and at least one decisive claim:
  - the predicate scores 100 if any claim matches, otherwise 0;
  - it is weighted by the largest decay among its claims;
  - fit is the rounded weighted mean, or 50 when no predicate counts.
- **ICP gate:** applies to `fit.vertical` and `fit.geography` only, as today, through `value_matches_icp`.
- **Scale:** `scale` role, numeric values only, with today's formula.
- **Timing:** claims on `signal`-role predicates within 90 days. With legacy roles, every claim counts, as
  today.
- **Labels:** the label kinds `vertical`, `geography` and `commercial_role` come from the matching `fit.*` roles.

- [ ] **Step 1: Write failing tests:**
  - **Fit, match:** `test_fit_matches_icp_list`. ICP verticals are `["energy"]`, and the claim `industry` =
    `"Energy"` gives fit 100. The claim `"renewable energy"` also gives 100.
  - **Fit, miss:** `test_fit_mismatch_scores_zero_and_gates`. `industry` = `"drone vendor"` with verticals
    `["energy"]` gives fit 0. A `fit.geography` mismatch gives Excluded, with
    `{"reason": "outside_icp", "predicate": "<key>"}`.
  - **Fit, size:** `test_size_band_numeric`. Value 450 against `["201-1000"]` matches; against `["1000+"]` it
    misses; against `["mid-size"]` it gives `None` and isn't counted.
  - **Scale:** `test_scale_reads_role`. `site_count` = 30 with role `scale` gives `int(log1p(30) * 10)`.
  - **Timing:** `test_timing_counts_signal_only`. An old `industry` claim plus a recent `drone_programme` claim
    gives timing 10.
  - **Legacy:** `test_legacy_config_unchanged_keys`. A config without specs and with a `vertical` claim still
    reads it as fit.
  - **Preview:** existing preview tests still pass, because the preview calls `score_account`.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.**
  - Remove `FIT_PREDICATES`, `QUANTITY_PREDICATES`, `LABEL_PREDICATES` and `ICP_LISTS`.
  - Keep the explanation keys the same, and add `explanation["fit"]["matched"]` / `["missed"]` as lists of
    keys.
- [ ] **Step 4: Run the tests.** Run `uv run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(scoring): fit, scale, timing and labels read predicate roles and ICP lists"`

### Task 6: Brief page — columns, evidence rules, assumptions, rename

**Files:**
- Modify:
  - `apps/web/src/lib/briefs.ts`;
  - `apps/web/src/app/(app)/briefs/[id]/brief-editor.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/page.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/versions/version-diff.tsx`;
  - `apps/web/src/app/actions/briefs.ts`
- Create:
  - `apps/web/src/app/(app)/briefs/[id]/columns-field.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/rules-field.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/rename-form.tsx`
- Delete:
  - `apps/web/src/app/(app)/briefs/[id]/predicates.tsx`;
  - `predicate-add-form.tsx`;
  - the `addPredicate` action

  Columns are now part of the Brief draft, so a change goes through the preview, with its cost, and then the
  backfill.
- Test: `apps/web/e2e/brief.spec.ts`

**Interfaces:**
- Consumes:
  - the RPCs `save_brief_draft_ui` (validating), `rename_brief`, and `create_brief(p_name_auto)` (Task 1);
  - the config shapes (Global Constraints).
- Produces, in `lib/briefs.ts`:
  - `type ColumnSpec`, `type EvidenceRule`;
  - `BriefConfig.predicates?: (ColumnSpec | string)[]`;
  - `BriefConfig.evidence_rules?: EvidenceRule[]`;
  - `BriefConfig.assumptions?: string[]`;
  - `ROLE_LABELS: Record<Role, string>`:
    - signal: "Signal (decides the verdict)";
    - fit.vertical: "Fit: industry";
    - fit.geography: "Fit: country or region";
    - fit.size: "Fit: size";
    - fit.commercial_role: "Fit: commercial role";
    - scale: "Scale";
    - info: "Information only";
  - `OPS_BY_TYPE`;
  - `describeRule(rule, columns) -> string`, e.g.
    `Requires "Runs or plans a drone programme" = yes, from 1 source, in the last 730 days` and
    `Rules out when "Industry" is one of: drone vendor`;
  - `keyFromLabel(label) -> string` (lower snake case, cut to 40 chars, prefixed `c_` if it starts with a digit).

**Behaviour:**
- **Columns ("Columns the AI fills"):**
  - each column shows its label, a type tag, its role and its hint;
  - the editor edits the label, type, role, hint and enum values (as chips);
  - it can add a column (the key comes from the label and can't be edited once saved) and remove one;
  - removing a column also removes its rules, and the editor says so in a line.
- **Rules ("How a verdict is decided"):** each rule is shown as a `describeRule` sentence. The editor offers:
  - a column select;
  - requires / rules out;
  - an op select filtered by the column's type;
  - a value input that fits the op;
  - min sources (1–5) and max age in days (blank means any);
  - add and remove.
- **What I assumed:** a read-only list, shown only when non-empty.
- **Rename:**
  - a small "Rename" button next to the H1 opens an inline form;
  - it is hidden for Viewers;
  - errors show inline.
- **Old section:** "Scoring and rules" keeps only Exclusions, and is renamed "Exclusions".
- **Version diff:** shows column labels and rule sentences, not JSON.
- **New Brief:** `createBrief` passes `p_name_auto: !text(form,'name')`.
- **Design:** use the existing ChipField pattern, the design-system classes and the light tokens; no purple.

- [ ] **Step 1: Write failing e2e tests** in `brief.spec.ts`:
  - **Drafted page:** extend `a drafted Brief shows every part and starts a pilot`. Merge the fixture columns and
    rules into `FLYTBASE_DRAFT`, and add `assumptions: ["Assumed energy includes transmission."]`. The page
    shows:
    - "Runs or plans a drone programme";
    - "Signal (decides the verdict)";
    - `Requires "Runs or plans a drone programme" = yes, from 1 source, in the last 730 days`;
    - the assumption.

    After Start pilot, the database has a `drone_programme` predicate in the workspace.
  - **Add column:** `adding a column and a rule saves to the draft`. Add the column "Hiring drone pilots" as
    boolean with role signal, then a requires rule on it. Saving shows both, and `draft_config.predicates` has
    the key `hiring_drone_pilots`.
  - **Invalid save:** `an invalid rule is refused with the database message`. Save an `at_least` rule through a
    crafted `saveBriefDraft` call, using the page's form with a hidden config value changed. The page shows the
    `22023` message.
  - **Rename:** `renaming a Brief`. The name changes, and a Viewer sees no Rename.
  - **New Brief:** `a Brief created without a name is marked for an AI name`. `name_auto` is `true`.
- [ ] **Step 2: Run the tests and see them fail.** Run `pnpm --filter web e2e -- brief.spec.ts`.
- [ ] **Step 3: Implement** the components and the changes above.
- [ ] **Step 4: Run the gates.**
  - Run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web e2e`. Expected: PASS.
  - Take screenshots of the Brief page with the draft, open editors and an error, on desktop at 1440 px and on a
    phone at 390 px, using the repo's Playwright screen helper (`e2e/screens.spec.ts` pattern).
  - Look at each one. There must be no horizontal scroll and no overflow at 390 px.
- [ ] **Step 5: Commit.** `git commit -m "feat(web): Brief page edits columns, evidence rules, assumptions and the Brief name"`

---

**After Task 6:**
1. Run every gate in Global Constraints on the branch head.
2. Do the whole-branch review, then the independent completion audit (the user's standing request).
3. Push, and open the PR. Its body lists each gap (#1 #8 #11 #7 #15) with its proof, the rulings, the known gaps,
   and the evals that were not run.
