# P3 Column runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Columns fill themselves.
- A column (predicate) has a fill method (`extract` or `formula` in this plan).
- Every fill is recorded as a run, so each cell has one of nine visible states, staleness can be checked, and cost
  is known before and after.
- Views are saved filters over accounts, written in one shared filter syntax.
- Run requests show their cost first and respect the budget.

**Architecture:**
- **Database:**
  - migration 0039 adds the column fields to `predicate`, plus the run table, derived claims and the cell-state
    function;
  - migration 0040 adds the filter compiler, account views and saved views;
  - migration 0041 adds run estimates and requests within budget.

  The filter is compiled in **one place** (SQL), so views, run requests, run conditions and later segments all
  share it.
- **Formula language:** a safe expression language, implemented twice:
  - in Python, which evaluates formulas in the worker;
  - in TypeScript (`packages/formula`), which parses and checks them in the editor.

  One shared case file pins the two to the same behaviour.
- **Worker:**
  - one executor job per run;
  - formula columns re-run automatically when an input claim changes;
  - usage is billed to the run.
- **Web:** the Brief editor can define formula columns. The table UI that shows cells and views is P5.

**Tech Stack:**
- Postgres / Supabase migrations, with pgTAP on PGlite;
- Python 3.13, psycopg 3 async, Procrastinate, pydantic, pytest;
- TypeScript with vitest (new `packages/formula`);
- Next.js, with Playwright e2e that runs in CI only.

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md):
- §2 decisions D1 (one graph, views not copies), D2 (fill methods) and D5 (safe formula language, not JavaScript);
- §5 rules T1 (a cell = latest claim + latest run, nine states), T4 (run controls), T5 (one filter syntax, which can
  test run status), T6 (views and presets) and T9 (cost before action, "first N within budget");
- §8 (the `predicate` fields, `predicate_run`, `saved_view`, the shared filter AST, `usage_ledger` +
  `predicate_run_id`);
- §9 (failures visible, one budget, stale is honest).

Shape detail: [04-mapping §4.1–4.3](../../research/clay/04-mapping.md) and
[05-ia-and-pages §3.1, 3.4–3.6](../../research/clay/05-ia-and-pages.md).
Roadmap: [2026-11-18-redesign-roadmap.md](2026-11-18-redesign-roadmap.md), row P3.

## Global Constraints

- **Branch:** `feat/column-runtime` from `main` (at `ce04231` or later). One PR.
- **New migrations, in order:**
  - `supabase/migrations/20261119000039_column_runtime.sql` (Task 1);
  - `20261119000040_views_filters.sql` (Task 2);
  - `20261119000041_column_runs.sql` (Task 5).

  Never edit a migration released on `main`.
- **Fill methods in this plan:**
  - `extract` (the default; today's behaviour) and `formula`;
  - the `predicate.fill_method` check also allows `provider`, `research` and `manual` for P6 and P5, but no code
    path in this plan creates them.
- **Column spec additions** (items of a Brief's `config.predicates`, extending the P2 table; an absent field means
  the default):

  | Field | Rule |
  |---|---|
  | `fill_method` | `extract` (default) · `formula` |
  | `formula` | required for `formula`, null otherwise; a string of 1–500 chars |
  | `inputs` | `formula` only: the column keys the formula reads, 1–10 distinct keys; each must be another column of the same Brief whose `fill_method` is `extract` or `formula`; never the column's own key |

  - No dependency cycle is allowed among a Brief's formula columns.
  - An evidence rule may not point at a formula column, because derived claims carry no quote and so can never
    satisfy a rule.
  - A key that already exists in the workspace with a different `fill_method` raises `23514` with:
    `column "<key>" already exists in this workspace filled by <method>; give it another key`.
- **Formula language (D5)** — the same rules in Python and TypeScript:
  - **Grammar** (keywords are lower case; whitespace is free):
    ```
    expr  := or
    or    := and ("or" and)*
    and   := not ("and" not)*
    not   := "not" not | cmp
    cmp   := add (("=" | "!=" | "<" | "<=" | ">" | ">=") add)?
    add   := mul (("+" | "-") mul)*
    mul   := unary (("*" | "/") unary)*
    unary := "-" unary | atom
    atom  := NUMBER | STRING | "true" | "false" | "null" | SYSREF | FUNC "(" [expr ("," expr)*] ")" | REF | "(" expr ")"
    NUMBER := [0-9]+ ("." [0-9]+)?
    STRING := '"' ( any char except " and \  |  \"  |  \\ )* '"'
    SYSREF := "@name" | "@domain"
    REF    := [a-z][a-z0-9_]{1,39}, not a keyword and not followed by "("
    ```
  - **Functions** (any other name is a syntax error):
    - `coalesce(a, …)`: the first argument that is not null and not `""`; null if none;
    - `concat(a, …)`: joins the text forms, with null counting as `""`; numbers print in shortest form, so `2.0`
      prints as `2`;
    - `if(c, a, b)`;
    - `contains(text, sub)`: case-insensitive;
    - `lower(t)`, `upper(t)`;
    - `round(x[, d])`: d from 0 to 6, rounding half away from zero;
    - `len(t)`;
    - `is_empty(x)`: true for null or `""`;
    - `days_since(date)`: whole days from a `YYYY-MM-DD` string to the evaluation date.
  - **Values:**
    - a column value is that column's most recent active claim (by `observed_at` desc, then `id`), or null when
      there is none;
    - `@name` is the entity's display name;
    - `@domain` is the entity's primary domain alias, or null when it has none.
  - **Null:** any null argument makes arithmetic, comparison, `lower`, `upper`, `round`, `len`, `contains` and
    `days_since` return null. `/` by zero gives null, and so does a non-finite result.
  - **Truth:** `and`, `or`, `not` and the condition of `if` treat only `true` as true; null counts as false. Any
    other non-boolean value there is a type error.
  - **Types:**
    - `+ - * /` take numbers only;
    - `< <= > >=` compare two numbers or two strings;
    - `= !=` compare any two values of the same type, and null with anything;
    - any other mix is a type error.
  - **Limits:** at most 500 characters, at most 200 tokens and nesting depth at most 32. Anything beyond raises a
    syntax error.
  - **Errors:**
    - `FormulaSyntaxError` carries a message and a 0-based character position;
    - `FormulaTypeError` carries a message.
  - **Parity file:** `packages/formula/cases.json` is the single source of truth. Both implementations must pass
    every case in it.
- **Formula result vs column type:**
  - `boolean`: a bool;
  - `number`: a finite number;
  - `text`: a string;
  - `enum`: a string in `allowed_values`;
  - `date`: a `YYYY-MM-DD` string.

  A null result means the run status is `no_data`. A result of the wrong type means the run status is `error`, with
  the message `formula returned <type>, column is <value_type>`.
- **Run statuses** (`predicate_run.status`): `queued` · `running` · `succeeded` · `no_data` · `condition_not_met` ·
  `error` · `cancelled`.
- **Cell states** (T1; exactly these nine, from `app.cell_state`), checked in this order:
  1. `queued` / `running`: the latest run has that status;
  2. `error`: the latest run errored;
  3. `skipped`: the latest run is `condition_not_met`;
  4. `stale`: there is an active claim, and the latest finished run's `inputs_hash` differs from
     `app.run_inputs_hash` now;
  5. `derived`: there is an active claim and the column is a formula;
  6. `value`: there is an active claim;
  7. `no_data`: the latest run is `no_data`;
  8. `empty`.
- **Inputs hash:** `app.run_inputs_hash(entity, predicate)` is the **only** implementation:
  - `md5(config_hash || '|' || <sorted active claim ids of the input predicates on that entity, comma-joined>)`;
  - for `extract` it is `md5(config_hash || '|')`;
  - the executor stores the value from this function and never computes its own.
- **Config hash:** `predicate.config_hash` is the md5 of the canonical JSON of
  `{fill_method, fill_config, input keys sorted, value_type, allowed_values sorted}`. `predicate.version` goes up
  by 1 whenever `config_hash` changes.
- **Shared filter AST (T5):**
  - **Shape:**
    - `{}` matches everything;
    - groups are `{"all": [F…]}`, `{"any": [F…]}` and `{"not": F}`;
    - a condition is `{"field": FIELD, "op": OP, "value": V}`.
  - **Fields and their ops:**

    | Field | Ops |
    |---|---|
    | `{"column": "<predicate key>"}` | `is_empty` `is_not_empty` `eq` `neq` `gt` `gte` `lt` `lte` `contains` `one_of` `is_true` `is_false`. A column condition holds when **any** active claim of that column on the entity satisfies it; `is_empty` holds when none exists |
    | `{"system": "verdict"}` | `one_of` (Confirmed, Likely, Unverified, Contradicted) |
    | `{"system": "band"}` | `one_of` (High, Medium, Low, Excluded) |
    | `{"system": "stage"}` | `one_of` (the account stage enum) |
    | `{"system": "label", "kind": "<kind>"}` | `one_of` |
    | `{"system": "name"}`, `{"system": "domain"}` | `eq` `contains` `is_empty` `is_not_empty` |
    | `{"system": "created_at"}` | `within_days` `older_than_days` |
    | `{"run": "<predicate key>"}` | `one_of`, over the cell states above |

  - **Brief context:** verdict and band use the given Brief's current version. With no Brief, they hold if any
    Pilot or Active Brief's current version matches.
  - **Limits:** at most 50 conditions and depth at most 8.
  - **Errors:** an unknown field, op, key or value raises `22023`, with a message naming the JSON path, e.g.
    `filter.all[1].field: unknown column "work_emial"`. Values are always bound as parameters or quoted with
    `%L`; they are never pasted into the SQL.
- **Views:**
  - a view lists the company entities that have an `account` in the workspace;
  - `entity_type = 'person'` is stored but returns `22023 person views come with People (P6)`;
  - page size is at most 500.
- **Presets** (T6), returned by `public.list_views` ahead of saved views, always in this order:

  | Preset | Filter |
  |---|---|
  | `ready_to_contact` | verdict one of Confirmed, Likely; band one of High, Medium |
  | `needs_evidence` | verdict one of Unverified |
  | `stale` | any column's run state is `stale` |
  | `errored` | any column's run state is `error` |
  | `by_verdict` | `{}`, sorted by verdict |

  For `stale` and `errored`, "any column" means any predicate of the Brief, or of the workspace when no Brief is
  given.
- **Cost and budget (T9):**
  - **Cost per entity** (`app.column_cost_per_entity(predicate, workspace)`):
    - `formula` and `manual` cost 0;
    - `extract` costs one extract call per extracted source record of the entity: 3000 input and 500 output
      tokens at the Capability.EXTRACT model's prices from the price table `PgPriceBook` reads. The records per
      entity are the workspace average, capped at 20.
  - **Remaining budget:** the smallest `cap − spent` over the workspace and Brief budgets for the current day and
    month, where spent counts usage rows with status `ok` or `reserved`. It is null when there is no cap.
  - **Refused request:** a run request whose estimate exceeds the remaining budget raises `55000` with this exact
    message:
    `this run needs $<est> but $<left> is left in the budget; run the first <N> within budget`.
  - **Request size:** at most 1000 runs per request.
- **Run modes (T4):**
  - `empty`: cell state `empty` or `no_data`;
  - `stale`;
  - `errored`;
  - `selected`: the given entity ids, whatever their state.

  An entity that already has a queued or running run for that column is skipped, never queued twice.
- **Auto-run:**
  - a formula column with `auto_run = true` (the default for formula) re-runs for an entity when a claim of one of
    its inputs is created;
  - `extract` columns are never auto-run by this plan (the pipeline and the P2 backfill cover them);
  - queueing lock for any run job: `run:<predicate_run_id>`.
- **Gates before calling the work done:**
  - `pnpm db:check`;
  - `uv run pytest -q` and `uv run pytest services/worker/tests/test_pipeline_e2e.py`;
  - `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`;
  - `pnpm typecheck`, `pnpm lint` and `pnpm --filter @signal/formula test`;
  - Playwright through CI on the PR, with the Brief page screenshots looked at.
- **No live prompt evals:** no prompt changes in this plan.

## Review Focus

1. **A formula cycle across columns.** For example `a = b + 1` and `b = a + 1`, or a column that reads itself.
   Saving the draft fails with `22023`, the message names the cycle, and nothing is saved. *pgTAP in Task 1.*
2. **A formula whose inputs are all empty** (`coalesce(work_email, personal_email)` with neither present). The run
   ends `no_data`. No claim is written, the cell shows `no_data` (never `value`), and no error appears. *Test in
   Task 5.*
3. **A filter value with a quote or SQL in it** (`O'Brien`, `x'); drop table claim; --`). It matches literally and
   never breaks or changes the query. An unknown column key gives the `22023` path message. *pgTAP in Task 2.*
4. **A run request larger than the budget.** It is refused with the exact message and the N that fits. Asking
   again with that N as the limit succeeds. The estimate equals the count times the cost per entity. *pgTAP in
   Task 5.*
5. **A duplicate or late run.**
   - The same request twice while runs are queued queues no second run.
   - A redelivered job for a finished run does nothing.
   - A formula re-run with unchanged inputs writes no new claim and emits no `claim.created`, so formulas cannot
     loop.

   *Tests in Task 5.*

---

### Task 1: Migration 0039 — column fields, runs, derived claims, cell state

**Files:**
- Create: `supabase/migrations/20261119000039_column_runtime.sql`
- Create: `supabase/tests/database/23_column_runtime.test.sql`

**Interfaces:**
- **Produces, on `predicate`:**
  - `fill_method text not null default 'extract'`, checked against `extract`, `provider`, `research`, `formula`
    and `manual`;
  - `fill_config jsonb not null default '{}'`, holding `{"formula": "..."}` for formula columns;
  - `input_predicate_ids uuid[] not null default '{}'`;
  - `run_condition jsonb` (a filter AST, or null);
  - `auto_run boolean not null default false`;
  - `version int not null default 1`;
  - `config_hash text`.

  Existing rows are backfilled with `config_hash`.
- **Produces, `public.predicate_run`:** the 04 §4.2 sketch with the Global Constraints statuses and these
  columns:
  - `id`, `org_id`, `workspace_id`, `entity_id`, `predicate_id`;
  - `predicate_version`, `brief_version_id` (nullable);
  - `status`, `inputs_hash text`, `not_run_reason jsonb`;
  - `cost_usd numeric(14,6) not null default 0`;
  - `claim_id`, `inputs jsonb` (the input claim ids used), `error text`;
  - `queued_at`, `started_at`, `finished_at`.

  It has the index `(entity_id, predicate_id, queued_at desc)`, a partial unique index that allows one
  queued-or-running run per `(entity_id, predicate_id)`, RLS read for Viewer+, and no direct writes.
- **Produces, ledger column:** `usage_ledger.predicate_run_id uuid references public.predicate_run(id)`.
- **Produces, extended P2 functions:** `app.validate_brief_columns` gains the column-spec additions and cycle
  check. The sync trigger writes `fill_method`, `fill_config`, `input_predicate_ids`, `auto_run` (true for formula)
  and `config_hash`; it bumps `version` when the hash changes, and raises the fill-method `23514`.
- **Produces, derived claims:** `app.derived_source_record(p_ws uuid, p_kind text) returns uuid` gets or creates
  one `source_record` per workspace and kind `formula`:
  - `connector_id 'derived:formula'`, `source_type 'formula'`;
  - `extraction_status 'skipped'` (or the enum value that keeps it out of the extraction pipeline; check the enum);
  - `content_hash 'derived:formula:<ws>'`;
  - one `extractor_version` row (`capability 'formula'`, `prompt_version 'formula-v1'`, `model 'none'`,
    `parser_version '1'`).
- **Produces, `app.write_formula_claim(p_run uuid, p_value jsonb) returns uuid`** (service role):
  - if an active formula claim with that value already exists for (entity, predicate), it links the run and
    returns that claim, with no event;
  - otherwise it supersedes the other active formula claims for (entity, predicate), calls `app.create_claim`
    with confidence 100, `observed_at now()` and no quote, and links the run.
- **Produces, inputs hash and cell state:**
  - `app.run_inputs_hash(p_entity uuid, p_predicate uuid) returns text`, as in Global Constraints;
  - `app.cell_state(p_entity uuid, p_predicate uuid) returns text`, the nine states in Global Constraints order;
  - `public.cells(p_ws uuid, p_entity_ids uuid[], p_predicate_ids uuid[])` returns `table(entity_id,
    predicate_id, state, value jsonb, confidence, claim_id, run_id, run_status, error, not_run_reason, cost_usd)`.
    It is Viewer+, with at most 500 entities × 50 predicates.

- [ ] **Step 1: Write the failing pgTAP file.** It reuses the P2 fixture Brief (see
  `22_brief_columns_rubric.test.sql` for setup). Assertions:
  - **Formula spec accepted and synced:** a spec
    `{key: "has_programme_and_sites", fill_method: "formula", formula: "drone_programme and site_count > 0",
    inputs: ["drone_programme", "site_count"], value_type: "boolean", role: "info"}` validates. After publish, the
    predicate has `fill_method 'formula'`, `input_predicate_ids` equal to the two ids, and `auto_run` true.
  - **Formula spec refused** (`22023`), for each of:
    - formula missing;
    - `inputs` empty;
    - an input not in the Brief;
    - its own key as an input;
    - the cycle `a`→`b`→`a`, where the message names both keys;
    - a rule pointing at a formula column;
    - 11 inputs.
  - **Fill-method clash:** an existing `extract` key republished as `formula` raises the `23514` fill-method
    message.
  - **Version and hash:** republishing the same spec keeps `version` at 1. Changing the formula moves it to 2 and
    changes `config_hash`.
  - **Formula claims:** `write_formula_claim` called twice with the same value gives one claim and one
    `claim.created` outbox row. A new value supersedes the old claim, which ends with `status 'superseded'`, and
    creates one new claim.
  - **The derived source record:** it is never `pending`, so extraction never picks it up.
  - **The nine cell states:** each is produced from seeded runs and claims, including `stale` (insert a claim on
    an input after a succeeded run) and `derived`.
  - **Run uniqueness:** a second queued run for the same cell violates the partial unique index.
  - **Access:** `cells` refuses a non-member with `42501`.
- [ ] **Step 2: Run it and see it fail.** Run `node scripts/check-pgtap.mjs`. Expected: suite 23 fails.
- [ ] **Step 3: Write the migration.**
  - Replace the P2 functions with `create or replace`, keeping every P2 check.
  - Add `predicate_run` to the enum and codegen files the way 0038 did, if `db:check` requires it.
- [ ] **Step 4: Run the gates.** Run `pnpm db:check`. Expected: all suites pass, with 22 unchanged.
- [ ] **Step 5: Commit.** `git commit -m "feat(db): column fill methods, predicate runs, formula claims, cell state"`

### Task 2: Migration 0040 — one filter syntax, account views, saved views

**Files:**
- Create: `supabase/migrations/20261119000040_views_filters.sql`
- Create: `supabase/tests/database/24_views_filters.test.sql`

**Interfaces:**
- **Consumes:** `app.cell_state` (Task 1).
- **Produces, the compiler:** `app.filter_where(p_ws uuid, p_brief uuid, p_filter jsonb, p_path text default
  'filter') returns text`. It turns the AST into a SQL boolean expression over the alias `e` (entity) and `a`
  (account). Every value goes through `%L` or `format`. It is the only filter compiler.
- **Produces, the view query:** `public.view_entities(p_ws uuid, p_brief uuid, p_filter jsonb, p_sort jsonb,
  p_limit int, p_offset int)` returns `table(entity_id uuid, total bigint)`. It is Viewer+.
  - Sort items:
    - `{"system": "name" | "created_at" | "priority" | "verdict", "dir": "asc" | "desc"}`;
    - `{"column": key, "dir"}`, which sorts by the latest active claim value.
  - The default sort is `name asc`.
- **Produces, `public.saved_view`:** the 04 §4.3 sketch, with:
  - `brief_id` nullable;
  - `columns jsonb`, a list of `{kind: "predicate" | "system", ref, width, pinned}`;
  - `filter`, `sort` and `layout` (`table`, `board` or `map`);
  - `visibility` (`personal` or `workspace`);
  - `preset text` (null for saved views);
  - `created_by`.

  RLS: workspace views are readable by Viewer+; personal views by their creator only.
- **Produces, RPCs:**
  - `public.save_view(p_ws, p_id uuid default null, p_name text, p_brief uuid, p_columns jsonb, p_filter jsonb,
    p_sort jsonb, p_layout text, p_visibility text) returns uuid`:
    - Editor+ for workspace visibility, any member for personal;
    - it validates the filter by compiling it, and the sort.
  - `public.delete_view(p_id)`, allowed for the creator or an Admin;
  - `public.list_views(p_ws, p_brief uuid default null)` returns the five presets (Global Constraints order) and
    then the saved views visible to the caller.

- [ ] **Step 1: Write the failing pgTAP file.** Seed 4 accounts with claims, verdicts, scores, stages, labels and
  runs. Assert:
  - **Column ops:** each column op on boolean, number, enum, text and date values, including the "any claim
    matches" rule and `is_empty`;
  - **Verdict and band:** with a Brief given, and with no Brief (match under any active Brief);
  - **Other system fields:** stage, label kind and value, name contains, domain, and `created_at within_days`;
  - **Run state:** `{"run": key}` with `one_of ["error", "stale"]` matches exactly the seeded cells;
  - **Groups:** nested `all`, `any` and `not`, and `{}` matching all 4 accounts;
  - **Review Focus 3:** `O'Brien` and `x'); drop table claim; --` match literally, and the claim table is still
    there afterwards;
  - **Errors:** `22023` with these exact path messages:
    - `filter.all[1].field: unknown column "work_emial"`;
    - an unknown op;
    - `one_of` with a non-list;
    - 51 conditions;
    - depth 9;
  - **Paging and sort:** `limit 2 offset 2` returns the right page and `total` is 4. Each sort and direction is
    covered;
  - **Saved views:**
    - `save_view` refuses a Viewer for workspace visibility;
    - a personal view is invisible to another member;
    - `list_views` returns the 5 presets first, in order;
  - **Person views:** `entity_type 'person'` raises the `22023` P6 message.
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Write the migration.** `filter_where` is a recursive plpgsql function that walks the AST with a
  path string for error messages.
- [ ] **Step 4: Run the gates.** Run `pnpm db:check`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(db): one filter syntax, account views, saved views and presets"`

### Task 3: Python formula engine and formula column specs

**Files:**
- Create: `packages/signal_core/src/signal_core/formula.py`
- Create: `packages/formula/cases.json` (the parity file; Task 4 adds the package around it)
- Modify:
  - `packages/signal_core/src/signal_core/brief_columns.py`;
  - `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Test:
  - `packages/signal_core/tests/test_formula.py`;
  - `packages/signal_core/tests/test_brief_columns.py`;
  - `packages/signal_core/tests/test_m03_extraction.py`

**Interfaces:**
- **Produces, in `signal_core.formula`:**
  - `class FormulaSyntaxError(ValueError)` with `.position: int`;
  - `class FormulaTypeError(ValueError)`;
  - `parse(expr: str) -> Node` (an immutable AST of dataclasses);
  - `references(node: Node) -> list[str]` (column keys, sorted and distinct);
  - `evaluate(node: Node, values: Mapping[str, JsonScalar], system: Mapping[str, str | None], today: date) ->
    JsonScalar`;
  - `check_result(value: JsonScalar, value_type: str, allowed_values: list[str] | None) -> str | None` (an error
    message or None, per Global Constraints).
- **Produces, in `brief_columns`:**
  - `ColumnSpec` gains `fill_method`, `formula` and `inputs`, with the Global Constraints rules;
  - `clean_columns` also parses each formula, drops a spec whose formula fails to parse or whose `inputs` ≠
    `references(parse(formula))`, and drops cycles and rules on formula columns;
  - `extract_keys(config) -> list[str]` returns the keys of `extract` columns only.
- **Changes in `m03_extraction`:** `scope_predicate_keys` and the backfill send only `extract` columns to the
  extractor. A formula column must never reach an extraction prompt.

- [ ] **Step 1: Write `packages/formula/cases.json` and the failing tests.**
  - **The cases file:** at least 60 cases, each
    `{"name", "expr", "values": {...}, "system": {"name", "domain"}, "today": "2026-11-19", "expect": <value> |
    {"error": "syntax" | "type"}}`. Cover:
    - each grammar rule, precedence (`not a = b`, `1 + 2 * 3`, unary minus);
    - every function, including `round` half away from zero (`round(2.5)` = 3, `round(-2.5)` = −3,
      `round(1.005, 2)`);
    - null propagation, `coalesce` skipping `""`, `concat` with a number and with null;
    - truth rules;
    - each type error;
    - `/` by zero;
    - the limits (501 chars, depth 33);
    - unknown function, a keyword used as a ref, `@name` and `@domain`;
    - `days_since`;
    - string escapes;
    - case-insensitive `contains`.
  - **`test_formula.py`:** runs every case. It also asserts that `references` returns the sorted distinct keys,
    and checks `check_result` for each value_type, including an enum outside its allowed values and an infinite
    number.
  - **Brief columns tests:** a valid formula spec is kept. Each Global Constraints violation is dropped with a
    reason, including an `inputs` mismatch with the parse and a cycle.
  - **m03 test:** `test_formula_columns_never_reach_extraction`.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement.**
  - A hand-written tokenizer and recursive-descent parser. No `eval`, `ast` or regex-built code execution.
  - Numbers are Python floats; integers stay ints where the input and every operation are integral, so `1 + 2` is
    `3`.
- [ ] **Step 4: Run the gates.** Run `uv run pytest -q`, ruff and mypy. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(formula): safe expression language for formula columns; extraction skips formula columns"`

### Task 4: `@signal/formula` — the same language in TypeScript

**Files:**
- Create:
  - `packages/formula/package.json` (`@signal/formula`, `"test": "vitest run"`, `"typecheck"`);
  - `packages/formula/tsconfig.json`;
  - `packages/formula/src/index.ts`;
  - `packages/formula/src/formula.test.ts`
- Modify:
  - `pnpm-workspace.yaml` (add `packages/formula`);
  - `apps/web/package.json` (depend on `@signal/formula`);
  - the turbo config, if needed, so `pnpm test` and `pnpm typecheck` include it

**Interfaces:**
- **Consumes:** `packages/formula/cases.json` (Task 3).
- **Produces:**
  - `parse(expr: string): Node`;
  - `references(node: Node): string[]`;
  - `evaluate(node, values, system, today: string): Scalar`;
  - `class FormulaSyntaxError extends Error { position: number }`;
  - `class FormulaTypeError extends Error`;
  - `checkResult(value, valueType, allowedValues): string | null`.

  The names and behaviour mirror Task 3 exactly.

- [ ] **Step 1: Write the failing test.** `formula.test.ts` loads `cases.json` and runs every case, with the same
  expectations as Python. It adds `references` and `checkResult` tests mirroring Task 3.
- [ ] **Step 2: Run it and see it fail.** Run `pnpm --filter @signal/formula test`.
- [ ] **Step 3: Implement.** No `eval`, no `Function`, no dependencies.
- [ ] **Step 4: Run the gates.** Run `pnpm --filter @signal/formula test`, `pnpm typecheck` and `pnpm lint`.
  Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(formula): TypeScript twin of the formula language, held to the shared cases"`

### Task 5: Run estimates, budget-aware requests and the executor

**Files:**
- Create:
  - `supabase/migrations/20261119000041_column_runs.sql`;
  - `supabase/tests/database/25_column_runs.test.sql`;
  - `packages/signal_core/src/signal_core/column_runtime.py`
- Modify:
  - `packages/signal_core/src/signal_core/metering.py` (`CallContext.predicate_run_id: UUID | None = None`;
    `PgBudgetGuard.settle` writes it to `usage_ledger`);
  - `services/worker/src/worker/app.py` (the `column.run` job and dispatcher branches)
- Test:
  - `packages/signal_core/tests/test_column_runtime.py`;
  - `services/worker/tests/test_worker.py`;
  - `services/worker/tests/test_pipeline_e2e.py`

**Interfaces:**
- **Consumes:**
  - Task 1: `predicate_run`, `app.run_inputs_hash`, `app.cell_state`, `app.write_formula_claim`;
  - Task 2: `app.filter_where`;
  - Task 3: `parse`, `references`, `evaluate`, `check_result`;
  - P2: `backfill_record(pool, gateway, source_record_id, keys, ctx)`.
- **Produces, SQL (0041):**
  - `alter type public.event_type add value 'column.run_requested'`;
  - `app.column_cost_per_entity(p_predicate uuid, p_ws uuid) returns numeric` (priced in the requesting workspace);
  - `app.remaining_budget_usd(p_ws uuid, p_brief uuid) returns numeric`, null when there is no cap;
  - `public.estimate_column_run(p_ws, p_predicate, p_mode, p_brief, p_filter, p_entity_ids)` returns
    `jsonb {count, per_entity_usd, cost_usd, remaining_usd, within_budget}`. It is Viewer+. `within_budget` is
    the largest N whose cost fits, or `count` when there is no cap;
  - `public.request_column_run(p_ws, p_predicate, p_mode, p_brief, p_filter, p_entity_ids, p_limit int default
    null)` returns `jsonb {queued, cost_usd}`. It is Editor+. It:
    - inserts the queued runs, with `predicate_version` and `brief_version_id` (the Brief's current version);
    - skips cells that already have a queued or running run;
    - raises the budget `55000` message or a size error;
    - emits `column.run_requested` with `{run_ids}`;
  - `app.queue_auto_runs(p_claim uuid) returns uuid[]` (service role). For each formula predicate with `auto_run`
    whose inputs include the claim's predicate, on that claim's entity, it queues one run unless one is already
    queued or running.
- **Produces, Python (`signal_core.column_runtime`):** `async execute_run(pool, gateway, run_id: UUID) -> str`.
  It returns the final status.
  - **Already handled:** it no-ops and returns the current status unless the run is `queued`.
  - **Start:** it sets `running` and `started_at`, and stores `inputs_hash = app.run_inputs_hash(...)`.
  - **Run condition:** when one is set, the entity must match it (through `app.filter_where`). Otherwise the
    status is `condition_not_met`, with `not_run_reason {"condition": <ast>}`.
  - **Formula columns:**
    - parse the formula, and error if `references` ⊄ the inputs;
    - load the input values (most recent active claim per key) and the system values;
    - evaluate, then check the result:
      - null gives `no_data`;
      - a type mismatch gives `error`;
      - otherwise `app.write_formula_claim`, then `succeeded`;
    - the input claim ids go in `inputs`.
  - **Extract columns:** call `backfill_record` with `[key]` over the entity's extracted source records, with
    `ctx.predicate_run_id` set. Afterwards an active claim gives `succeeded` (with `claim_id`); none gives
    `no_data`.
  - **Finish:**
    - `cost_usd` is the sum of `usage_ledger.cost_usd` for the run, with `finished_at` set;
    - any exception sets `error` with the message, and the job does not retry a run marked `error`.
- **Worker:**
  - a task `column.run(run_id)` with lock `run:<run_id>`;
  - a dispatcher branch for `column.run_requested`, which defers one job per run id;
  - on `claim.created`, it calls `app.queue_auto_runs` and defers each run returned;
  - malformed payloads are logged and skipped (P2 pattern).

- [ ] **Step 1: Write failing tests.**
  - **pgTAP 25:**
    - estimates for each mode, from seeded cell states;
    - a request queues the right entities and skips one already queued;
    - **Review Focus 4:** a budget cap of $0.05 with an estimate of $0.12 refuses with the exact message and N.
      A re-request with `p_limit` N succeeds, and the estimate equals count × per-entity cost;
    - formula costs 0, and has no budget refusal even with a $0 left;
    - `queue_auto_runs` queues dependents only, and never twice;
    - a Viewer can't request;
    - 1001 runs are refused.
  - **`test_column_runtime.py`** (FakeConn style, as in the P2 tests):
    - **formula:** succeeded, writing a claim;
    - **Review Focus 2:** all inputs empty gives `no_data` with no claim;
    - a type mismatch errors with the exact message;
    - a reference outside the inputs errors;
    - condition not met gives `condition_not_met`;
    - **extract:** calls `backfill_record` with `[key]` and `predicate_run_id`;
    - **Review Focus 5:** a non-queued run is a no-op;
    - an exception sets `error`.
  - **`test_worker.py`:** the `column.run_requested` dispatch, the `claim.created` → auto-run dispatch, and
    malformed payloads skipped.
  - **e2e** (`test_pipeline_e2e.py`), using the P2 fixture Brief plus the Task 1 formula column:
    1. extracting the drone page creates the input claim;
    2. the auto-run computes `has_programme_and_sites`, which `cells` reports as `derived`;
    3. a new `site_count` claim makes the cell `stale` until the auto-run finishes and it is `derived` again;
    4. **Review Focus 5:** re-running with unchanged inputs writes no new claim.
- [ ] **Step 2: Run the tests and see them fail.**
- [ ] **Step 3: Implement** the migration, `column_runtime.py`, the metering field and the worker wiring.
- [ ] **Step 4: Run the gates.** Run `pnpm db:check`, `uv run pytest -q`, the e2e, ruff and mypy. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(runs): cost-first, budget-aware column runs; executor for extract and formula; formula auto-run"`

### Task 6: Brief editor — formula columns

**Files:**
- Modify:
  - `apps/web/src/lib/briefs.ts`;
  - `apps/web/src/app/(app)/briefs/[id]/columns-field.tsx`;
  - `apps/web/src/app/(app)/briefs/[id]/rules-field.tsx`;
  - `apps/web/e2e/brief.spec.ts`;
  - the Brief screenshot spec
- Test: `apps/web/e2e/brief.spec.ts` (runs in CI)

**Interfaces:**
- **Consumes:**
  - `@signal/formula` (Task 4): `parse`, `references`, `FormulaSyntaxError`;
  - the Task 1 SQL validation messages.
- **Produces, in `lib/briefs.ts`:**
  - `ColumnSpec` gains `fill_method?: 'extract' | 'formula'`, `formula?: string | null` and
    `inputs?: string[]`;
  - `FILL_LABELS = { extract: 'Found in sources', formula: 'Formula' }`.

**Behaviour:**
- **Read view:**
  - each column shows its fill label;
  - a formula column shows its expression in monospace, and its inputs as the labels of the columns it reads.
- **Editor:**
  - a "Filled by" select (Found in sources / Formula);
  - for a formula, an expression textarea parsed on every change with `@signal/formula`. A syntax error shows
    `Formula: <message> (at character <position + 1>)` under the box and blocks Save;
  - unknown references show `Formula reads "<key>", which is not a column of this Brief` and block Save;
  - `inputs` are set from `references()`;
  - the hint field is hidden for formula columns.
- **Rules editor:** formula columns are left out of the Column select.
- **Cycles:** the database refuses them. The message shows inline as the P2 errors do.

- [ ] **Step 1: Write failing e2e tests** in `brief.spec.ts`:
  - **Saving a formula:** `a formula column saves its expression and inputs`. Add "Programme and sites" as a
    formula with `drone_programme and site_count > 0`. Saving shows the expression, and
    `draft_config.predicates` has `fill_method 'formula'` and `inputs ["drone_programme", "site_count"]`;
  - **Syntax error:** `a formula with a syntax error blocks Save`. Typing `drone_programme and` shows the error
    with its position, and Save is disabled;
  - **Unknown column:** `a formula reading an unknown column blocks Save`;
  - **Cycle:** `a formula cycle is refused with the database message`;
  - **Rules:** `formula columns are not offered as rule columns`;
  - **Screenshots:** add a formula column to the screenshot test's draft and an "editor open" shot. Keep the
    390 px overflow check.
- [ ] **Step 2: Confirm the failure in CI or by reading** (no local Docker; same rule as P2).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the gates.** Run `pnpm typecheck`, `pnpm lint` and `pnpm --filter web build`. Expected: PASS.
  CI runs Playwright, and the controller looks at the `brief-screens` artifact.
- [ ] **Step 5: Commit.** `git commit -m "feat(web): Brief editor defines formula columns, checked as you type"`

---

**After Task 6:**
1. Run every gate on the branch head.
2. Do the whole-branch review, then the independent completion audit.
3. Open the PR as a draft early so CI runs. Mark it ready when it is clean.
4. The PR body states what P3 makes possible but does not show yet: the table, cell panel and run buttons are P5.
