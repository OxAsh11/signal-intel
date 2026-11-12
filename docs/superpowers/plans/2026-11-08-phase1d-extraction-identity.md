# Phase 1d: Extraction & Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build M3 (relevance judge + structured extraction + claim capture) and M4 (alias-priority identity resolution + review queue + duplicate sweep) so every fetched source_record gets extracted and every mention gets resolved to an entity.

**Architecture:** A two-stage extraction pipeline (prejudge for relevance, then structured extract for mentions+claims) writes facts through SECURITY DEFINER RPCs. Identity resolution runs alias-priority matching (registry_id > domain > profile_url > name_country), falls back to pg_trgm fuzzy, then AI tie-break; mentions scoring 40–89 land in a `review_item` queue for human merge/skip/create-new. A duplicate-sweep cron fires daily.

**Tech Stack:** Python 3.13 / Procrastinate / psycopg3 / AI gateway (PREJUDGE + EXTRACT capabilities) / Next.js 16 App Router / Supabase Postgres with RLS / pgTAP

**Spec:** docs/superpowers/specs/2026-11-06-platform-approach-design.md §M3, §M4

## Global Constraints

- S8 write discipline: every INSERT/UPDATE goes through `app.*` SECURITY DEFINER RPCs; `authenticated` gets SELECT only on public tables; never direct-INSERT in Python or TypeScript
- psycopg3 cursor pattern: `async with conn.cursor() as cur: await cur.execute(...); row = await cur.fetchone()`
- Kill-switch every Procrastinate job: `@kill_switched("category.name")` before `@app.task`
- AI gateway: `gateway.run(Capability.PREJUDGE, prompt, vars, OutputModel, ctx)` for relevance; `gateway.run(Capability.EXTRACT, ...)` for structured extraction; modules never call AI providers directly
- All migrations must `REVOKE EXECUTE ... FROM public, anon, authenticated` before granting; include REVOKE before every GRANT block
- Commit after every task with descriptive message; run `pytest` and `tsc` after each task before committing
- Working directory: `packages/signal_core/src/signal_core/` for Python; `apps/web/src/app/` for TypeScript; `supabase/migrations/` for SQL; `supabase/tests/database/` for pgTAP

## Review Focus

- Relevance judge runs even for source_records already marked `extracted` or `irrelevant` — `extraction_status` check must gate the job
- Identity resolution creates a new entity when confidence < 40 even if a near-match exists — confirm the threshold comparison is `<` not `<=`
- `review_item` rows must be scoped to workspace: a reviewer in workspace A must never see workspace B's queue entries
- `app.merge_entities` must update `mention.entity_id` for all mentions pointing to the loser entity, not just set `entity.merged_into`
- Duplicate sweep processes unresolved mentions; it must skip mentions whose source_record belongs to a workspace this org has since deleted

---

### Task 1: DB migration 0018 — review_item table + extraction/identity write-path RPCs + kill-switch seeds

**Files:**
- Create: `supabase/migrations/20261108000018_extraction_identity.sql`
- Test: `supabase/tests/database/09_extraction_identity.test.sql` (skeleton only — full tests in Task 6)

**Interfaces:**
- Produces:
  - `public.review_item` table (see schema below)
  - `app.update_extraction_status(p_id uuid, p_status public.extraction_status, p_detail text default null) → void`
  - `app.create_mention(p_org uuid, p_source_record uuid, p_entity_type public.entity_type, p_raw_name text, p_raw_domain text, p_raw_country text, p_raw_attributes jsonb) → uuid`
  - `app.create_claim(p_org uuid, p_entity uuid, p_predicate uuid, p_value jsonb, p_quote text, p_quote_original text, p_quote_language text, p_source_record uuid, p_observed_at timestamptz, p_extractor_version uuid, p_confidence smallint) → uuid`
  - `app.create_entity(p_org uuid, p_entity_type public.entity_type, p_display_name text, p_scope public.data_scope default 'tenant') → uuid`
  - `app.create_entity_alias(p_org uuid, p_entity uuid, p_alias_type public.alias_type, p_value_normalised text, p_confidence smallint, p_source_record uuid) → uuid`
  - `app.resolve_mention(p_mention uuid, p_entity uuid, p_match_method text, p_match_confidence smallint) → void`
  - `app.create_account(p_org uuid, p_workspace uuid, p_entity uuid) → uuid`
  - `app.merge_entities(p_winner uuid, p_loser uuid, p_actor uuid) → void`
  - `app.create_review_item(p_org uuid, p_workspace uuid, p_mention uuid, p_candidate_entity uuid, p_match_method text, p_match_confidence smallint) → uuid`
  - `app.resolve_review_item(p_id uuid, p_action text, p_new_entity_name text default null) → void` — action: 'merge' | 'skip' | 'create_new'
  - Kill-switch seeds: `extract.relevance_judge`, `extract.structured`, `identity.resolve`, `identity.duplicate_sweep`

**review_item schema:**
```sql
create table public.review_item (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  mention_id       uuid not null references public.mention (id),
  candidate_entity uuid references public.entity (id),
  match_method     text,
  match_confidence smallint check (match_confidence between 0 and 100),
  status           text not null default 'pending' check (status in ('pending', 'merged', 'skipped', 'created_new')),
  resolved_by      uuid references public.member (id),
  resolved_at      timestamptz,
  created_at       timestamptz not null default now()
);
create index review_item_ws_status_idx on public.review_item (workspace_id, status, created_at)
  where status = 'pending';
```

- [ ] **Step 1: Write the migration file**

Create `supabase/migrations/20261108000018_extraction_identity.sql` with:
1. `public.review_item` table (schema above), RLS enabled, policy `review_item_read` for `Viewer` using `has_workspace_role`, `review_item_write` for `Editor` using `has_workspace_role`; grant SELECT, INSERT to authenticated (INSERT only for the two RPCs below, not direct)
2. `app.update_extraction_status`: UPDATE `public.source_record` SET extraction_status = p_status, status_detail = p_detail, updated_at = now() WHERE id = p_id; SECURITY DEFINER; grant to service_role only (workers call this)
3. `app.create_mention`: INSERT into public.mention, RETURNING id; SECURITY DEFINER; grant to service_role
4. `app.create_claim`: INSERT into public.claim, RETURNING id; SECURITY DEFINER; grant to service_role
5. `app.create_entity`: INSERT into public.entity (org_id, entity_type, display_name, scope), RETURNING id; also INSERT into public.audit_log (action='entity.created', ...); SECURITY DEFINER; grant to service_role
6. `app.create_entity_alias`: INSERT into public.entity_alias ON CONFLICT (org_id, alias_type, value_normalised, entity_id) DO UPDATE SET confidence = greatest(entity_alias.confidence, excluded.confidence), RETURNING id; SECURITY DEFINER; grant to service_role
7. `app.resolve_mention`: UPDATE public.mention SET entity_id = p_entity, match_method = p_match_method, match_confidence = p_match_confidence WHERE id = p_mention; SECURITY DEFINER; grant to service_role
8. `app.create_account`: INSERT into public.account (org_id, workspace_id, entity_id) ON CONFLICT (workspace_id, entity_id) DO NOTHING RETURNING id; SECURITY DEFINER; grant to service_role
9. `app.merge_entities(p_winner, p_loser, p_actor)`: UPDATE public.mention SET entity_id = p_winner WHERE entity_id = p_loser; UPDATE public.entity_alias SET entity_id = p_winner WHERE entity_id = p_loser; UPDATE public.entity SET status = 'merged', merged_into = p_winner WHERE id = p_loser; INSERT into audit_log; SECURITY DEFINER; grant to service_role
10. `app.create_review_item`: INSERT into public.review_item; SECURITY DEFINER; grant to service_role
11. `app.resolve_review_item`: check status = 'pending'; set resolved_by = auth.uid(), resolved_at = now(), status = p_action cast; if 'merge' call app.resolve_mention and app.create_account; if 'create_new' call app.create_entity then app.resolve_mention and app.create_account; SECURITY DEFINER; grant to authenticated (Editor check: `IF NOT app.has_workspace_role((SELECT workspace_id FROM public.review_item WHERE id = p_id), 'Editor') THEN RAISE EXCEPTION using errcode = '42501'; END IF`)
12. Kill-switch seeds: INSERT into public.job_type_config (job_type, enabled) VALUES ('extract.relevance_judge', true), ('extract.structured', true), ('identity.resolve', true), ('identity.duplicate_sweep', true) ON CONFLICT DO NOTHING
13. REVOKE EXECUTE blocks before every GRANT block (pattern from migration 0017)

- [ ] **Step 2: Run SQL lint check**

```bash
grep -n "grant execute" supabase/migrations/20261108000018_extraction_identity.sql
grep -n "revoke execute" supabase/migrations/20261108000018_extraction_identity.sql
```
Expected: each `grant execute` block has a preceding `revoke execute` block.

- [ ] **Step 3: Create pgTAP skeleton**

Create `supabase/tests/database/09_extraction_identity.test.sql` with:
```sql
begin;
select plan(1);
select pass('skeleton');
select * from finish();
rollback;
```

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/20261108000018_extraction_identity.sql supabase/tests/database/09_extraction_identity.test.sql
git commit -m "feat(db): migration 0018 — review_item, extraction/identity RPCs, kill-switch seeds"
```

---

### Task 2: M3 extraction pipeline — relevance judge + structured extraction

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/relevance_judge/v1.md`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/extraction/v1.md`
- Modify: `services/worker/src/worker/app.py` (add `extract_relevance_judge_job`, `extract_structured_job`)
- Modify: `packages/signal_core/src/signal_core/ai_gateway/prompts/__init__.py` (register new prompts)
- Test: `packages/signal_core/tests/test_m03_extraction.py`

**Interfaces:**
- Consumes from Task 1: `app.create_mention`, `app.create_claim`, `app.update_extraction_status`
- Consumes from Task 1: kill-switch names `extract.relevance_judge`, `extract.structured`
- Consumes from existing: `AiGateway.run()`, `Capability.PREJUDGE` (relevance), `Capability.EXTRACT` (structured), `kill_switched` decorator from `signal_core.gate`, `CallContext` from `signal_core.metering`
- Produces:
  - `async def judge_relevance(pool, gateway, source_record_id, ctx) -> bool` in m03_extraction
  - `async def extract_structured(pool, gateway, source_record_id, ctx) -> ExtractionResult` in m03_extraction
  - Worker jobs: `extract_relevance_judge_job(source_record_id: str)`, `extract_structured_job(source_record_id: str)`

**Output models (define in m03_extraction.py):**
```python
class RelevanceJudgement(BaseModel):
    relevant: bool
    reason: str

class MentionExtract(BaseModel):
    entity_type: str  # 'company' | 'person'
    raw_name: str | None = None
    raw_domain: str | None = None
    raw_country: str | None = None
    raw_attributes: dict = {}

class ClaimExtract(BaseModel):
    mention_index: int  # index into the mentions list this claim references
    predicate_key: str
    value: str  # JSON string
    quote: str | None = None
    quote_original: str | None = None
    quote_language: str | None = None
    confidence: int  # 0-100

class ExtractionResult(BaseModel):
    mentions: list[MentionExtract]
    claims: list[ClaimExtract]
```

- [ ] **Step 1: Write failing tests**

```python
# test_m03_extraction.py
async def test_judge_relevance_relevant(fake_pool, fake_gateway, fake_source_record):
    result = await judge_relevance(fake_pool, fake_gateway, fake_source_record["id"], ctx)
    assert result is True

async def test_judge_relevance_irrelevant_updates_status(fake_pool, fake_gateway, fake_source_record):
    # fake_gateway returns relevant=False
    result = await judge_relevance(fake_pool, fake_gateway, fake_source_record["id"], ctx)
    assert result is False
    # verify update_extraction_status called with 'irrelevant'

async def test_extract_structured_creates_mentions_and_claims(fake_pool, fake_gateway, fake_source_record):
    result = await extract_structured(fake_pool, fake_gateway, fake_source_record["id"], ctx)
    assert len(result.mentions) > 0
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd packages/signal_core && python -m pytest tests/test_m03_extraction.py -v 2>&1 | head -20
```
Expected: ImportError or NameError

- [ ] **Step 3: Write relevance_judge prompt** at `packages/signal_core/src/signal_core/ai_gateway/prompts/relevance_judge/v1.md`

Prompt must have `name: relevance_judge`, `version: 1`, system section ("You are a relevance classifier..."), user template with `{{content}}` variable. Output schema: `RelevanceJudgement`.

- [ ] **Step 4: Write extraction prompt** at `packages/signal_core/src/signal_core/ai_gateway/prompts/extraction/v1.md`

Prompt must have `name: extraction`, `version: 1`, system section with predicate list injected via `{{predicates}}`, user template with `{{content}}` variable. Output schema: `ExtractionResult`.

- [ ] **Step 5: Implement m03_extraction.py**

Key logic:
- `judge_relevance`: load source_record payload (from `payload_ref` via storage or inline text), call `gateway.run(Capability.PREJUDGE, relevance_judge_prompt, {"content": text}, RelevanceJudgement, ctx)`, if not relevant call `app.update_extraction_status(id, 'irrelevant')` and return False
- `extract_structured`: load payload, fetch workspace predicate list (SELECT key, extraction_hint FROM public.predicate WHERE workspace_id = ...), call `gateway.run(Capability.EXTRACT, extraction_prompt, {"content": text, "predicates": json_predicates}, ExtractionResult, ctx)`, for each mention call `app.create_mention(...)`, lookup predicate UUID for each claim's predicate_key, call `app.create_claim(...)`, then call `app.update_extraction_status(id, 'extracted')`
- Error handling: on `AiOutputInvalid` or `AiProviderError`, call `app.update_extraction_status(id, 'failed', str(e))` and re-raise

- [ ] **Step 6: Add worker jobs to app.py**

```python
@kill_switched("extract.relevance_judge")
@app.task(queue="extract", name="extract.relevance_judge")
async def extract_relevance_judge_job(*, source_record_id: str) -> None:
    async with get_pool() as pool, get_gateway() as gw:
        ctx = CallContext(org_id=..., workspace_id=..., member_id=None)
        relevant = await judge_relevance(pool, gw, UUID(source_record_id), ctx)
        if relevant:
            await extract_structured_job.defer_async(source_record_id=source_record_id)

@kill_switched("extract.structured")
@app.task(queue="extract", name="extract.structured")
async def extract_structured_job(*, source_record_id: str) -> None:
    async with get_pool() as pool, get_gateway() as gw:
        ctx = CallContext(...)
        await extract_structured(pool, gw, UUID(source_record_id), ctx)
```

Add "extract" to the queue list in main.py worker startup.

- [ ] **Step 7: Run tests**

```bash
cd packages/signal_core && python -m pytest tests/test_m03_extraction.py -v
```
Expected: all pass

- [ ] **Step 8: Run full test suite**

```bash
cd packages/signal_core && python -m pytest -v 2>&1 | tail -5
```
Expected: no regressions

- [ ] **Step 9: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m03_extraction.py \
  packages/signal_core/src/signal_core/ai_gateway/prompts/ \
  packages/signal_core/tests/test_m03_extraction.py \
  services/worker/src/worker/app.py
git commit -m "feat(py): M3 extraction pipeline — relevance judge, structured extract, claims"
```

---

### Task 3: M4 identity resolution — alias matching + review queue + duplicate sweep

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m04_identity.py`
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/identity_tiebreak/v1.md`
- Modify: `services/worker/src/worker/app.py` (add `identity_resolve_job`, `identity_duplicate_sweep_job`)
- Test: `packages/signal_core/tests/test_m04_identity.py`

**Interfaces:**
- Consumes from Task 1: `app.create_entity`, `app.create_entity_alias`, `app.resolve_mention`, `app.create_account`, `app.create_review_item`, kill-switch names `identity.resolve`, `identity.duplicate_sweep`
- Consumes from Task 2: worker jobs pattern (kill_switched + app.task)
- Produces:
  - `async def resolve_mention(pool, gateway, mention_id, workspace_id, ctx) -> str` — returns 'attached'|'queued'|'created'
  - `async def duplicate_sweep(pool, gateway, org_id, ctx) -> int` — returns count of resolved mentions
  - Worker jobs: `identity_resolve_job(mention_id: str, workspace_id: str)`, `identity_duplicate_sweep_job(org_id: str)`

**Identity resolution algorithm:**
```
Priority order for alias lookup (highest confidence first):
1. registry_id  → alias_type = 'registry_id'
2. profile_url  → alias_type = 'profile_url'
3. domain       → alias_type = 'domain'
4. name_country → alias_type = 'name_country' (pg_trgm fuzzy, threshold 0.5)

For each alias in priority order:
  - Exact match (alias_type + value_normalised) → confidence = 95
  - name_country fuzzy (similarity > 0.5) → confidence = 70 + round(similarity * 20)
  - If multiple candidates → AI tie-break (Capability.JUDGE)

Routing:
  - confidence >= 90 → app.resolve_mention + app.create_account
  - 40 <= confidence < 90 → app.create_review_item
  - confidence < 40 → app.create_entity + app.create_entity_alias + app.resolve_mention + app.create_account
```

- [ ] **Step 1: Write failing tests**

```python
async def test_resolve_mention_exact_match_attaches(fake_pool_with_entity, fake_gateway, fake_mention):
    result = await resolve_mention(fake_pool_with_entity, fake_gateway, fake_mention["id"], ws_id, ctx)
    assert result == 'attached'

async def test_resolve_mention_low_confidence_creates_entity(fake_pool_empty, fake_gateway, fake_mention):
    result = await resolve_mention(fake_pool_empty, fake_gateway, fake_mention["id"], ws_id, ctx)
    assert result == 'created'

async def test_resolve_mention_mid_confidence_queues(fake_pool_fuzzy_match, fake_gateway, fake_mention):
    result = await resolve_mention(fake_pool_fuzzy_match, fake_gateway, fake_mention["id"], ws_id, ctx)
    assert result == 'queued'
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
cd packages/signal_core && python -m pytest tests/test_m04_identity.py -v 2>&1 | head -20
```
Expected: ImportError

- [ ] **Step 3: Write identity_tiebreak prompt** at `packages/signal_core/src/signal_core/ai_gateway/prompts/identity_tiebreak/v1.md`

Prompt: system = "You are an entity resolution expert. Given a mention and two or more candidate entities, decide which is the best match or whether none match.", user template with `{{mention}}` and `{{candidates}}`. Output model: `IdentityTiebreak(winner_index: int | None, confidence: int, reason: str)`.

- [ ] **Step 4: Implement m04_identity.py**

Key logic:
- `_lookup_aliases(conn, org_id, mention)`: query entity_alias for each alias_type in priority order; for name_country use `similarity(value_normalised, $name) > 0.5` with pg_trgm; return list of `(entity_id, alias_type, confidence)`
- `_ai_tiebreak(gateway, mention, candidates, ctx)`: call `gateway.run(Capability.JUDGE, identity_tiebreak_prompt, {...}, IdentityTiebreak, ctx)`; return winning entity_id or None
- `resolve_mention(pool, gateway, mention_id, workspace_id, ctx)`: fetch mention, call `_lookup_aliases`, route by best confidence
- `duplicate_sweep(pool, gateway, org_id, ctx)`: SELECT unresolved mentions (entity_id IS NULL), process each, return count

- [ ] **Step 5: Add worker jobs to app.py**

```python
@kill_switched("identity.resolve")
@app.task(queue="identity", name="identity.resolve")
async def identity_resolve_job(*, mention_id: str, workspace_id: str) -> None: ...

@kill_switched("identity.duplicate_sweep")
@app.task(queue="identity", name="identity.duplicate_sweep", schedule=CronExpression("0 2 * * *"))
async def identity_duplicate_sweep_job(*, org_id: str) -> None: ...
```

Add "identity" to the queue list in main.py.

- [ ] **Step 6: Wire mention creation to dispatch identity_resolve_job**

In `m03_extraction.py`, after each `app.create_mention(...)` returns a mention_id, call:
```python
await identity_resolve_job.defer_async(mention_id=str(mention_id), workspace_id=str(workspace_id))
```

- [ ] **Step 7: Run tests**

```bash
cd packages/signal_core && python -m pytest tests/test_m04_identity.py -v
```
Expected: all pass

- [ ] **Step 8: Run full test suite**

```bash
cd packages/signal_core && python -m pytest -v 2>&1 | tail -5
```
Expected: no regressions

- [ ] **Step 9: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m04_identity.py \
  packages/signal_core/src/signal_core/ai_gateway/prompts/identity_tiebreak/ \
  packages/signal_core/tests/test_m04_identity.py \
  services/worker/src/worker/app.py
git commit -m "feat(py): M4 identity resolution — alias matching, review queue, duplicate sweep"
```

---

### Task 4: Web — Review screen (identity queue)

**Files:**
- Create: `apps/web/src/app/(workspace)/review/page.tsx`
- Create: `apps/web/src/app/(workspace)/review/review-actions.tsx` (server actions)
- Modify: `apps/web/src/app/(workspace)/layout.tsx` or nav component (add Review link)

**Interfaces:**
- Consumes from Task 1: `app.resolve_review_item(p_id, p_action, p_new_entity_name)` via supabase RPC
- Consumes from existing: supabase server client, workspace middleware, `app.has_workspace_role`
- Produces: `/review` page visible to Editor+ showing pending review_item queue

**Page design:**
- Server component: `select review_item.*, mention.*, entity.display_name as candidate_name from public.review_item join public.mention on mention_id = mention.id left join public.entity on candidate_entity = entity.id where review_item.workspace_id = $ws and status = 'pending' order by created_at limit 50`
- Each row: show mention raw_name, raw_domain, raw_country | candidate entity display_name, match_method, match_confidence% | action buttons: Merge (green), Skip (gray), Create New (blue)
- Server actions in `review-actions.tsx`:
  - `mergeAction(itemId: string)` → `supabase.rpc('resolve_review_item', { p_id: itemId, p_action: 'merge' })`
  - `skipAction(itemId: string)` → `supabase.rpc('resolve_review_item', { p_id: itemId, p_action: 'skip' })`
  - `createNewAction(itemId: string, newName: string)` → `supabase.rpc('resolve_review_item', { p_id: itemId, p_action: 'create_new', p_new_entity_name: newName })`
- Revalidate path after each action

- [ ] **Step 1: Create review-actions.tsx**

```typescript
'use server'
import { createServerClient } from '@/lib/supabase/server'
import { revalidatePath } from 'next/cache'

export async function mergeAction(itemId: string): Promise<void> {
  const supabase = createServerClient()
  const { error } = await supabase.rpc('resolve_review_item', {
    p_id: itemId, p_action: 'merge'
  })
  if (error) throw new Error(error.message)
  revalidatePath('/review')
}
// similar skipAction and createNewAction
```

- [ ] **Step 2: Create review/page.tsx as server component**

Fetch pending review items for current workspace, render table with inline form buttons using `useActionState` or plain form actions. Show empty state ("No items to review") when queue is empty.

- [ ] **Step 3: Add Review nav link**

Add `{ href: '/review', label: 'Review', icon: ... }` to the workspace nav alongside Radars.

- [ ] **Step 4: Type-check**

```bash
cd apps/web && npx tsc --noEmit 2>&1 | tail -10
```
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/\(workspace\)/review/
git commit -m "feat(web): Review screen — identity queue merge/skip/create-new"
```

---

### Task 5: M3 backfill job — re-extract on brief version published

**Files:**
- Modify: `services/worker/src/worker/app.py` (add `extract_backfill_job`)
- Modify: `packages/signal_core/src/signal_core/modules/m03_extraction.py` (export `extract_backfill`)
- Test: `packages/signal_core/tests/test_m03_extraction.py` (add backfill test)

**Interfaces:**
- Consumes from Task 2: `extract_relevance_judge_job.defer_async`, source_record query pattern
- Trigger: fired when `brief.version_published` Procrastinate event fires (see m02_radar.py for event pattern)
- Logic: SELECT source_records with `workspace_id = p_workspace_id AND extraction_status = 'pending'` and re-queue each for relevance judge

- [ ] **Step 1: Add backfill test**

```python
async def test_backfill_queues_pending_records(fake_pool_with_pending_records, ...):
    count = await extract_backfill(fake_pool, brief_id, workspace_id)
    assert count == 3  # 3 pending records queued
```

- [ ] **Step 2: Implement extract_backfill in m03_extraction.py**

```python
async def extract_backfill(pool, brief_id: UUID, workspace_id: UUID) -> int:
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "select id from public.source_record where workspace_id = %s and extraction_status = 'pending'",
                (str(workspace_id),)
            )
            rows = await cur.fetchall()
    for (sr_id,) in rows:
        await extract_relevance_judge_job.defer_async(source_record_id=str(sr_id))
    return len(rows)
```

- [ ] **Step 3: Add worker job in app.py**

```python
@kill_switched("extract.relevance_judge")
@app.task(queue="extract", name="extract.backfill")
async def extract_backfill_job(*, brief_id: str, workspace_id: str) -> None:
    async with get_pool() as pool:
        await extract_backfill(pool, UUID(brief_id), UUID(workspace_id))
```

Wire to `brief.version_published` event handler (dispatch backfill job alongside existing radar.generate_queries dispatch).

- [ ] **Step 4: Run tests**

```bash
cd packages/signal_core && python -m pytest tests/test_m03_extraction.py -v
```
Expected: all pass including new backfill test

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m03_extraction.py \
  packages/signal_core/tests/test_m03_extraction.py \
  services/worker/src/worker/app.py
git commit -m "feat(py): M3 backfill job — re-queue pending source_records on brief.version_published"
```

---

### Task 6: pgTAP tests — suite 09 extraction and identity

**Files:**
- Modify: `supabase/tests/database/09_extraction_identity.test.sql` (replace skeleton with full suite)

**Interfaces:**
- Consumes from Task 1: all RPCs from migration 0018
- Follows pattern from `08_connectors_radars.test.sql` (temp table for ids, plan(N), explicit rollback)

**Test groups (12 total):**
1. `app.update_extraction_status` — service_role can update; authenticated cannot
2. `app.create_mention` — service_role creates mention; authenticated cannot
3. `app.create_claim` — service_role creates claim with valid predicate; authenticated cannot
4. `app.create_entity` — service_role creates entity; authenticated cannot; display_name trim enforced
5. `app.create_entity_alias` — upsert: second insert with same (org, type, value, entity) updates confidence
6. `app.resolve_mention` — sets entity_id, match_method, match_confidence; authenticated cannot call
7. `app.create_account` — creates account; ON CONFLICT DO NOTHING on duplicate (workspace, entity)
8. `app.merge_entities` — winner gets loser's mentions; loser status = 'merged'; authenticated cannot call
9. `app.create_review_item` — service_role creates; authenticated cannot directly
10. `app.resolve_review_item` — Editor can merge/skip/create_new; Viewer cannot; second resolve on pending item raises
11. review_item RLS — workspace isolation: Viewer in ws-A cannot see ws-B review_items
12. source_record extraction_status read — authenticated Viewer can SELECT source_record (from migration 0007)

- [ ] **Step 1: Write plan(12) and all 12 test groups**

Follow the established pattern:
```sql
begin;
select plan(12);

create temp table ids (name text primary key, val uuid);
-- setup: org, workspace, member (Editor), member (Viewer), predicate, source_record
...
select lives_ok(
  $$ select app.update_extraction_status((select val from ids where name = 'sr'), 'extracted', null) $$,
  'service_role can update extraction_status'
);
...
select * from finish();
rollback;
```

- [ ] **Step 2: Verify plan count matches actual tests**

```bash
grep -c "^select " supabase/tests/database/09_extraction_identity.test.sql
```
Confirm count ≥ 12 (plan assertions + finish()).

- [ ] **Step 3: Commit**

```bash
git add supabase/tests/database/09_extraction_identity.test.sql
git commit -m "test(db): pgTAP suite 09 — extraction/identity RPCs and RLS"
```
