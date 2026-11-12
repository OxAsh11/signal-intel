# Phase 1b — Brief Studio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Brief Studio (M1 without pilot): AI-assisted intake, editable chips for all fields, required-field gate before publish, workspace predicates ("add a column"), per-Brief exclusion list, state machine (Draft → Active ⇄ Paused → Archived), and two-version diff.

**Architecture:** The `brief` table already exists (migration 0008) but needs a `draft_config` JSONB column and `intake_status` text column to hold work-in-progress state. AI intake runs as a Procrastinate job (`brief.intake`) dispatched via the existing outbox relay when the `brief.intake_requested` event fires. All writes go through SECURITY DEFINER SQL functions; the web reads under RLS. The brief detail page polls with `router.refresh()` while intake is pending, switching to the full editor once `intake_status = 'done'`.

**Tech Stack:** SQL migrations + pgTAP · Python 3.13 / Pydantic v2 / Procrastinate · `signal_core.ai_gateway` (Capability.WRITE) · Next.js 16 App Router / Cache Components / `useActionState` · Tailwind v4 + `@signal/ui`

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §3 (write paths), §6 (Phase 1); PRD M1, F3 (predicates), A.2 (closed value lists)

## Global Constraints

- Every user write is a server action → Postgres SECURITY DEFINER RPC → one transaction (change + audit_log + outbox). No table grants for `authenticated`.
- RLS on every public table; no INSERT/UPDATE/DELETE grants to `anon` or `authenticated`.
- `brief_version` rows are immutable once inserted (trigger `app.forbid_mutation()`). Edits always publish a new version.
- Brief cannot leave Draft unless `draft_config` contains: `offer` (non-empty), `hypothesis` (non-empty), `examples.positive` with ≥ 3 items, `examples.negative` with ≥ 2 items, `sources.radar_types` with ≥ 1 item.
- State transitions allowed: Draft → Active (publish); Active ↔ Paused; Active → Archived; Paused → Archived. All others raise an error.
- Event type `brief.intake_requested` must be added to the `public.event_type` enum via migration.
- AI gateway: use `Capability.WRITE` for intake. The gateway falls back to fake adapter when no Anthropic key is set; the job must succeed (saves the fake output) so the UI is always testable without keys.
- Next.js 16: `PageProps<'/briefs/[id]'>` has `params: Promise<{ id: string }>`. Use `'use cache'` + `cacheLife` for reads; call `refresh()` from `'next/cache'` after mutations.
- Worker: register `brief.intake` via the `@job` decorator + `app.periodic`/task as appropriate; no extra infrastructure.
- All DB tests use existing `plan(N)` / pgTAP structure in `supabase/tests/database/`.
- Python module lives at `packages/signal_core/src/signal_core/modules/m01_brief.py`.
- Prompt file: `packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v1.md` (update `LOCK.json` after adding).
- `exclusion_list` row's `pattern` column stores exact name or domain text; no regex. `entity_id` is nullable for unresolved exclusions.

## Review Focus

- Publish with a missing required field should raise `42501`, never silently create a version with an empty offer.
- `update_brief_state` must reject invalid transitions (e.g. Draft → Paused) with a clear error code.
- `brief_version_validate` trigger enforces sequential versions; a concurrent publish must fail on the second transaction with a constraint violation, not silently overwrite.
- Outbox relay must mark a `brief.intake_requested` event published whether the job succeeds or fails (not leave it in the pending queue indefinitely).
- An Operator or Viewer calling `app.publish_brief` must get `42501`; only Editor+ may publish.

---

### Task 1: DB migration — schema additions and RPC functions

**Files:**
- Create: `supabase/migrations/20261108000015_brief_studio.sql`
- Modify: `supabase/tests/database/07_brief_studio.test.sql` (new file)
- Modify: `scripts/check-migrations.mjs` (add brief-studio checks)
- Modify: `packages/contracts/schema/enums.json` (add `brief.intake_requested` to EventType)

**Interfaces:**
- Produces: `app.create_brief(p_ws uuid, p_name text) returns uuid`
- Produces: `app.save_brief_draft(p_brief uuid, p_config jsonb) returns void`
- Produces: `app.fail_brief_intake(p_brief uuid) returns void`
- Produces: `app.publish_brief(p_brief uuid, p_change_note text default null) returns uuid` (new version id)
- Produces: `app.update_brief_state(p_brief uuid, p_new_state public.brief_state) returns void`
- Produces: `public.create_workspace_predicate(p_ws uuid, p_key text, p_label text, p_value_type text, p_allowed_values jsonb default null, p_extraction_hint text default null) returns uuid`
- Produces: `public.add_exclusion(p_brief uuid, p_value_type text, p_entity_id uuid default null, p_pattern text default null, p_reason text default null) returns uuid`
- Produces: `public.remove_exclusion(p_id uuid) returns void`
- Produces: `public.list_briefs(p_ws uuid) returns table(id uuid, name text, state public.brief_state, intake_status text, version_count bigint, current_version int, updated_at timestamptz)`

- [ ] **Step 1: Write failing pgTAP test** `supabase/tests/database/07_brief_studio.test.sql`

```sql
begin;
create extension if not exists pgtap with schema extensions;
select plan(16);
-- Uses same Alpha/Beta org setup as 06_metering.test.sql (copy the insert block)
-- Tests:
-- 1. create_brief creates a draft brief with intake_status='pending'
-- 2. save_brief_draft sets draft_config and intake_status='done'
-- 3. publish_brief with missing offer raises 42501
-- 4. publish_brief with all required fields returns a version id, sets state='Active'
-- 5. brief_version is immutable (update raises)
-- 6. update_brief_state Active→Paused ok; Draft→Paused raises
-- 7. create_workspace_predicate creates predicate with workspace_id set
-- 8. add_exclusion with entity_id ok; with pattern ok; with neither raises
-- 9. remove_exclusion deletes the row; Viewer cannot remove (42501)
-- 10. list_briefs returns rows for workspace Viewer, not for another workspace
-- 11. Operator cannot publish (42501)
-- 12. event_type enum includes 'brief.intake_requested'
select finish();
rollback;
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pnpm db:check
```
Expected: `FAIL apply 20261108000015_brief_studio.sql` or test failures

- [ ] **Step 3: Write migration `supabase/migrations/20261108000015_brief_studio.sql`**

Schema additions:
```sql
-- add brief.intake_requested to the closed event-type enum (reviewed change, PRD §S4)
alter type public.event_type add value 'brief.intake_requested';

-- work-in-progress config and intake tracking on the brief row
alter table public.brief
  add column draft_config    jsonb,
  add column intake_status   text not null default 'none'
    check (intake_status in ('none', 'pending', 'done', 'failed'));

-- F3 / M1: companies a Brief must not target (tenant-scoped, brief-scoped)
create table public.exclusion_list (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  brief_id     uuid not null references public.brief (id) on delete restrict,
  value_type   text not null check (value_type in ('entity', 'name', 'domain')),
  entity_id    uuid references public.entity (id),
  pattern      text,  -- exact name or domain text; null for entity exclusions
  reason       text,
  added_by     uuid references public.member (id),
  added_at     timestamptz not null default now(),
  constraint exclusion_needs_value check (
    (value_type = 'entity' and entity_id is not null and pattern is null) or
    (value_type in ('name', 'domain') and pattern is not null and entity_id is null)
  )
);
alter table public.exclusion_list enable row level security;
create policy exclusion_read on public.exclusion_list for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.exclusion_list to authenticated;
create index exclusion_brief_idx on public.exclusion_list (brief_id);
```

RPC functions (all SECURITY DEFINER, search_path = ''):
- `app.create_brief(p_ws, p_name)`: checks Editor+, inserts brief with state='Draft' and intake_status='pending', writes audit+outbox(`brief.intake_requested`, payload=`{"brief_id":"…","name":"…"}`), returns brief id.
- `app.save_brief_draft(p_brief, p_config)`: service_role only (called by worker), sets `draft_config = p_config`, `intake_status = 'done'`, `updated_at = now()`.
- `app.fail_brief_intake(p_brief)`: service_role only, sets `intake_status = 'failed'`, `updated_at = now()`.
- `app.publish_brief(p_brief, p_change_note)`: checks Editor+, validates required fields in draft_config (raises 42501 if missing), inserts brief_version (config=draft_config, rubric='{}', weights='{}'), updates brief.current_version_id + state='Active' + intake_status='none' + draft_config=null, writes audit+outbox(`brief.version_published`), returns new version id.
- `app.update_brief_state(p_brief, p_new_state)`: checks Editor+, validates transition (Draft→Active blocked here — use publish_brief; allowed: Active↔Paused, Active→Archived, Paused→Archived), updates brief.state, writes audit+outbox as appropriate.
- `public.create_workspace_predicate(p_ws, p_key, p_label, p_value_type, p_allowed_values, p_extraction_hint)`: checks Editor+, inserts predicate with org_id+workspace_id from p_ws, returns predicate id.
- `public.add_exclusion(p_brief, p_value_type, p_entity_id, p_pattern, p_reason)`: checks Editor+, inserts exclusion_list row, returns id.
- `public.remove_exclusion(p_id)`: checks Editor+ on the exclusion's workspace, deletes row.
- `public.list_briefs(p_ws)`: Viewer+ on p_ws; returns id, name, state, intake_status, version count, current version number, updated_at.

Revoke anon/authenticated execute on all new non-public functions; grant public functions to authenticated.

- [ ] **Step 4: Update `packages/contracts/schema/enums.json`**

Add `"brief.intake_requested"` to the `EventType` enum's `values` array.

- [ ] **Step 5: Run codegen to regenerate contracts**

```bash
pnpm --filter @signal/contracts generate
```
Expected: `packages/contracts/gen/py/enums.py` and `gen/ts/enums.ts` updated with `brief.intake_requested`

- [ ] **Step 6: Run checks to verify they pass**

```bash
pnpm db:check
```
Expected: all checks pass including new brief-studio checks

- [ ] **Step 7: Add check-migrations checks for brief studio**

In `scripts/check-migrations.mjs`, add checks for:
- `exclusion_list` table exists and has RLS enabled
- `app.create_brief`, `app.publish_brief`, `app.save_brief_draft` functions exist
- `public.list_briefs` exists and is callable
- `brief.intake_requested` exists in event_type enum
- `brief.draft_config` column exists on `public.brief`

- [ ] **Step 8: Run final check**

```bash
pnpm db:check
```
Expected: all checks pass

- [ ] **Step 9: Commit**

```bash
git add supabase/migrations/20261108000015_brief_studio.sql \
        supabase/tests/database/07_brief_studio.test.sql \
        scripts/check-migrations.mjs \
        packages/contracts/schema/enums.json \
        packages/contracts/gen/
git commit -m "feat(db): brief studio schema — draft_config, exclusion_list, brief RPCs (0015)"
```

---

### Task 2: Python brief intake module

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v1.md`
- Modify: `packages/signal_core/src/signal_core/ai_gateway/prompts/LOCK.json`
- Modify: `packages/signal_core/src/signal_core/ai_gateway/outputs.py`
- Create: `packages/signal_core/src/signal_core/modules/m01_brief.py`
- Create: `packages/signal_core/evals/brief_intake/v1.jsonl`
- Create: `packages/signal_core/tests/test_brief_module.py`

**Interfaces:**
- Consumes: `AiGateway.run(Capability.WRITE, prompt, vars, BriefDraft, ctx)` from `signal_core.ai_gateway.gateway`
- Consumes: `load_prompt("brief_intake", 1)` from `signal_core.ai_gateway.prompts`
- Consumes: `pool: AsyncConnectionPool` for DB calls
- Produces: `BriefDraft` Pydantic model (exported from `m01_brief.py`)
- Produces: `run_brief_intake(brief_id, description, ctx, gateway, pool) -> None` async function
- Produces: `BriefIntakeInput` Pydantic model with `brief_id: UUID`, `description: str`

- [ ] **Step 1: Write failing test** `packages/signal_core/tests/test_brief_module.py`

```python
@pytest.mark.asyncio
async def test_draft_has_required_fields(fake_gateway, mock_pool):
    draft = await run_brief_intake(UUID("…"), "CAD firms that are hiring", fake_ctx(), fake_gateway, mock_pool)
    # run_brief_intake returns None; check that save_brief_draft was called
    assert mock_pool.last_call_fn == "app.save_brief_draft"

def test_brief_draft_model_validates():
    raw = {"offer": "CAD software", "hypothesis": "firms using SolidWorks",
           "examples": {"positive": ["A","B","C"], "negative": ["X","Y"]},
           "sources": {"radar_types": ["job_postings"]}}
    draft = BriefDraft.model_validate(raw)
    assert len(draft.examples.positive) == 3

def test_brief_draft_model_rejects_too_few_examples():
    with pytest.raises(ValidationError):
        BriefDraft.model_validate({
            "offer": "X", "hypothesis": "Y",
            "examples": {"positive": ["A","B"], "negative": ["X","Y"]},  # only 2 positive
            "sources": {"radar_types": ["news"]}})
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run --package signal_core pytest packages/signal_core/tests/test_brief_module.py -v
```
Expected: ImportError / test failures

- [ ] **Step 3: Write prompt `packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/v1.md`**

```
capability: write
---
You convert a natural-language description of a go-to-market hypothesis into a structured Brief.
Return ONLY valid JSON matching this schema — no commentary, no markdown fences.

Required fields:
- offer: what is being sold (string)
- hypothesis: the observable public fact that marks a target, as a clear predicate statement (string)
- examples.positive: at least 3 real or plausible company names or descriptions that would match (array of strings)
- examples.negative: at least 2 real or plausible company names or descriptions that would NOT match (array of strings)
- sources.radar_types: list of applicable radar types from: news_publications, job_postings, public_registries, tenders_procurement, events, directories, company_websites (array of at least 1)

Optional fields (include when clearly implied):
- icp.verticals: industry verticals (array of strings)
- icp.geographies: countries or regions (array of strings)
- icp.size_bands: e.g. ["1-50", "51-200"] (array of strings)
- icp.commercial_roles: e.g. ["buyer", "operator"] (array of strings)
- personas.buyer: buyer title patterns (array of strings)
- personas.user: user title patterns (array of strings)
- personas.influencer: influencer title patterns (array of strings)

Do not invent data. If a field is unclear, omit it rather than guess.
--- user ---
Description: {description}
```

- [ ] **Step 4: Update LOCK.json**

Compute SHA-256 of `v1.md` (normalised line endings) and add entry:
```json
{ "brief_intake/v1": "<sha256>" }
```

- [ ] **Step 5: Add `BriefDraft` output model to `outputs.py`**

```python
from pydantic import BaseModel, field_validator

class BriefExamples(BaseModel):
    positive: list[str]
    negative: list[str]

    @field_validator("positive")
    @classmethod
    def at_least_3_positive(cls, v: list[str]) -> list[str]:
        if len(v) < 3:
            raise ValueError("at least 3 positive examples required")
        return v

    @field_validator("negative")
    @classmethod
    def at_least_2_negative(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("at least 2 negative examples required")
        return v

class BriefSources(BaseModel):
    radar_types: list[str]

    @field_validator("radar_types")
    @classmethod
    def at_least_1(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least 1 radar type required")
        return v

class BriefIcp(BaseModel):
    verticals: list[str] = []
    geographies: list[str] = []
    size_bands: list[str] = []
    commercial_roles: list[str] = []

class BriefPersonas(BaseModel):
    buyer: list[str] = []
    user: list[str] = []
    influencer: list[str] = []

class BriefDraft(BaseModel):
    offer: str
    hypothesis: str
    examples: BriefExamples
    sources: BriefSources
    icp: BriefIcp = Field(default_factory=BriefIcp)
    personas: BriefPersonas = Field(default_factory=BriefPersonas)

OUTPUT_MODELS["brief_intake"] = BriefDraft
```

- [ ] **Step 6: Write `m01_brief.py`**

```python
"""M1 Brief Studio — Python side: AI intake job."""
from pydantic import BaseModel
from uuid import UUID
from psycopg_pool import AsyncConnectionPool
from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.outputs import BriefDraft
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.metering import CallContext

PROMPT_NAME = "brief_intake"
PROMPT_VERSION = 1

class BriefIntakeInput(BaseModel):
    brief_id: UUID
    description: str
    org_id: UUID
    workspace_id: UUID

async def run_brief_intake(
    inp: BriefIntakeInput,
    gateway: AiGateway,
    pool: AsyncConnectionPool,
) -> None:
    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    ctx = CallContext(
        org_id=inp.org_id,
        workspace_id=inp.workspace_id,
        brief_id=inp.brief_id,
        job_id=None,
        job_type="brief.intake",
        job_args={"description": inp.description},
    )
    try:
        draft: BriefDraft = await gateway.run(
            Capability.WRITE, prompt, {"description": inp.description}, BriefDraft, ctx
        )
        config = draft.model_dump()
    except Exception:
        async with pool.connection() as conn:
            await conn.execute(
                "select app.fail_brief_intake(%s)", (str(inp.brief_id),)
            )
        raise
    async with pool.connection() as conn:
        import json
        await conn.execute(
            "select app.save_brief_draft(%s, %s)",
            (str(inp.brief_id), json.dumps(config)),
        )
```

- [ ] **Step 7: Write 4 eval cases** `packages/signal_core/evals/brief_intake/v1.jsonl`

Each line: `{"input": {"description": "..."}, "expect": {"offer_non_empty": true, "examples_positive_min": 3}}`

Four diverse industry descriptions: CAD engineering firms + hiring, cold-storage warehouse operators, hospital groups implementing EHR, public works contractors seeking tender awards.

- [ ] **Step 8: Run tests to verify they pass**

```bash
uv run --package signal_core pytest packages/signal_core/tests/test_brief_module.py -v
```
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add packages/signal_core/src/signal_core/ai_gateway/prompts/brief_intake/ \
        packages/signal_core/src/signal_core/ai_gateway/prompts/LOCK.json \
        packages/signal_core/src/signal_core/ai_gateway/outputs.py \
        packages/signal_core/src/signal_core/modules/m01_brief.py \
        packages/signal_core/evals/brief_intake/ \
        packages/signal_core/tests/test_brief_module.py
git commit -m "feat(worker): brief intake prompt, BriefDraft model and m01_brief module"
```

---

### Task 3: Worker brief.intake job registration

**Files:**
- Modify: `services/worker/src/worker/app.py`

**Interfaces:**
- Consumes: `BriefIntakeInput`, `run_brief_intake` from `signal_core.modules.m01_brief`
- Consumes: `gateway: AiGateway` and `pool: AsyncConnectionPool` passed via closure in `build_app`
- Produces: `brief.intake` Procrastinate task registered in the worker app
- Produces: outbox dispatch handler updated to dispatch `brief.intake` for `brief.intake_requested` events

- [ ] **Step 1: Write failing test** (no separate test file — verify by running the worker health check)

Verify `build_app(...)` doesn't raise when `brief.intake` is registered.

```bash
uv run --package worker python -c "from worker.app import build_app; print('ok')"
```
Expected: ImportError (m01_brief not wired yet)

- [ ] **Step 2: Register brief.intake job and dispatch handler in `app.py`**

Add imports:
```python
from signal_core.modules.m01_brief import BriefIntakeInput, run_brief_intake
```

In `build_app(...)` signature, add `gateway: AiGateway` parameter.

Add job after existing jobs:
```python
@job(app, type="brief.intake", kill_switch=kill_switch, queue="briefs", retry=1)
async def brief_intake(
    brief_id: str, description: str, org_id: str, workspace_id: str, **_: Any
) -> None:
    inp = BriefIntakeInput(
        brief_id=UUID(brief_id), description=description,
        org_id=UUID(org_id), workspace_id=UUID(workspace_id),
    )
    await run_brief_intake(inp, gateway, pool)
    log.info("brief.intake.done", brief_id=brief_id)
```

Update `dispatch` handler to route `brief.intake_requested`:
```python
@job(app, type=DISPATCH_TASK, kill_switch=kill_switch, queue="events")
async def dispatch(**event: Any) -> None:
    evt = event.get("event", "")
    payload = event.get("payload") or {}
    if evt == "brief.intake_requested":
        await brief_intake.defer_async(
            brief_id=payload["brief_id"],
            description=payload.get("description", ""),
            org_id=payload["org_id"],
            workspace_id=payload["workspace_id"],
        )
    log.info("outbox.dispatched", event_id=event.get("event_id"), event_type=evt)
```

Update `main.py` to pass `gateway` to `build_app`.

- [ ] **Step 3: Run verify**

```bash
uv run --package worker python -c "from worker.app import build_app; print('ok')"
```
Expected: `ok`

- [ ] **Step 4: Run worker smoke test for 10 seconds** (manual check if dev keys unavailable, automated in CI)

```bash
uv run --package worker python -m worker.main &
sleep 8 && kill %1
```
Expected: worker starts, no import errors, heartbeat fires

- [ ] **Step 5: Commit**

```bash
git add services/worker/src/worker/app.py services/worker/src/worker/main.py
git commit -m "feat(worker): register brief.intake job and outbox dispatch handler"
```

---

### Task 4: Server actions for brief CRUD

**Files:**
- Create: `apps/web/src/app/actions/briefs.ts`

**Interfaces:**
- Consumes: `createClient` from `@/lib/supabase/server`, `getOrgContext`/`friendlyError` from `@/lib/org`
- Consumes: `ActionState` from `./access`
- Produces: `createBrief(_: ActionState, form: FormData): Promise<ActionState & { briefId?: string }>`
- Produces: `saveBriefDraft(_: ActionState, form: FormData): Promise<ActionState>`
- Produces: `publishBrief(_: ActionState, form: FormData): Promise<ActionState>`
- Produces: `updateBriefState(_: ActionState, form: FormData): Promise<ActionState>`
- Produces: `createWorkspacePredicate(_: ActionState, form: FormData): Promise<ActionState>`
- Produces: `addExclusion(_: ActionState, form: FormData): Promise<ActionState>`
- Produces: `removeExclusion(_: ActionState, form: FormData): Promise<ActionState>`

- [ ] **Step 1: Write failing test** (TypeScript; just verify imports compile)

```bash
pnpm --filter web typecheck
```
Expected: type errors on missing `briefs.ts`

- [ ] **Step 2: Implement `apps/web/src/app/actions/briefs.ts`**

Pattern follows `actions/budgets.ts`:
```typescript
'use server';
import { redirect } from 'next/navigation';
import { refresh } from 'next/cache';
import { createClient } from '@/lib/supabase/server';
import { friendlyError } from '@/lib/org';
import type { ActionState } from './access';

async function rpc(fn: string, args: Record<string, unknown>) {
  const supabase = await createClient();
  if (!supabase) return { data: null, error: 'Supabase is not configured.' };
  const { data, error } = await supabase.rpc(fn, args);
  return { data, error: error ? friendlyError(error.message) : null };
}

const text = (form: FormData, key: string) => String(form.get(key) ?? '').trim();

export async function createBrief(_: ActionState, form: FormData): Promise<ActionState> {
  const name = text(form, 'name');
  const description = text(form, 'description');
  const workspace = text(form, 'workspace');
  if (!name) return { error: 'Enter a name for the Brief.' };
  if (!workspace) return { error: 'Choose a workspace first.' };
  const { data, error } = await rpc('create_brief', {
    p_ws: workspace, p_name: name, p_description: description,
  });
  if (error) return { error };
  redirect(`/briefs/${data as string}`);
}

export async function saveBriefDraft(_: ActionState, form: FormData): Promise<ActionState> {
  const brief = text(form, 'brief');
  const config = text(form, 'config');
  if (!brief || !config) return { error: 'Invalid request.' };
  let parsed: unknown;
  try { parsed = JSON.parse(config); } catch { return { error: 'Invalid config JSON.' }; }
  const { error } = await rpc('save_brief_draft_ui', { p_brief: brief, p_config: parsed });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function publishBrief(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('publish_brief', {
    p_brief: text(form, 'brief'),
    p_change_note: text(form, 'change_note') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function updateBriefState(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('update_brief_state', {
    p_brief: text(form, 'brief'),
    p_new_state: text(form, 'state'),
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function createWorkspacePredicate(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('create_workspace_predicate', {
    p_ws: text(form, 'workspace'),
    p_key: text(form, 'key'),
    p_label: text(form, 'label'),
    p_value_type: text(form, 'value_type'),
    p_allowed_values: text(form, 'allowed_values') ? JSON.parse(text(form, 'allowed_values')) : null,
    p_extraction_hint: text(form, 'extraction_hint') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function addExclusion(_: ActionState, form: FormData): Promise<ActionState> {
  const valType = text(form, 'value_type');
  const { error } = await rpc('add_exclusion', {
    p_brief: text(form, 'brief'),
    p_value_type: valType,
    p_entity_id: text(form, 'entity_id') || null,
    p_pattern: text(form, 'pattern') || null,
    p_reason: text(form, 'reason') || null,
  });
  if (error) return { error };
  refresh();
  return { ok: true };
}

export async function removeExclusion(_: ActionState, form: FormData): Promise<ActionState> {
  const { error } = await rpc('remove_exclusion', { p_id: text(form, 'id') });
  if (error) return { error };
  refresh();
  return { ok: true };
}
```

Note: `save_brief_draft_ui` is a public RPC wrapper around `app.save_brief_draft` that checks Editor+ role (the worker calls the app. version directly via service_role; the UI uses a public wrapper).

Add `public.save_brief_draft_ui(p_brief uuid, p_config jsonb) returns void` to migration 0015 (Editor+ role check, then calls `app.save_brief_draft`). Update Task 1 to include this function.

- [ ] **Step 3: Verify typecheck passes**

```bash
pnpm --filter web typecheck
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/actions/briefs.ts
git commit -m "feat(web): server actions for brief CRUD — create, draft, publish, state, predicates, exclusions"
```

---

### Task 5: Brief list page and intake form

**Files:**
- Modify: `apps/web/src/app/(app)/briefs/page.tsx` (replace placeholder)
- Create: `apps/web/src/app/(app)/briefs/new/page.tsx`
- Create: `apps/web/src/app/(app)/briefs/brief-list.tsx`
- Create: `apps/web/src/app/(app)/briefs/intake-form.tsx`

**Interfaces:**
- Consumes: `list_briefs(p_ws)` via supabase-js `.rpc()`
- Consumes: `createBrief` server action from `@/app/actions/briefs`
- Consumes: `getOrgContext` from `@/lib/org`
- Consumes: `atLeast` from `@/lib/org`

- [ ] **Step 1: Write brief-list server component** `apps/web/src/app/(app)/briefs/brief-list.tsx`

```tsx
import 'server-only';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import Link from 'next/link';

export async function BriefList() {
  const ctx = await getOrgContext();
  const supabase = await createClient();
  if (!ctx?.workspace || !supabase) return <p className="m-0 text-ink-muted">No workspace selected.</p>;

  const { data, error } = await supabase.rpc('list_briefs', { p_ws: ctx.workspace.id });
  if (error) return <p className="m-0 text-danger">{error.message}</p>;
  if (!data?.length) return (
    <p className="m-0 text-ink-muted">
      No Briefs yet.{' '}
      <Link href="/briefs/new" className="text-accent underline-offset-2 hover:underline">Create the first one.</Link>
    </p>
  );

  // Group by state: Draft, Active, Paused, Archived
  const groups = ['Draft', 'Active', 'Paused', 'Archived'] as const;
  const byState = Object.fromEntries(groups.map(s => [s, data.filter(b => b.state === s)]));

  return (
    <div className="flex flex-col gap-6">
      {groups.filter(s => byState[s].length > 0).map(state => (
        <section key={state}>
          <h2 className="mb-2 text-sm font-medium text-ink-muted uppercase tracking-wide">{state}</h2>
          <ul className="flex flex-col gap-2">
            {byState[state].map(b => (
              <li key={b.id}>
                <Link href={`/briefs/${b.id}`}
                  className="flex items-center justify-between rounded-md border border-line bg-surface px-4 py-3 hover:bg-surface-raised transition-colors">
                  <span className="font-medium">{b.name}</span>
                  <span className="text-xs text-ink-muted tabular-nums">
                    {b.intake_status === 'pending' ? '⏳ drafting…' :
                     b.version_count ? `v${b.current_version}` : 'draft'}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Replace `/briefs/page.tsx`**

```tsx
import type { Metadata } from 'next';
import { Suspense } from 'react';
import Link from 'next/link';
import { Button } from '@signal/ui';
import { atLeast, getOrgContext } from '@/lib/org';
import { BriefList } from './brief-list';

export const metadata: Metadata = { title: 'Brief Studio' };

export default async function BriefStudioPage() {
  const ctx = await getOrgContext();
  const canEdit = atLeast(ctx?.workspaceRole, 'Editor');
  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">Brief Studio</h1>
          <p className="m-0 max-w-[65ch] text-ink-muted">
            Define what you are looking for. Each Brief is a versioned hypothesis the platform collects and scores accounts against.
          </p>
        </div>
        {canEdit && (
          <Link href="/briefs/new">
            <Button variant="primary">New Brief</Button>
          </Link>
        )}
      </div>
      <Suspense fallback={<p className="m-0 text-ink-muted">Loading…</p>}>
        <BriefList />
      </Suspense>
    </>
  );
}
```

- [ ] **Step 3: Write intake form** `apps/web/src/app/(app)/briefs/intake-form.tsx` (client component)

```tsx
'use client';
import { useActionState } from 'react';
import { ActionForm, inputClass } from '@/components/forms';
import { createBrief } from '@/app/actions/briefs';
import { Button } from '@signal/ui';

export function IntakeForm({ workspaceId }: { workspaceId: string }) {
  const [state, action] = useActionState(createBrief, {});
  return (
    <ActionForm action={action} state={state}>
      <input type="hidden" name="workspace" value={workspaceId} />
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Brief name</span>
        <input name="name" required placeholder="e.g. CAD firms hiring 2026" className={inputClass} />
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-sm font-medium">Describe what you're looking for</span>
        <textarea name="description" rows={5} required
          placeholder="e.g. Engineering firms that design in SolidWorks or CATIA and are actively hiring mechanical engineers…"
          className={inputClass} />
        <p className="text-xs text-ink-muted">The AI will draft the structured Brief from this description. You can edit every field afterwards.</p>
      </label>
      <Button type="submit" variant="primary">Draft with AI</Button>
    </ActionForm>
  );
}
```

- [ ] **Step 4: Write `/briefs/new/page.tsx`**

```tsx
import type { Metadata } from 'next';
import { getOrgContext, atLeast } from '@/lib/org';
import { redirect } from 'next/navigation';
import { IntakeForm } from '../intake-form';

export const metadata: Metadata = { title: 'New Brief' };

export default async function NewBriefPage() {
  const ctx = await getOrgContext();
  if (!atLeast(ctx?.workspaceRole, 'Editor')) redirect('/briefs');
  return (
    <>
      <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">New Brief</h1>
      <p className="m-0 max-w-[65ch] text-ink-muted">
        Describe your hypothesis in plain language. The AI drafts the structured fields; you review and edit each one before publishing.
      </p>
      <IntakeForm workspaceId={ctx!.workspace!.id} />
    </>
  );
}
```

- [ ] **Step 5: Verify typecheck**

```bash
pnpm --filter web typecheck
```
Expected: PASS

- [ ] **Step 6: Run the dev server and check the page renders**

```bash
pnpm --filter web dev &
# navigate to http://localhost:3000/briefs
```
Expected: list page renders without errors; "New Brief" button navigates to intake form

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/app/(app)/briefs/
git commit -m "feat(web): brief list page and AI intake form"
```

---

### Task 6: Brief detail page with chips editor and state controls

**Files:**
- Create: `apps/web/src/app/(app)/briefs/[id]/page.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/brief-intake-loading.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/brief-editor.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/chip-field.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/state-controls.tsx`

**Interfaces:**
- Consumes: `saveBriefDraft`, `publishBrief`, `updateBriefState` server actions
- Consumes: `.from('brief').select('id, name, state, intake_status, draft_config, current_version_id').eq('id', id).single()` via supabase-js (Viewer+ RLS)
- Consumes: `PageProps<'/briefs/[id]'>` for route params

- [ ] **Step 1: Write brief detail server page** `apps/web/src/app/(app)/briefs/[id]/page.tsx`

```tsx
import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { Suspense } from 'react';
import { createClient } from '@/lib/supabase/server';
import { getOrgContext, atLeast } from '@/lib/org';
import { BriefIntakeLoading } from './brief-intake-loading';
import { BriefEditor } from './brief-editor';
import { StateControls } from './state-controls';

export const metadata: Metadata = { title: 'Brief' };

export default async function BriefDetailPage({ params }: PageProps<'/briefs/[id]'>) {
  const { id } = await params;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: brief, error } = await supabase
    .from('brief').select('id, name, state, intake_status, draft_config, current_version_id, updated_at')
    .eq('id', id).eq('workspace_id', ctx.workspace.id).maybeSingle();

  if (error || !brief) notFound();
  const canEdit = atLeast(ctx.workspaceRole, 'Editor');

  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="m-0 text-[28px] leading-[34px] font-semibold tracking-[-0.015em]">{brief.name}</h1>
          <p className="m-0 text-sm text-ink-muted">
            {brief.state}
            {brief.state === 'Draft' && brief.intake_status === 'pending' && ' · AI is drafting…'}
            {brief.state === 'Draft' && brief.intake_status === 'failed' && ' · Draft failed — edit manually or retry'}
          </p>
        </div>
        {canEdit && <StateControls brief={brief} />}
      </div>

      {brief.intake_status === 'pending'
        ? <BriefIntakeLoading briefId={id} />
        : <BriefEditor brief={brief} canEdit={canEdit} workspaceId={ctx.workspace.id} />}
    </>
  );
}
```

- [ ] **Step 2: Write `brief-intake-loading.tsx`** (client component, polls every 3s)

```tsx
'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

export function BriefIntakeLoading({ briefId }: { briefId: string }) {
  const router = useRouter();
  useEffect(() => {
    const id = setInterval(() => router.refresh(), 3000);
    return () => clearInterval(id);
  }, [router]);
  return (
    <div className="flex flex-col gap-3 rounded-md border border-line bg-surface p-6 animate-pulse">
      <p className="m-0 text-ink-muted">The AI is drafting your Brief. This takes about 10–15 seconds…</p>
      <div className="h-4 w-48 rounded bg-line" />
      <div className="h-4 w-72 rounded bg-line" />
      <div className="h-4 w-56 rounded bg-line" />
    </div>
  );
}
```

- [ ] **Step 3: Write `chip-field.tsx`** (client component — a single expandable editable chip)

ChipField renders a label + current value as a compact chip. Clicking expands to show an edit form (textarea or input). On submit, calls `saveBriefDraft` with the whole updated config. Used for: Offer, Hypothesis, Examples (positive/negative), Sources, ICP, Personas, Budget.

Each chip shows:
- Required chips (Offer, Hypothesis, Examples, Sources): orange "Required" indicator if empty
- Optional chips: greyed-out "Not set" if empty
- Filled chips: show preview of value and an "Edit" affordance

```tsx
'use client';
import { useState, useActionState } from 'react';
import { Button } from '@signal/ui';
import { saveBriefDraft } from '@/app/actions/briefs';
import { ActionForm } from '@/components/forms';

type Props = {
  briefId: string;
  fieldName: string;
  label: string;
  required?: boolean;
  currentConfig: Record<string, unknown>;
  renderValue: (config: Record<string, unknown>) => React.ReactNode;
  renderEditor: (config: Record<string, unknown>, onChange: (patch: Record<string, unknown>) => void) => React.ReactNode;
};

export function ChipField({ briefId, fieldName, label, required, currentConfig, renderValue, renderEditor }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(currentConfig);
  const [state, action] = useActionState(saveBriefDraft, {});

  const isEmpty = !currentConfig[fieldName] ||
    (Array.isArray((currentConfig[fieldName] as any)?.positive) && (currentConfig[fieldName] as any).positive.length === 0);

  return (
    <div className={`rounded-md border ${open ? 'border-accent' : 'border-line'} bg-surface`}>
      <button type="button" onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between px-4 py-3 text-left">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          {required && isEmpty && <span className="text-xs text-caution font-medium">Required</span>}
        </div>
        {!open && <span className="text-sm text-ink-muted">{isEmpty ? 'Not set' : renderValue(currentConfig)}</span>}
      </button>
      {open && (
        <ActionForm action={action} state={state} className="border-t border-line p-4 flex flex-col gap-3">
          <input type="hidden" name="brief" value={briefId} />
          <input type="hidden" name="config" value={JSON.stringify(draft)} />
          {renderEditor(draft, patch => setDraft(d => ({ ...d, ...patch })))}
          <div className="flex gap-2">
            <Button type="submit" variant="primary" size="sm">Save</Button>
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
          {state?.error && <p className="text-xs text-danger">{state.error}</p>}
        </ActionForm>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Write `brief-editor.tsx`** (client component — composes all chips)

Renders all brief fields as ChipFields + publish button. Validates required fields before publish.

```tsx
'use client';
import { useActionState } from 'react';
import { ChipField } from './chip-field';
import { publishBrief } from '@/app/actions/briefs';
import { Button } from '@signal/ui';
import { ActionForm } from '@/components/forms';
import type { BriefConfig } from '@/lib/briefs';

type Brief = { id: string; state: string; draft_config: BriefConfig | null };

function isPublishable(cfg: BriefConfig | null): boolean {
  if (!cfg) return false;
  return !!(cfg.offer?.trim() && cfg.hypothesis?.trim() &&
    (cfg.examples?.positive?.length ?? 0) >= 3 &&
    (cfg.examples?.negative?.length ?? 0) >= 2 &&
    (cfg.sources?.radar_types?.length ?? 0) >= 1);
}

export function BriefEditor({ brief, canEdit, workspaceId }: { brief: Brief; canEdit: boolean; workspaceId: string }) {
  const cfg = brief.draft_config ?? {};
  const [pubState, pubAction] = useActionState(publishBrief, {});
  const canPublish = brief.state === 'Draft' && canEdit && isPublishable(cfg);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <ChipField briefId={brief.id} fieldName="offer" label="Offer" required
          currentConfig={cfg}
          renderValue={c => <span className="line-clamp-1">{String(c.offer ?? '')}</span>}
          renderEditor={(c, onChange) => (
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">What is being sold or offered?</span>
              <input defaultValue={String(c.offer ?? '')}
                onChange={e => onChange({ ...c, offer: e.target.value })}
                className="rounded border border-line bg-surface-raised px-3 py-2 text-sm" />
            </label>
          )} />
        <ChipField briefId={brief.id} fieldName="hypothesis" label="Hypothesis" required
          currentConfig={cfg}
          renderValue={c => <span className="line-clamp-1">{String(c.hypothesis ?? '')}</span>}
          renderEditor={(c, onChange) => (
            <label className="flex flex-col gap-1">
              <span className="text-xs text-ink-muted">The observable public fact that marks a target.</span>
              <textarea rows={3} defaultValue={String(c.hypothesis ?? '')}
                onChange={e => onChange({ ...c, hypothesis: e.target.value })}
                className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y" />
            </label>
          )} />
        {/* Examples chip: renders two text-areas, one for positive (comma-separated) and one for negative */}
        <ChipField briefId={brief.id} fieldName="examples" label="Examples" required
          currentConfig={cfg}
          renderValue={c => {
            const ex = c.examples as { positive?: string[]; negative?: string[] } | undefined;
            return <span>{ex?.positive?.length ?? 0} positive · {ex?.negative?.length ?? 0} negative</span>;
          }}
          renderEditor={(c, onChange) => {
            const ex = (c.examples as { positive?: string[]; negative?: string[] }) ?? {};
            return (
              <div className="flex flex-col gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">Positive examples (≥ 3, one per line)</span>
                  <textarea rows={4} defaultValue={(ex.positive ?? []).join('\n')}
                    onChange={e => onChange({ ...c, examples: { ...ex, positive: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-xs text-ink-muted">Negative examples (≥ 2, one per line)</span>
                  <textarea rows={3} defaultValue={(ex.negative ?? []).join('\n')}
                    onChange={e => onChange({ ...c, examples: { ...ex, negative: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })}
                    className="rounded border border-line bg-surface-raised px-3 py-2 text-sm resize-y font-mono text-xs" />
                </label>
              </div>
            );
          }} />
        {/* Sources chip */}
        <ChipField briefId={brief.id} fieldName="sources" label="Sources" required
          currentConfig={cfg}
          renderValue={c => <span>{((c.sources as any)?.radar_types ?? []).join(', ') || 'None'}</span>}
          renderEditor={(c, onChange) => {
            const RADAR_TYPES = ['news_publications','job_postings','public_registries','tenders_procurement','events','directories','company_websites'];
            const current = ((c.sources as any)?.radar_types ?? []) as string[];
            return (
              <fieldset className="flex flex-col gap-1">
                <legend className="text-xs text-ink-muted mb-1">Select at least one radar type</legend>
                {RADAR_TYPES.map(t => (
                  <label key={t} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={current.includes(t)}
                      onChange={e => {
                        const next = e.target.checked ? [...current, t] : current.filter(x => x !== t);
                        onChange({ ...c, sources: { ...((c.sources as any) ?? {}), radar_types: next } });
                      }} />
                    {t.replace(/_/g, ' ')}
                  </label>
                ))}
              </fieldset>
            );
          }} />
      </div>

      {brief.state === 'Draft' && canEdit && (
        <ActionForm action={pubAction} state={pubState} className="pt-2">
          <input type="hidden" name="brief" value={brief.id} />
          <Button type="submit" variant="primary" disabled={!canPublish}>
            Publish Brief
          </Button>
          {!canPublish && (
            <p className="mt-1 text-xs text-ink-muted">Fill in all required fields before publishing.</p>
          )}
          {pubState?.error && <p className="text-xs text-danger mt-1">{pubState.error}</p>}
        </ActionForm>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Write `state-controls.tsx`** (client component)

Shows "Pause" / "Resume" / "Archive" buttons appropriate to current state.

```tsx
'use client';
import { useActionState } from 'react';
import { Button } from '@signal/ui';
import { ActionForm } from '@/components/forms';
import { updateBriefState } from '@/app/actions/briefs';

type Brief = { id: string; state: string };

export function StateControls({ brief }: { brief: Brief }) {
  const [state, action] = useActionState(updateBriefState, {});
  const transitions: { label: string; next: string; variant?: 'primary' | 'danger' | 'ghost' }[] =
    brief.state === 'Active' ? [
      { label: 'Pause', next: 'Paused', variant: 'ghost' },
      { label: 'Archive', next: 'Archived', variant: 'danger' },
    ] : brief.state === 'Paused' ? [
      { label: 'Resume', next: 'Active', variant: 'primary' },
      { label: 'Archive', next: 'Archived', variant: 'danger' },
    ] : [];

  if (!transitions.length) return null;

  return (
    <div className="flex gap-2 items-center">
      {transitions.map(t => (
        <ActionForm key={t.next} action={action} state={state} className="inline">
          <input type="hidden" name="brief" value={brief.id} />
          <input type="hidden" name="state" value={t.next} />
          <Button type="submit" variant={t.variant ?? 'ghost'} size="sm">{t.label}</Button>
        </ActionForm>
      ))}
      {state?.error && <p className="text-xs text-danger">{state.error}</p>}
    </div>
  );
}
```

- [ ] **Step 6: Add BriefConfig type** `apps/web/src/lib/briefs.ts`

```typescript
export type BriefConfig = {
  offer?: string;
  hypothesis?: string;
  examples?: { positive?: string[]; negative?: string[] };
  sources?: { radar_types?: string[] };
  icp?: { verticals?: string[]; geographies?: string[]; size_bands?: string[]; commercial_roles?: string[] };
  personas?: { buyer?: string[]; user?: string[]; influencer?: string[] };
  predicates?: string[];
  seeds?: { company_names?: string[]; company_domains?: string[] };
  research_template?: Record<string, unknown>;
  scoring?: Record<string, unknown>;
  budget?: { day_cap?: number | null; month_cap?: number | null };
};
```

- [ ] **Step 7: Verify typecheck**

```bash
pnpm --filter web typecheck
```
Expected: PASS

- [ ] **Step 8: Run dev server and test the brief detail page**

Navigate to a Brief created in the previous task. Verify: intake loading shows while pending, chips render with correct required indicators, Publish button disabled without all required fields, Publish enabled with all fields.

- [ ] **Step 9: Commit**

```bash
git add apps/web/src/app/(app)/briefs/[id]/ apps/web/src/lib/briefs.ts
git commit -m "feat(web): brief detail page — chips editor, intake loading, state controls"
```

---

### Task 7: Exclusion list and workspace predicates in brief detail

**Files:**
- Create: `apps/web/src/app/(app)/briefs/[id]/exclusions.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/predicates.tsx`
- Modify: `apps/web/src/app/(app)/briefs/[id]/page.tsx` (add sections)

**Interfaces:**
- Consumes: `addExclusion`, `removeExclusion`, `createWorkspacePredicate` server actions
- Consumes: `.from('exclusion_list').select(...)` and `.from('predicate').select(...)` via supabase-js

- [ ] **Step 1: Write `exclusions.tsx`** (server + client mix)

Server part fetches current exclusions for the brief. Client part provides add/remove form.

```tsx
import { createClient } from '@/lib/supabase/server';
import { addExclusion, removeExclusion } from '@/app/actions/briefs';
// ...
// Renders a list of exclusions with Remove buttons
// Renders an Add Exclusion form: type (entity/name/domain), pattern text input, reason
```

- [ ] **Step 2: Write `predicates.tsx`** ("Add a column")

```tsx
import { createClient } from '@/lib/supabase/server';
import { createWorkspacePredicate } from '@/app/actions/briefs';
// Lists predicates scoped to this workspace (where workspace_id = ctx.workspace.id)
// Shows an "Add a column" form: key, label, value_type (dropdown: boolean/number/enum/text/date),
//   optional allowed_values (for enum), optional extraction_hint
// New predicates immediately visible in the list; their key is added to brief.draft_config.predicates
//   via a saveBriefDraft call in the createWorkspacePredicate server action
```

- [ ] **Step 3: Add sections to brief detail page**

In `/briefs/[id]/page.tsx`, after `<BriefEditor>`, add:
```tsx
<Suspense fallback={null}>
  <Exclusions briefId={id} canEdit={canEdit} />
</Suspense>
<Suspense fallback={null}>
  <Predicates briefId={id} workspaceId={ctx.workspace.id} canEdit={canEdit} />
</Suspense>
```

- [ ] **Step 4: Verify typecheck and render**

```bash
pnpm --filter web typecheck
```
Expected: PASS. Navigate to a Brief page; verify exclusions and predicates sections render (empty).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/(app)/briefs/[id]/exclusions.tsx \
        apps/web/src/app/(app)/briefs/[id]/predicates.tsx \
        apps/web/src/app/(app)/briefs/[id]/page.tsx
git commit -m "feat(web): brief exclusion list and workspace predicates ('add a column')"
```

---

### Task 8: Version compare

**Files:**
- Create: `apps/web/src/app/(app)/briefs/[id]/versions/page.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/versions/version-diff.tsx`

**Interfaces:**
- Consumes: `.from('brief_version').select('id, version, config, rubric, weights, edit_tier, change_note, published_at').eq('brief_id', id).order('version')` via supabase-js

- [ ] **Step 1: Write version list page** `apps/web/src/app/(app)/briefs/[id]/versions/page.tsx`

```tsx
import { createClient } from '@/lib/supabase/server';
import { getOrgContext } from '@/lib/org';
import { notFound } from 'next/navigation';
import { VersionDiff } from './version-diff';

export default async function VersionsPage({ params, searchParams }: PageProps<'/briefs/[id]/versions'>) {
  const { id } = await params;
  const sp = await searchParams;
  const [ctx, supabase] = await Promise.all([getOrgContext(), createClient()]);
  if (!ctx?.workspace || !supabase) return null;

  const { data: versions, error } = await supabase
    .from('brief_version').select('id, version, config, rubric, weights, edit_tier, change_note, published_at')
    .eq('brief_id', id).order('version');

  if (error || !versions) notFound();

  const aV = Number(Array.isArray(sp.a) ? sp.a[0] : sp.a) || null;
  const bV = Number(Array.isArray(sp.b) ? sp.b[0] : sp.b) || null;
  const vA = versions.find(v => v.version === aV) ?? versions.at(-2) ?? versions.at(0) ?? null;
  const vB = versions.find(v => v.version === bV) ?? versions.at(-1) ?? null;

  return (
    <>
      <h2 className="m-0 text-xl font-semibold">Version history</h2>
      {versions.length < 2
        ? <p className="m-0 text-ink-muted">Publish at least two versions to compare them.</p>
        : <VersionDiff versions={versions} vA={vA} vB={vB} briefId={id} />}
    </>
  );
}
```

- [ ] **Step 2: Write `version-diff.tsx`** (client component)

Shows two version pickers (selects) and a table of changed fields.

```tsx
'use client';
import { useRouter } from 'next/navigation';

type Version = { id: string; version: number; config: Record<string, unknown>; published_at: string; change_note: string | null };

const FIELDS: { key: string; label: string }[] = [
  { key: 'offer', label: 'Offer' },
  { key: 'hypothesis', label: 'Hypothesis' },
  { key: 'examples', label: 'Examples' },
  { key: 'sources', label: 'Sources' },
  { key: 'icp', label: 'ICP' },
  { key: 'personas', label: 'Personas' },
  { key: 'predicates', label: 'Predicates' },
  { key: 'seeds', label: 'Seeds' },
];

function display(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value || '—';
  return JSON.stringify(value, null, 2);
}

export function VersionDiff({ versions, vA, vB, briefId }:
  { versions: Version[]; vA: Version | null; vB: Version | null; briefId: string }) {
  const router = useRouter();
  const setVersions = (a: number, b: number) =>
    router.push(`/briefs/${briefId}/versions?a=${a}&b=${b}`);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-3 items-center">
        <select value={vA?.version ?? ''} onChange={e => setVersions(Number(e.target.value), vB?.version ?? 1)}
          className="rounded border border-line bg-surface px-3 py-2 text-sm">
          {versions.map(v => <option key={v.id} value={v.version}>v{v.version} — {new Date(v.published_at).toLocaleDateString()}</option>)}
        </select>
        <span className="text-ink-muted">vs</span>
        <select value={vB?.version ?? ''} onChange={e => setVersions(vA?.version ?? 1, Number(e.target.value))}
          className="rounded border border-line bg-surface px-3 py-2 text-sm">
          {versions.map(v => <option key={v.id} value={v.version}>v{v.version} — {new Date(v.published_at).toLocaleDateString()}</option>)}
        </select>
      </div>

      {vA && vB && (
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr>
              <th className="text-left pb-2 w-32 text-ink-muted font-medium">Field</th>
              <th className="text-left pb-2 pl-4 text-ink-muted font-medium">v{vA.version}</th>
              <th className="text-left pb-2 pl-4 text-ink-muted font-medium">v{vB.version}</th>
            </tr>
          </thead>
          <tbody>
            {FIELDS.map(({ key, label }) => {
              const aVal = vA.config[key];
              const bVal = vB.config[key];
              const changed = JSON.stringify(aVal) !== JSON.stringify(bVal);
              return (
                <tr key={key} className={changed ? 'bg-caution/5' : ''}>
                  <td className="py-2 font-medium align-top">{label}</td>
                  <td className={`py-2 pl-4 font-mono text-xs align-top whitespace-pre-wrap ${changed ? 'text-caution' : ''}`}>
                    {display(aVal)}
                  </td>
                  <td className={`py-2 pl-4 font-mono text-xs align-top whitespace-pre-wrap ${changed ? 'text-accent' : ''}`}>
                    {display(bVal)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add versions link to brief detail page**

In `/briefs/[id]/page.tsx`, add after the header:
```tsx
<Link href={`/briefs/${id}/versions`} className="text-sm text-ink-muted hover:text-ink">
  Version history →
</Link>
```

- [ ] **Step 4: Verify typecheck**

```bash
pnpm --filter web typecheck
```
Expected: PASS

- [ ] **Step 5: Run dev server and test version compare**

Navigate to `/briefs/<id>/versions` on a brief with ≥ 2 published versions. Verify: version pickers work, changed fields highlight in caution/accent, unchanged fields show identical values.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/(app)/briefs/[id]/versions/
git commit -m "feat(web): brief version history and two-version diff"
```

---

## Final steps (after all tasks complete)

Run the full test suite:

```bash
pnpm db:check
uv run --package signal_core pytest packages/signal_core/tests/ -v
pnpm --filter web typecheck
```

Then use `superpowers:finishing-a-development-branch` to review and merge.
