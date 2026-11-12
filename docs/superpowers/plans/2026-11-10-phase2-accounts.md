# Phase 2 — Accounts

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Accounts layer: M8 scoring + segmentation, M6 research, M7 enrichment, M11 account workspace + stage machine, M15 account cards, and M17 full health monitoring — so every discovered account can be scored, researched, enriched with contacts, and tracked through its lifecycle on a fully derived account page.

**Architecture:** The derivation engine pattern from Phase 1e extends to `score`, `label`, `account_stage_event`, and `account_card` (all already guarded in migration 0009). New tables for `research_doc`, `contact_point`, `segment`, and `data_completeness` are added in one migration (0022). Python modules m08–m17 each own exactly the tables the PRD assigns them and write only through `app.*` SECURITY DEFINER RPCs. Worker tasks follow the existing `@job` / kill-switch / `CallContext` pattern. Web pages are read-only RSC with one exception: the account page exposes write actions (comment, file upload, ownership assignment) through server actions calling RPCs.

**Tech Stack:** PostgreSQL RPC + guard-trigger bypass (derivation pattern), psycopg3 async, Procrastinate jobs, Pydantic v2, AiGateway (WRITE for research/account-card, EMBED for indexing), Next.js 16 App Router.

**Spec:** `docs/PRD.md` §M6, §M7, §M8, §M11, §M15, §M17; `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §6 Phase 2

## Global Constraints

- S8 write discipline: all INSERT/UPDATE/DELETE goes through `app.*` SECURITY DEFINER RPCs; `authenticated` gets SELECT only on public tables; no direct-INSERT in Python or TypeScript
- Every migration: REVOKE EXECUTE FROM public, anon, authenticated BEFORE every GRANT block
- All derivation RPCs (`upsert_score`, `upsert_label`, `upsert_account_stage`, `upsert_account_card`) have NO GRANT to authenticated — called via service_role only
- Kill switches must be seeded in `job_type_config` for every new job type
- psycopg3 placeholder syntax: `%s` (not `$1`/`$2`)
- `band` enum: 'High', 'Medium', 'Low', 'Excluded' (gate match sets band Excluded; Unverified verdict caps at Low)
- `stage` enum: 'Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off', 'Dormant', 'Disqualified'
- `contact_verification_status` enum: 'valid', 'risky', 'invalid', 'unknown'
- Derivation tables already guarded in migration 0009: `score`, `label`, `account_stage_event`, `account_card` — do not re-create or re-add triggers
- `app.guard_derived()` trigger is already on `score`, `label`, `account_stage_event`, `account_card` — set `app.writer = 'derivation'` transaction-locally inside each RPC
- All derivation RPCs are SECURITY DEFINER — `set_config('app.writer','derivation',true)` with `is_local=true` (the 2nd `true` in Postgres syntax)
- Research write-back rule: facts found during research are written as `claim` rows with `source_record_id` pointing to the fetched source; the `research_doc` itself is never a claim's evidence
- `contact_point.scope` is always `'tenant'` per M7 spec
- Enrichment caps: enforced via S6 `app.reserve_budget()` before every paid provider call
- Score explanation must be a JSON object with keys `fit`, `evidence`, `scale`, `timing`, `reachability`, `gates`, each citing specific claim IDs

## Review Focus

1. **Guard bypass inside the same transaction**: `set_config('app.writer','derivation',true)` must pass `true` as the third argument (`is_local`) — without it the config persists beyond the transaction and allows unguarded writes.
2. **Verdict caps band**: an account with `verdict_value = 'Unverified'` must never get band 'High' or 'Medium'; the scoring RPC must enforce this gate in SQL, not only in Python.
3. **Never pay twice for enrichment**: `app.create_contact_point()` must check for an existing unexpired valid `contact_point` for the same `(entity_id, type)` before the Python module even calls the provider; the check belongs in SQL to prevent races.
4. **Stage machine idempotency**: `app.upsert_account_stage()` must be safe to call twice for the same event — if `account_stage_event` already has a row for `(account_id, activity_id)` it should return the existing row's id without updating `account.stage`.
5. **Research doc claims need a real source_record**: `app.create_claim()` enforces `source_record_id NOT NULL` via the existing schema; research write-back must pass the fetched page's `source_record_id`, never NULL.

---

## File Map

| File | Action |
|---|---|
| `supabase/migrations/20261110000022_phase2_accounts.sql` | Create |
| `supabase/tests/database/11_scoring.test.sql` | Create |
| `supabase/tests/database/12_research_enrichment.test.sql` | Create |
| `supabase/tests/database/13_stage_machine.test.sql` | Create |
| `supabase/tests/database/14_account_card.test.sql` | Create |
| `packages/signal_core/src/signal_core/modules/m08_scoring.py` | Create |
| `packages/signal_core/src/signal_core/modules/m06_research.py` | Create |
| `packages/signal_core/src/signal_core/ai_gateway/prompts/research/v1.md` | Create |
| `packages/signal_core/src/signal_core/modules/m07_enrichment.py` | Create |
| `packages/signal_core/src/signal_core/modules/m11_stage.py` | Create |
| `packages/signal_core/src/signal_core/modules/m15_account_card.py` | Create |
| `packages/signal_core/src/signal_core/ai_gateway/prompts/account_card/v1.md` | Create |
| `packages/signal_core/src/signal_core/modules/m17_health.py` | Create |
| `services/worker/src/worker/app.py` | Modify (add 7 new jobs + cron) |
| `packages/signal_core/src/signal_core/modules/m05_verdict.py` | Modify (defer score + account_card jobs after verdict write) |
| `packages/signal_core/src/signal_core/modules/m04_identity.py` | Modify (defer stage.advance job after account create/update) |
| `packages/signal_core/tests/test_m08_scoring.py` | Create |
| `packages/signal_core/tests/test_m06_research.py` | Create |
| `packages/signal_core/tests/test_m07_enrichment.py` | Create |
| `packages/signal_core/tests/test_m11_stage.py` | Create |
| `packages/signal_core/tests/test_m15_account_card.py` | Create |
| `packages/signal_core/tests/test_m17_health.py` | Create |
| `apps/web/src/app/(app)/workspace/[wsid]/accounts/page.tsx` | Create |
| `apps/web/src/app/(app)/workspace/[wsid]/accounts/[aid]/page.tsx` | Create |

---

### Task 1: DB migration 0022 — Phase 2 tables + derivation write-path RPCs + kill-switch seeds

**Files:**
- Create: `supabase/migrations/20261110000022_phase2_accounts.sql`

**Interfaces:**
- Produces: `public.research_doc(id, org_id, workspace_id, account_id, brief_version_id, entity_id, version, content jsonb, source_record_ids uuid[], published_at timestamptz, inputs jsonb, created_at timestamptz)` — unique on `(account_id, brief_version_id, version)`
- Produces: `public.research_job(id, org_id, workspace_id, account_id, brief_version_id, status text, started_at, finished_at, inputs jsonb, error text, created_at)`
- Produces: `public.contact_point(id, org_id, workspace_id, entity_id uuid NOT NULL, account_id uuid, type text CHECK IN ('email','phone','profile'), value_encrypted text, status contact_verification_status DEFAULT 'unknown', verified_at timestamptz, provider text, scope data_scope DEFAULT 'tenant', created_at)` — unique on `(entity_id, type, value_encrypted)`
- Produces: `public.enrichment_job(id, org_id, workspace_id, account_id, brief_version_id, status text, started_at, finished_at, inputs jsonb, error text, created_at)`
- Produces: `public.segment(id, org_id, workspace_id, brief_id uuid, name text, filter jsonb, created_by uuid, created_at)`
- Produces: `public.data_completeness(id bigint identity, org_id, workspace_id, pipeline text, period_start timestamptz, period_end timestamptz, expected int, actual int, gap_pct numeric(5,2) generated, recorded_at)`
- Produces: `app.upsert_score(p_account_id uuid, p_brief_version_id uuid, p_fit smallint, p_evidence smallint, p_scale smallint, p_timing smallint, p_reachability smallint, p_priority numeric, p_readiness smallint, p_band public.band, p_gates jsonb, p_explanation jsonb, p_inputs jsonb) RETURNS uuid` — SECURITY DEFINER; sets app.writer=derivation; ON CONFLICT (account_id, brief_version_id) DO UPDATE; emits `score.band_changed` when band changes
- Produces: `app.upsert_label(p_account_id uuid, p_brief_version_id uuid, p_kind text, p_value text, p_inputs jsonb) RETURNS uuid` — SECURITY DEFINER; sets app.writer=derivation; ON CONFLICT (account_id, brief_version_id, kind, value) DO UPDATE
- Produces: `app.upsert_account_stage(p_account_id uuid, p_to_stage public.stage, p_activity_id uuid, p_occurred_at timestamptz) RETURNS uuid` — SECURITY DEFINER; sets app.writer=derivation; inserts account_stage_event; updates account.stage; emits `account.stage_changed`; idempotent on `(account_id, activity_id)`
- Produces: `app.upsert_account_card(p_account_id uuid, p_brief_version_id uuid, p_content jsonb, p_inputs jsonb) RETURNS uuid` — SECURITY DEFINER; sets app.writer=derivation; ON CONFLICT (account_id, brief_version_id) DO UPDATE
- Produces: `app.create_research_doc(p_org_id uuid, p_workspace_id uuid, p_account_id uuid, p_brief_version_id uuid, p_entity_id uuid, p_version int, p_content jsonb, p_source_record_ids uuid[], p_inputs jsonb) RETURNS uuid` — SECURITY DEFINER; inserts research_doc; emits `research_doc.published`
- Produces: `app.create_contact_point(p_org_id uuid, p_workspace_id uuid, p_entity_id uuid, p_account_id uuid, p_type text, p_value_encrypted text, p_status contact_verification_status, p_verified_at timestamptz, p_provider text) RETURNS uuid` — SECURITY DEFINER; checks for existing unexpired valid point before inserting; emits `contact_point.verified` when status is 'valid'
- Produces: kill-switch seeds for 'score.compute', 'research.generate', 'enrichment.run', 'account.stage_advance', 'account.card.generate', 'health.check' in `job_type_config`

- [ ] **Step 1: Write the migration**

New tables (not already in migration 0009):
```sql
-- research_doc: owns the generated document and its source links
create table public.research_doc ( ... );  -- see Interfaces above; RLS: Viewer SELECT

-- research_job: tracks the in-flight generation job
create table public.research_job ( ... );  -- RLS: Viewer SELECT

-- contact_point: verified reach for a person entity; always tenant-scoped
create table public.contact_point ( ... );  -- RLS: Viewer SELECT

-- enrichment_job: tracks the in-flight enrichment job
create table public.enrichment_job ( ... );  -- RLS: Viewer SELECT

-- segment: saved filter query over accounts
create table public.segment ( ... );  -- RLS: Viewer SELECT; Editor INSERT/DELETE

-- data_completeness: pipeline gap record (M17)
create table public.data_completeness ( ... );  -- RLS: Admin SELECT
```

For every new table: `enable row level security`, write RLS policies, `grant select to authenticated`.

For `segment`: Editor role may also INSERT/DELETE (it's not derived). Add a `create policy segment_write on public.segment for insert to authenticated with check (app.has_workspace_role(workspace_id, 'Editor'))` and matching delete policy.

Derivation write-path RPCs (score, label, account_stage, account_card) all follow the pattern from `app.upsert_verdict`:
```sql
-- Inside each RPC:
perform set_config('app.writer', 'derivation', true);
-- ... write to the guarded table ...
-- For score/label/account_stage: also emit the matching outbox event
```

For `app.upsert_score`:
- Validates account and brief_version belong to same org and workspace
- Enforces gate: if `p_band` is 'High' or 'Medium', verify entity's latest verdict is not 'Unverified' — if it is, downgrade band to 'Low' and add a gate entry `{"reason":"verdict_unverified"}` to p_gates
- ON CONFLICT (account_id, brief_version_id) DO UPDATE; emit `score.band_changed` only when `excluded.band is distinct from score.band`

For `app.upsert_account_stage`:
- Idempotency: `ON CONFLICT (account_id, activity_id) DO NOTHING RETURNING id` — if no row returned, fetch existing row's id
- Must handle NULL activity_id gracefully (manual stage advances); for NULL use `ON CONFLICT (account_id) WHERE activity_id IS NULL`... actually use a unique index `unique (account_id, coalesce(activity_id, '00000000-0000-0000-0000-000000000000'::uuid))` and idempotent upsert

REVOKE/GRANT pattern for every function: `REVOKE EXECUTE ON FUNCTION app.X(...) FROM public, anon, authenticated; GRANT EXECUTE ON FUNCTION app.X(...) TO postgres, service_role;`

- [ ] **Step 2: Run `pnpm db:check` or `supabase db diff` to verify no drift**

```
cd C:\Users\Ashish Mishra\OneDrive\Desktop\Signal Intel
pnpm db:check
```
Expected: 0 errors (or note known CI-only pgTAP gap if CLI unavailable locally)

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20261110000022_phase2_accounts.sql
git commit -m "feat(db): Phase 2 tables + derivation RPCs + kill-switch seeds (migration 0022)

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 2: M8 scoring engine — `m08_scoring.py`

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m08_scoring.py`
- Create: `packages/signal_core/tests/test_m08_scoring.py`

**Interfaces:**
- Consumes from T1: `app.upsert_score(...)`, `app.upsert_label(...)` RPCs
- Consumes from Phase 1e: `verdict` table (latest active verdict for entity)
- Produces: `compute_score(pool, entity_id: UUID, workspace_id: UUID, ctx: CallContext) -> str | None` — returns band string or None when no brief_version

**Score computation logic (5 dimensions, each 0–100):**

| Dimension | Derivation |
|---|---|
| `fit` | Weighted average of Fit claims (vertical, size, geography, commercial_role predicates); default 50 when no such claims |
| `evidence` | Based on verdict value: Confirmed=90, Likely=65, Unverified=10, Contradicted=0; +2 per additional claim, capped at 100 |
| `scale` | From quantity predicates (sites, seats, units): 0 when none; log-normalised against workspace percentiles |
| `timing` | Count of recent signal claims (within 90 days) × 10, capped at 100 |
| `reachability` | count(valid contact_points for account's personas) / count(required personas) × 100 |

Gates (applied before band assignment): `verdict = 'Contradicted'` → Excluded; in an exclusion list → Excluded; `verdict = 'Unverified'` → band capped at Low. These are enforced again in SQL inside `app.upsert_score` as the authoritative gate.

`priority = fit × w_fit + evidence × w_evidence + scale × w_scale + timing × w_timing` using weights from brief_version.

Band thresholds from brief_version (default: High ≥ 70, Medium ≥ 40, Low ≥ 0).

Labels derived here and upserted: `radar:<source>`, `vertical:<value>`, `geography:<country>`, `persona_coverage:<persona>:<status>`.

- [ ] **Step 1: Write failing tests**

```python
# test_m08_scoring.py

async def test_compute_score_no_brief_version_returns_none(): ...
async def test_compute_score_unverified_verdict_caps_at_low(): ...
async def test_compute_score_contradicted_verdict_sets_excluded(): ...
async def test_compute_score_valid_dimensions_returns_band(): ...
async def test_compute_score_calls_upsert_score_and_upsert_label(): ...
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/signal_core && uv run pytest tests/test_m08_scoring.py -v
```

- [ ] **Step 3: Implement `compute_score(pool, entity_id, workspace_id, ctx)` in `m08_scoring.py`**

Pattern is similar to `m05_verdict.py`: open one `pool.connection()` context, two cursor executes (fetch brief_version + weights; fetch claims + verdict), compute dimensions, call `upsert_score` and batch-call `upsert_label` via the derivation RPCs.

- [ ] **Step 4: Run tests to verify they pass**

```
cd packages/signal_core && uv run pytest tests/test_m08_scoring.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m08_scoring.py packages/signal_core/tests/test_m08_scoring.py
git commit -m "feat(py): M8 scoring engine — 5 dimensions, gates, labels

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 3: M6 research engine — `m06_research.py` + prompt

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/research/v1.md`
- Create: `packages/signal_core/src/signal_core/modules/m06_research.py`
- Create: `packages/signal_core/tests/test_m06_research.py`

**Interfaces:**
- Consumes from T1: `app.create_research_doc(...)` RPC; `app.create_claim(...)` from Phase 1d for write-back
- Produces: `generate_research_doc(pool, gateway, account_id: UUID, workspace_id: UUID, ctx: CallContext) -> str | None` — returns research_doc id string or None when already up to date

**Prompt template** (`research/v1.md`):
```
You are researching {{entity_name}} for a sales team.

Brief hypothesis: {{hypothesis}}
Research template sections: {{sections_json}}

Source material:
{{sources}}

For each section, write 2–5 sentences backed by the sources above.
Every factual sentence must end with [SOURCE:{{source_record_id}}].
Do not invent facts not found in the sources.
```
Curly braces in JSON examples must be escaped as `{{` and `}}`.

**Write-back rule**: after generating, for each factual sentence parse the `[SOURCE:id]` citation. For each sentence, attempt `app.create_claim()` with the entity, the sentence as quote, the source_record_id, and an appropriate predicate (use a fixed 'research.finding' predicate key). Skip write-back when `app.create_claim()` raises (the claim may already exist with higher confidence).

**Reuse rule**: before generating, query `research_doc` for a row with the same `(account_id, brief_version_id)` and `published_at` more recent than the latest claim for the entity. If one exists, return early.

- [ ] **Step 1: Write the prompt template** at `packages/signal_core/src/signal_core/ai_gateway/prompts/research/v1.md`
- [ ] **Step 2: Write failing tests** (mock pool + gateway; test early-return on existing doc; test AI call; test write-back skips on exception)
- [ ] **Step 3: Run tests to verify they fail**

```
cd packages/signal_core && uv run pytest tests/test_m06_research.py -v
```

- [ ] **Step 4: Implement `generate_research_doc(pool, gateway, account_id, workspace_id, ctx)`**
- [ ] **Step 5: Run tests to verify they pass**

```
cd packages/signal_core && uv run pytest tests/test_m06_research.py -v
```

Expected: 4+ PASSED

- [ ] **Step 6: Commit**

```bash
git add packages/signal_core/src/signal_core/ai_gateway/prompts/research/ packages/signal_core/src/signal_core/modules/m06_research.py packages/signal_core/tests/test_m06_research.py
git commit -m "feat(py): M6 research engine — AI doc generation with source citations

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 4: M7 enrichment — `m07_enrichment.py`

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m07_enrichment.py`
- Create: `packages/signal_core/tests/test_m07_enrichment.py`

**Interfaces:**
- Consumes from T1: `app.create_contact_point(...)` RPC
- Consumes: `app.reserve_budget()` from Phase 1e metering
- Produces: `enrich_account(pool, gateway, account_id: UUID, workspace_id: UUID, ctx: CallContext) -> int` — returns count of new contact_points created

**Provider waterfall:**
```python
async def _try_provider(provider_name: str, person_entity_id: UUID, person_name: str, company_domain: str) -> ContactPointResult | None
```
Providers are configured as an ordered list in workspace settings. The module tries them in order and stops at the first `status='valid'` result. Never calls a provider for a person who already has a valid, recently-verified contact_point (within 30 days). Budget reserved via `app.reserve_budget()` before each paid call.

**Suppression check:** query `contact_point` for `status = 'invalid'` with the same `(entity_id, type)` — skip those forever. For now implement one stub provider `StubProvider` that returns a fixed test email for unit tests.

- [ ] **Step 1: Write failing tests** (test never-pay-twice; test suppression skip; test waterfall stops at first valid; test budget reserve called before provider)
- [ ] **Step 2: Run tests to verify they fail**

```
cd packages/signal_core && uv run pytest tests/test_m07_enrichment.py -v
```

- [ ] **Step 3: Implement `enrich_account(pool, gateway, account_id, workspace_id, ctx)` with `StubProvider`**
- [ ] **Step 4: Run tests to verify they pass**

```
cd packages/signal_core && uv run pytest tests/test_m07_enrichment.py -v
```

Expected: 4+ PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m07_enrichment.py packages/signal_core/tests/test_m07_enrichment.py
git commit -m "feat(py): M7 enrichment — provider waterfall + verification gate + suppression

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 5: M11 stage machine — `m11_stage.py`

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m11_stage.py`
- Modify: `packages/signal_core/src/signal_core/modules/m04_identity.py` (defer `account.stage_advance` after account create)
- Create: `packages/signal_core/tests/test_m11_stage.py`

**Interfaces:**
- Consumes from T1: `app.upsert_account_stage(p_account_id, p_to_stage, p_activity_id, p_occurred_at) RETURNS uuid`
- Produces: `advance_stage(pool, account_id: UUID, activity_id: UUID, activity_kind: str, workspace_id: UUID, ctx: CallContext) -> str | None` — returns new stage string or None when stage unchanged

**Stage transition table** (implemented as a dict in Python):
```python
TRANSITIONS: dict[str, dict[str, str]] = {
    # activity_kind → {from_stage → to_stage}
    'message_sent':      {'Untouched': 'Contacted', 'Queued': 'Contacted'},
    'reply_received':    {'Contacted': 'Engaged'},
    'inbound_contact':   {'Untouched': 'Engaged', 'Contacted': 'Engaged'},
    'meeting_held':      {'Contacted': 'Meeting', 'Engaged': 'Meeting', 'Qualified': 'Meeting'},
    'marked_qualified':  {'Contacted': 'Qualified', 'Engaged': 'Qualified'},
    'marked_disqualified': {'*': 'Disqualified'},  # any stage → Disqualified
    'deal_created':      {'Meeting': 'Handed off'},
    # Notes, out-of-office, task_completed: no transition
}
```

Dormant transition is a cron check (not activity-driven): any account in Contacted/Engaged/Qualified with no genuine reply for 45 days. Implement as a separate function `mark_dormant_accounts(pool, workspace_id, ctx)` called by the health cron.

m04 modification: after `app.create_account()` succeeds, defer a Procrastinate job `account.stage_advance` with `{"account_id": str(id), "activity_id": None, "activity_kind": "account_created", "workspace_id": str(workspace_id)}` — this causes the stage engine to set stage to 'Untouched' (initial state; handled as no-op since default is already Untouched, but it records the event).

- [ ] **Step 1: Write failing tests** (test each transition; test idempotent double-call; test mark_dormant only for right ages)
- [ ] **Step 2: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m11_stage.py -v
```

- [ ] **Step 3: Implement `advance_stage` and `mark_dormant_accounts` in `m11_stage.py`; modify `m04_identity.py`**
- [ ] **Step 4: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m11_stage.py -v
```

Expected: 5+ PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m11_stage.py packages/signal_core/src/signal_core/modules/m04_identity.py packages/signal_core/tests/test_m11_stage.py
git commit -m "feat(py): M11 stage machine — activity-driven transitions + dormant detection

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 6: M15 account card — `m15_account_card.py` + prompt

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/account_card/v1.md`
- Create: `packages/signal_core/src/signal_core/modules/m15_account_card.py`
- Modify: `packages/signal_core/src/signal_core/modules/m05_verdict.py` (defer `account.card.generate` after verdict write)
- Create: `packages/signal_core/tests/test_m15_account_card.py`

**Interfaces:**
- Consumes from T1: `app.upsert_account_card(p_account_id, p_brief_version_id, p_content jsonb, p_inputs jsonb) RETURNS uuid`
- Produces: `generate_account_card(pool, gateway, account_id: UUID, workspace_id: UUID, ctx: CallContext) -> str | None`

**Prompt template** (`account_card/v1.md`):
```
Generate a brief account card for {{entity_name}} ({{account_stage}} stage).

Brief hypothesis: {{hypothesis}}
Verdict: {{verdict_value}} ({{verdict_quotes}})
Score: {{band}} band — Fit {{fit}}, Evidence {{evidence}}, Scale {{scale}}, Timing {{timing}}
Key claims:
{{claims_summary}}
Contact coverage: {{contact_coverage}}

Write 5–8 bullet points covering: who they are, the evidence, key people, history so far,
open questions, and the next step. Every bullet must cite [CLAIM:{{id}}] or [ACTIVITY:{{id}}].
```
Escape all literal `{{` / `}}` in JSON within the prompt body.

m05_verdict modification: after a successful `upsert_verdict`, defer `account.card.generate` job with `{"account_id": str(account_id), "workspace_id": str(workspace_id), "org_id": str(org_id)}`.

- [ ] **Step 1: Write the prompt template**
- [ ] **Step 2: Write failing tests** (mock pool + gateway; test early-return when card already current; test card format; test citation presence)
- [ ] **Step 3: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m15_account_card.py -v
```

- [ ] **Step 4: Implement `generate_account_card` and modify `m05_verdict.py`**
- [ ] **Step 5: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m15_account_card.py -v
```

Expected: 4+ PASSED

- [ ] **Step 6: Commit**

```bash
git add packages/signal_core/src/signal_core/ai_gateway/prompts/account_card/ packages/signal_core/src/signal_core/modules/m15_account_card.py packages/signal_core/src/signal_core/modules/m05_verdict.py packages/signal_core/tests/test_m15_account_card.py
git commit -m "feat(py): M15 account card generation + defer from verdict write

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 7: M17 full health monitoring — `m17_health.py`

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m17_health.py`
- Create: `packages/signal_core/tests/test_m17_health.py`

**Interfaces:**
- Consumes from T1: `public.data_completeness` table (direct INSERT via service_role — health writes are not derived, they are operational logs)
- Consumes: `app.record_health(component, status, detail, owner)` from migration 0010
- Produces: `run_health_checks(pool, ctx: CallContext) -> dict[str, str]` — returns `{component: status}` map

**What to monitor** (per M17 spec):

| Component | Silence rule | Checks |
|---|---|---|
| `source.{connector}` | Volume below expected window | SELECT COUNT from source_record WHERE created_at > now() - expected_window; compare to job_type_config.expected_window |
| `queue.depth` | Oldest job in Procrastinate > 2h | SELECT MIN(planned_at) from procrastinate_jobs WHERE status = 'todo' |
| `derivation.lag` | Latest score computed_at < latest claim created_at − 1h | Compare max claim created_at vs min score computed_at per workspace |
| `enrichment.caps` | Burn against budget | Query workspace_spend vs budget (existing S6 tables) |
| `review_queue.age` | Oldest pending review_item > 48h | SELECT MIN(created_at) from review_item WHERE status = 'pending' |

Write a `data_completeness` row for each pipeline per check cycle (use direct `INSERT INTO public.data_completeness` — it is NOT a derived table, no guard trigger).

Call `app.record_health(component, 'ok'/'incident'/'silent', detail_jsonb, owner='M17')` to open/close incidents.

- [ ] **Step 1: Write failing tests** (mock pool; test each check raises incident on threshold breach; test ok resets incident; test data_completeness row written)
- [ ] **Step 2: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m17_health.py -v
```

- [ ] **Step 3: Implement `run_health_checks(pool, ctx)`**
- [ ] **Step 4: Run tests**

```
cd packages/signal_core && uv run pytest tests/test_m17_health.py -v
```

Expected: 5+ PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m17_health.py packages/signal_core/tests/test_m17_health.py
git commit -m "feat(py): M17 health monitoring — silence detection + queue depth + lag checks

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 8: Worker wiring — new jobs + health cron

**Files:**
- Modify: `services/worker/src/worker/app.py`

**Interfaces:**
- Consumes from T1 kill-switch seeds: 'score.compute', 'research.generate', 'enrichment.run', 'account.stage_advance', 'account.card.generate', 'health.check'
- Consumes from T2: `m08_scoring.compute_score(pool, gateway, entity_id, workspace_id, ctx)`
- Consumes from T3: `m06_research.generate_research_doc(pool, gateway, account_id, workspace_id, ctx)`
- Consumes from T4: `m07_enrichment.enrich_account(pool, gateway, account_id, workspace_id, ctx)`
- Consumes from T5: `m11_stage.advance_stage(pool, account_id, activity_id, activity_kind, workspace_id, ctx)`
- Consumes from T6: `m15_account_card.generate_account_card(pool, gateway, account_id, workspace_id, ctx)`
- Consumes from T7: `m17_health.run_health_checks(pool, ctx)`
- Produces: 6 new `@job` decorated async functions in `app.py`, each following the pattern from `verdict_compute_task`; 1 cron registration for `health.check` running every 30 minutes

```python
@job(app, type="score.compute",          kill_switch=kill_switch, queue="derive",  retry=2)
async def score_compute_task(*, entity_id: str, workspace_id: str, org_id: str, approval_id: str | None = None, **_): ...

@job(app, type="research.generate",      kill_switch=kill_switch, queue="compute", retry=1)
async def research_generate_task(*, account_id: str, workspace_id: str, org_id: str, approval_id: str | None = None, **_): ...

@job(app, type="enrichment.run",         kill_switch=kill_switch, queue="compute", retry=1)
async def enrichment_run_task(*, account_id: str, workspace_id: str, org_id: str, approval_id: str | None = None, **_): ...

@job(app, type="account.stage_advance",  kill_switch=kill_switch, queue="derive",  retry=3)
async def account_stage_advance_task(*, account_id: str, workspace_id: str, org_id: str, activity_id: str | None, activity_kind: str, approval_id: str | None = None, **_): ...

@job(app, type="account.card.generate",  kill_switch=kill_switch, queue="derive",  retry=2)
async def account_card_generate_task(*, account_id: str, workspace_id: str, org_id: str, approval_id: str | None = None, **_): ...

@cron(app, cron="*/30 * * * *", queue="health", type="health.check")
async def health_check_task(**_): ...
```

Each task builds a `CallContext` with `job_args` including `approval_id` when present (same pattern as `verdict_compute_task`). The "derive" queue is already in the worker's queue list from Phase 1e; add "compute" and "health" queues.

The `m05_verdict.py` and `m04_identity.py` modifications from Tasks 5 and 6 defer jobs via `await verdict_compute_job.defer_async(...)` — the new job handles must be assigned in `build_app()` the same way.

- [ ] **Step 1: Verify test suite baseline (all Phase 1e tests still pass)**

```
cd services/worker && uv run pytest -x -q
```

- [ ] **Step 2: Add the 6 new tasks and cron, update queue list in `run_worker_async`**

Add "compute" and "health" to the `queues=[...]` list in `app.run_worker_async(...)`.

- [ ] **Step 3: Run all tests**

```
cd services/worker && uv run pytest -x -q
```

Expected: all previous tests PASS + new smoke tests PASS

- [ ] **Step 4: Commit**

```bash
git add services/worker/src/worker/app.py
git commit -m "feat(worker): wire Phase 2 jobs — scoring, research, enrichment, stage, card, health cron

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 9: Web — accounts table page

**Files:**
- Create: `apps/web/src/app/(app)/workspace/[wsid]/accounts/page.tsx`

**Interfaces:**
- Consumes: `public.account` (with joins to `score`, `verdict`, `entity`) via Supabase client with user JWT
- Produces: RSC page at `/workspace/[wsid]/accounts` — table with columns: entity name, band chip, verdict badge, fit score, evidence score, stage badge, contact coverage

**Key implementation notes:**
- Use the existing Supabase server client (`createServerClient`) from the app's existing pattern
- Table query: `supabase.from('account').select('id, entity:entity(id, display_name), score(*), verdict:verdict(value), stage').eq('workspace_id', wsid).order('score.priority', { ascending: false })`
- PostgREST FK join: `score` and `verdict` return single objects (many-to-one FK), not arrays — access as `row.score?.band` not `row.score?.[0]?.band`
- Use `BandChip`, `VerdictBadge`, `StageBadge` domain components from `@signal/ui`
- Filter bar: band multi-select, verdict multi-select, stage multi-select (client-side filter over fetched page)
- Empty state: `EmptyState` component with "No accounts found" and a reason when filters are active

- [ ] **Step 1: Implement the page, run `pnpm typecheck`**

```
cd apps/web && pnpm typecheck
```

Expected: 0 TS errors (use `@ts-expect-error` with a comment if Supabase types are pending codegen)

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/app/(app)/workspace/[wsid]/accounts/page.tsx
git commit -m "feat(web): accounts table page with score/band/verdict/stage

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 10: Web — account page

**Files:**
- Create: `apps/web/src/app/(app)/workspace/[wsid]/accounts/[aid]/page.tsx`

**Interfaces:**
- Consumes: account + all related tables (entity, verdict, score, label, account_card, research_doc, contact_point, activity, review_item) via Supabase client
- Produces: RSC page at `/workspace/[wsid]/accounts/[aid]`

**Sections to render** (per M11 spec):

1. **Header** — entity display_name, aliases, `EntityHeader` component
2. **Verdict** — `VerdictBadge` + quotes list; "Explain" link to verdict panel
3. **Score** — `ScoreBreakdown` (5 dimensions + gates + band)
4. **Account card** — `account_card.content` rendered as a cited bullet list
5. **Labels** — chip list by kind
6. **People** — contacts for person entities with `works_at` relationships; contact_point coverage per persona
7. **Activity timeline** — `activity` rows ordered by occurred_at desc
8. **Research documents** — list of research_doc rows with published_at and version
9. **Stage** — `StageBadge` + history from `account_stage_event`
10. **Tasks** — not yet wired (M12 is Phase 3); render empty state

**Key constraints:**
- No field has an edit control if it is derived (verdict, score, band, labels, card — use `DerivedValue` wrapper)
- The page loads each section independently (`Suspense` boundaries per section)
- Activity timeline requires join with entity name for the activity subject; use `activity.select('*, entity:entity(display_name)')` PostgREST join

- [ ] **Step 1: Implement the page, run `pnpm typecheck`**

```
cd apps/web && pnpm typecheck
```

Expected: 0 TS errors

- [ ] **Step 2: Commit**

```bash
git add apps/web/src/app/(app)/workspace/[wsid]/accounts/[aid]/page.tsx
git commit -m "feat(web): account page with all Phase 2 sections

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

### Task 11: pgTAP suites for Phase 2

**Files:**
- Create: `supabase/tests/database/11_scoring.test.sql`
- Create: `supabase/tests/database/12_research_enrichment.test.sql`
- Create: `supabase/tests/database/13_stage_machine.test.sql`
- Create: `supabase/tests/database/14_account_card.test.sql`

**Interfaces:**
- Consumes from T1: all Phase 2 RPCs and new tables

**Suite 11 — scoring (plan: 10 assertions):**
1. `app.upsert_score` exists
2. `app.upsert_label` exists
3. Direct INSERT into `public.score` blocked (42501)
4. Direct INSERT into `public.label` blocked (42501)
5. `upsert_score` with valid inputs returns UUID
6. `upsert_score` ON CONFLICT DO UPDATE returns same UUID
7. `upsert_score` with Unverified verdict cannot set band High (gate SQL enforcement)
8. `upsert_label` with valid inputs returns UUID
9. `score_band_changed` outbox event emitted when band changes
10. No `score_band_changed` event when band is unchanged on update

**Suite 12 — research + enrichment (plan: 8 assertions):**
1. `app.create_research_doc` exists
2. `app.create_contact_point` exists
3. `create_research_doc` returns UUID and emits `research_doc.published`
4. `create_contact_point` with status='valid' emits `contact_point.verified`
5. `create_contact_point` with status='unknown' does NOT emit `contact_point.verified`
6. Second `create_contact_point` for same (entity_id, type, value_encrypted) returns existing id
7. `contact_point.scope` is always 'tenant'
8. Direct INSERT into `public.research_doc` blocked (not derived — INSERT should succeed but only via RPC per S8; verify RLS blocks authenticated direct insert)

**Suite 13 — stage machine (plan: 9 assertions):**
1. `app.upsert_account_stage` exists
2. Direct INSERT into `public.account_stage_event` blocked (42501)
3. `upsert_account_stage` inserts event and sets account.stage
4. `upsert_account_stage` emits `account.stage_changed`
5. Second call with same (account_id, activity_id) returns same UUID without duplicate event
6. `account.stage` cannot be set directly (guard from migration 0009)
7. Advance Untouched → Queued works
8. Advance Queued → Contacted works
9. Exit stage Disqualified: further transitions are blocked

**Suite 14 — account card (plan: 6 assertions):**
1. `app.upsert_account_card` exists
2. Direct INSERT into `public.account_card` blocked (42501)
3. `upsert_account_card` with valid inputs returns UUID
4. `upsert_account_card` ON CONFLICT DO UPDATE returns same UUID
5. Viewer can SELECT from `public.account_card`
6. `account_card.content` is jsonb (not text)

- [ ] **Step 1: Write all four test files**
- [ ] **Step 2: Verify syntax (CI-only run; note in commit that Supabase CLI not available locally)**
- [ ] **Step 3: Commit**

```bash
git add supabase/tests/database/11_scoring.test.sql supabase/tests/database/12_research_enrichment.test.sql supabase/tests/database/13_stage_machine.test.sql supabase/tests/database/14_account_card.test.sql
git commit -m "test(db): pgTAP suites 11-14 — scoring, research/enrichment, stage, account_card

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```
