# Phase 1e — Derivation Engine, Verdicts, and Search/Indexing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the S5 derivation engine, M5 verdict computation, and S9 search indexing so that after extraction + identity resolution, every entity gets a verdict backed by evidence quotes, and every source record is chunked and embedded for semantic search.

**Architecture:** The derivation engine is two write-path SQL RPCs (`app.upsert_verdict`, `app.upsert_memory_chunk`) that set `app.writer = 'derivation'` transaction-locally, bypassing the guard trigger. Python modules call these RPCs directly — no context-manager wrapper needed. Verdict computation is triggered by a new `verdict.compute` Procrastinate job deferred from the identity-resolve job on outcome 'attached'/'created'. Indexing is triggered by a new `index.chunk_embed` job deferred from `extract.structured` on completion. Both jobs use the existing `@job` + kill-switch pattern.

**Tech Stack:** PostgreSQL RPC + guard-trigger bypass, psycopg3 async, Procrastinate jobs, Pydantic v2, AiGateway (JUDGE for verdict, EMBED for chunks), pgvector, Next.js 16 App Router.

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §S5, §M5, §S9

## Global Constraints

- S8 write discipline: all INSERT/UPDATE/DELETE goes through `app.*` SECURITY DEFINER RPCs; no direct-INSERT in Python
- Every migration block: REVOKE EXECUTE FROM public, anon, authenticated BEFORE any GRANT block
- `app.upsert_verdict` and `app.upsert_memory_chunk` have NO GRANT to authenticated (worker calls via service_role)
- Kill switches must be seeded in `job_type_config` for every new job type
- `verdict_value` enum: 'Confirmed', 'Likely', 'Unverified', 'Contradicted'
- `confirmed_has_claims` CHECK: `value <> 'Confirmed' OR cardinality(claim_ids) > 0`
- `memory_chunk` unique on `(doc_type, doc_id, chunk_index, embedding_model)`
- Python module `m05_verdict.py` uses `Capability.JUDGE`; `m05_index.py` uses `gateway.embed()`
- `verdict.changed` outbox event is emitted inside `app.upsert_verdict`
- Job handle pattern: `verdict_compute_job: object | None = None` assigned by `build_app()`
- Chunk size: 1 500 chars; overlap: 200 chars; `input_type="document"` for embed calls

## Review Focus

1. **Guard bypass works within same transaction**: `set_config('app.writer','derivation',true)` with `is_local=true` — verify the INSERT fires within the same transaction before the config reverts.
2. **Confirmed verdict with no claims is blocked**: the `confirmed_has_claims` CHECK rejects it; RPC must pass non-empty `claim_ids` or use a weaker verdict value.
3. **Embedder not configured**: if `gateway._embedder is None`, `gateway.embed()` raises `AiNotConfigured` — indexer must catch and log, not crash the job.
4. **Brief has no published version**: `verdict.compute` looks up `latest published brief_version`; if none, job should return early without writing.
5. **On conflict for memory_chunk with NULL embedding_model**: the unique key includes `embedding_model`; NULL ≠ NULL in SQL, so two NULL-model chunks would not conflict — embedding_model must always be set.

---

## File Map

| File | Action |
|---|---|
| `supabase/migrations/20261109000020_derivation_rpcs.sql` | Create |
| `supabase/tests/database/10_derivation_verdicts.test.sql` | Create |
| `packages/signal_core/src/signal_core/derivation/__init__.py` | Create |
| `packages/signal_core/src/signal_core/derivation/writer.py` | Create |
| `packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/v1.md` | Create |
| `packages/signal_core/src/signal_core/modules/m05_verdict.py` | Create |
| `packages/signal_core/src/signal_core/modules/m05_index.py` | Create |
| `services/worker/src/worker/app.py` | Modify (add verdict + index jobs, wire dispatch) |
| `packages/signal_core/src/signal_core/modules/m03_extraction.py` | Modify (defer index job after extract_structured) |
| `packages/signal_core/src/signal_core/modules/m04_identity.py` | Modify (defer verdict job after resolve_mention) |
| `apps/web/src/app/(app)/workspace/[wsid]/verdicts/page.tsx` | Create |

---

### Task 1: DB migration 0020 — derivation write-path RPCs + kill-switch seeds

**Files:**
- Create: `supabase/migrations/20261109000020_derivation_rpcs.sql`

**Interfaces:**
- Produces: `app.upsert_verdict(p_brief_version_id uuid, p_entity_id uuid, p_value public.verdict_value, p_claim_ids uuid[], p_quotes jsonb, p_inputs jsonb) RETURNS uuid`
- Produces: `app.upsert_memory_chunk(p_org_id uuid, p_workspace_id uuid, p_entity_id uuid, p_brief_id uuid, p_doc_type text, p_doc_id uuid, p_chunk_index int, p_content text, p_embedding extensions.vector, p_embedding_model text, p_scope public.data_scope, p_occurred_at timestamptz) RETURNS uuid`
- Produces: kill-switch seeds for 'verdict.compute' and 'index.chunk_embed' in `job_type_config`

- [ ] **Step 1: Write the migration**

```sql
-- app.upsert_verdict
-- Performs set_config('app.writer','derivation',true) so the guard trigger allows the write.
-- Validates that entity and brief_version belong to the same org.
-- ON CONFLICT (brief_version_id, entity_id) DO UPDATE.
-- Emits verdict.changed outbox event in the same transaction.
-- No GRANT to authenticated or anon.
CREATE OR REPLACE FUNCTION app.upsert_verdict(
  p_brief_version_id uuid,
  p_entity_id        uuid,
  p_value            public.verdict_value,
  p_claim_ids        uuid[],
  p_quotes           jsonb,
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_verdict_id   uuid;
BEGIN
  -- Resolve org + workspace from brief_version
  SELECT bv.org_id, bv.workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.brief_version bv WHERE bv.id = p_brief_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief_version % not found', p_brief_version_id USING ERRCODE = '22023';
  END IF;
  -- Verify entity belongs to same org
  IF NOT EXISTS (SELECT 1 FROM public.entity e WHERE e.id = p_entity_id AND e.org_id = v_org_id) THEN
    RAISE EXCEPTION 'entity % does not belong to org %', p_entity_id, v_org_id USING ERRCODE = '42501';
  END IF;
  -- Allow derivation write for this transaction
  PERFORM set_config('app.writer', 'derivation', true);
  INSERT INTO public.verdict
    (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids, quotes, inputs)
  VALUES
    (v_org_id, v_workspace_id, p_brief_version_id, p_entity_id, p_value, p_claim_ids, p_quotes, p_inputs)
  ON CONFLICT (brief_version_id, entity_id) DO UPDATE
    SET value = EXCLUDED.value,
        claim_ids = EXCLUDED.claim_ids,
        quotes = EXCLUDED.quotes,
        inputs = EXCLUDED.inputs,
        computed_at = now()
  RETURNING id INTO v_verdict_id;
  -- Emit verdict.changed outbox event
  INSERT INTO public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  VALUES (
    'verdict.changed',
    v_org_id,
    v_workspace_id,
    'verdict',
    v_verdict_id,
    jsonb_build_object(
      'verdict_id', v_verdict_id,
      'entity_id', p_entity_id,
      'brief_version_id', p_brief_version_id,
      'value', p_value
    )
  );
  RETURN v_verdict_id;
END $$;

-- app.upsert_memory_chunk
-- ON CONFLICT (doc_type, doc_id, chunk_index, embedding_model) DO UPDATE.
CREATE OR REPLACE FUNCTION app.upsert_memory_chunk(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_entity_id       uuid,
  p_brief_id        uuid,
  p_doc_type        text,
  p_doc_id          uuid,
  p_chunk_index     int,
  p_content         text,
  p_embedding       extensions.vector,
  p_embedding_model text,
  p_scope           public.data_scope,
  p_occurred_at     timestamptz
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid; BEGIN
  PERFORM set_config('app.writer', 'derivation', true);
  INSERT INTO public.memory_chunk
    (org_id, workspace_id, entity_id, brief_id, doc_type, doc_id, chunk_index,
     content, embedding, embedding_model, scope, occurred_at)
  VALUES
    (p_org_id, p_workspace_id, p_entity_id, p_brief_id, p_doc_type, p_doc_id, p_chunk_index,
     p_content, p_embedding, p_embedding_model, p_scope, p_occurred_at)
  ON CONFLICT (doc_type, doc_id, chunk_index, embedding_model) DO UPDATE
    SET content = EXCLUDED.content,
        embedding = EXCLUDED.embedding,
        computed_at = now()
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- Grants and kill-switch seeds
REVOKE EXECUTE ON FUNCTION app.upsert_verdict FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION app.upsert_memory_chunk FROM public, anon, authenticated;

INSERT INTO public.job_type_config (job_type, enabled)
VALUES ('verdict.compute', true), ('index.chunk_embed', true)
ON CONFLICT (job_type) DO NOTHING;
```

- [ ] **Step 2: Run migration dry-run check**

```bash
grep -c "REVOKE EXECUTE" supabase/migrations/20261109000020_derivation_rpcs.sql
```
Expected: 2 (one per RPC)

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20261109000020_derivation_rpcs.sql
git commit -m "feat(db): migration 0020 — upsert_verdict + upsert_memory_chunk RPCs + kill-switch seeds"
```

---

### Task 2: Python derivation writer module

**Files:**
- Create: `packages/signal_core/src/signal_core/derivation/__init__.py`
- Create: `packages/signal_core/src/signal_core/derivation/writer.py`
- Test: `packages/signal_core/tests/test_derivation_writer.py`

**Interfaces:**
- Consumes: `app.upsert_verdict`, `app.upsert_memory_chunk` from Task 1
- Produces: `upsert_verdict(pool, brief_version_id, entity_id, value, claim_ids, quotes, inputs) → UUID`
- Produces: `upsert_memory_chunk(pool, org_id, workspace_id, entity_id, brief_id, doc_type, doc_id, chunk_index, content, embedding, embedding_model, scope, occurred_at) → UUID`

- [ ] **Step 1: Write tests**

```python
# packages/signal_core/tests/test_derivation_writer.py
import pytest
from unittest.mock import AsyncMock, MagicMock, call
from uuid import UUID, uuid4
from signal_core.derivation.writer import upsert_verdict, upsert_memory_chunk

@pytest.mark.asyncio
async def test_upsert_verdict_calls_rpc():
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.execute = AsyncMock(return_value=cur)
    verdict_id = uuid4()
    cur.fetchone = AsyncMock(return_value=(verdict_id,))
    bv_id, ent_id = uuid4(), uuid4()
    result = await upsert_verdict(pool, bv_id, ent_id, "Confirmed", [uuid4()], [], {})
    assert result == verdict_id
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "app.upsert_verdict" in sql

@pytest.mark.asyncio
async def test_upsert_memory_chunk_calls_rpc():
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.execute = AsyncMock(return_value=cur)
    chunk_id = uuid4()
    cur.fetchone = AsyncMock(return_value=(chunk_id,))
    result = await upsert_memory_chunk(
        pool, uuid4(), uuid4(), None, None,
        "source_record", uuid4(), 0, "hello", [0.1] * 1024, "voyage-3", "tenant", None
    )
    assert result == chunk_id
    sql = conn.execute.call_args[0][0]
    assert "app.upsert_memory_chunk" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_derivation_writer.py -v
```
Expected: ImportError / ModuleNotFoundError

- [ ] **Step 3: Implement `writer.py`**

`packages/signal_core/src/signal_core/derivation/writer.py`:
- `async def upsert_verdict(pool, brief_version_id: UUID, entity_id: UUID, value: str, claim_ids: list[UUID], quotes: list[dict], inputs: dict) -> UUID`
  - `SELECT app.upsert_verdict($1, $2, $3::public.verdict_value, $4::uuid[], $5::jsonb, $6::jsonb)` via `conn.execute`
  - Use `json.dumps(quotes)` and `json.dumps(inputs)` for the jsonb args
  - `claim_ids` passed as list of str-UUIDs to psycopg (it handles `uuid[]` from Python list)
- `async def upsert_memory_chunk(pool, org_id, workspace_id, entity_id, brief_id, doc_type, doc_id, chunk_index, content, embedding, embedding_model, scope, occurred_at) -> UUID`
  - `SELECT app.upsert_memory_chunk(...)` — 13 positional params
  - `embedding` is a `list[float]`; pass as `str(embedding)` with `::extensions.vector` cast in SQL, or use `psycopg` adapter

`packages/signal_core/src/signal_core/derivation/__init__.py`:
```python
from signal_core.derivation.writer import upsert_verdict, upsert_memory_chunk
__all__ = ["upsert_verdict", "upsert_memory_chunk"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_derivation_writer.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/derivation/ packages/signal_core/tests/test_derivation_writer.py
git commit -m "feat(py): S5 derivation writer — upsert_verdict + upsert_memory_chunk"
```

---

### Task 3: M5 verdict computation + verdict prompt

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/v1.md`
- Create: `packages/signal_core/src/signal_core/modules/m05_verdict.py`
- Test: `packages/signal_core/tests/test_m05_verdict.py`

**Interfaces:**
- Consumes: `upsert_verdict` from Task 2; `AiGateway.run(Capability.JUDGE, ...)` from gateway
- Produces: `compute_verdict(pool, gateway, entity_id: UUID, workspace_id: UUID, ctx: CallContext) -> str | None`
  — returns the verdict value string ('Confirmed' etc.) or None if no published brief_version found
- Produces: `verdict_compute_job: object | None = None` module-level handle

- [ ] **Step 1: Write verdict prompt**

`packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/v1.md`:
```
capability: judge
---
You are a verdict engine for a business intelligence platform.
Given a hypothesis about a type of business entity and a list of factual claims extracted from public sources, determine whether the evidence confirms, likely supports, contradicts, or is insufficient to evaluate the hypothesis for this specific entity.

Verdict values:
- "Confirmed": multiple independent claims directly confirm the hypothesis (requires at least one claim)
- "Likely": some claims support the hypothesis but evidence is incomplete
- "Unverified": no claims address the hypothesis (no evidence either way)
- "Contradicted": claims directly refute the hypothesis

Output JSON only. No commentary.
--- user ---
Hypothesis: {hypothesis}

Entity: {entity_name}

Claims (predicate: value | confidence | quote):
{claims_text}
```

Output schema: `{"value": "<verdict_value>", "reasoning": "<one sentence>", "supporting_claim_indices": [<0-based indices>]}`

- [ ] **Step 2: Write tests**

```python
# packages/signal_core/tests/test_m05_verdict.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from signal_core.modules.m05_verdict import compute_verdict, VerdictOutput
from signal_core.metering import CallContext

@pytest.mark.asyncio
async def test_compute_verdict_no_brief_version_returns_none():
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.fetchone = AsyncMock(return_value=None)  # no published brief_version
    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "verdict.compute", {})
    result = await compute_verdict(pool, gateway, uuid4(), uuid4(), ctx)
    assert result is None
    gateway.run.assert_not_called()

@pytest.mark.asyncio
async def test_compute_verdict_no_claims_returns_unverified(monkeypatch):
    # When entity has no claims, verdict is Unverified without calling AI.
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    # First fetchone: brief_version row; second fetchone: entity display_name; fetchall: claims
    cur.fetchone = AsyncMock(side_effect=[(uuid4(), "test hypothesis"), ("ACME Corp",)])
    cur.fetchall = AsyncMock(return_value=[])  # no claims
    gateway = MagicMock()
    upsert = AsyncMock(return_value=uuid4())
    monkeypatch.setattr("signal_core.modules.m05_verdict.upsert_verdict", upsert)
    ctx = CallContext(uuid4(), uuid4(), None, None, "verdict.compute", {})
    result = await compute_verdict(pool, gateway, uuid4(), uuid4(), ctx)
    assert result == "Unverified"
    gateway.run.assert_not_called()
    upsert.assert_called_once()
    # claim_ids must be empty for Unverified
    assert upsert.call_args[0][4] == []  # claim_ids
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m05_verdict.py -v
```
Expected: ImportError

- [ ] **Step 4: Implement `m05_verdict.py`**

`packages/signal_core/src/signal_core/modules/m05_verdict.py`:

```python
"""M5 Verdict computation — per entity per brief_version.

Flow:
  1. Find the latest published brief_version for the workspace (early-return if none).
  2. Fetch entity display_name + all active claims linked to the entity in the workspace.
  3. If no claims → Unverified without AI call.
  4. Otherwise run AI (Capability.JUDGE) with the verdict prompt.
  5. Write verdict via app.upsert_verdict (through derivation writer).
"""
```

Key implementation points:
- `VerdictOutput(BaseModel)` with `value: Literal["Confirmed","Likely","Unverified","Contradicted"]`, `reasoning: str`, `supporting_claim_indices: list[int]`
- Fetch latest published brief_version: `SELECT bv.id, b.draft_config->>'hypothesis' FROM public.brief_version bv JOIN public.brief b ON b.id = bv.brief_id WHERE bv.workspace_id = %s ORDER BY bv.created_at DESC LIMIT 1`
- Fetch entity name: `SELECT display_name FROM public.entity WHERE id = %s`
- Fetch claims: `SELECT c.id, c.predicate_key, c.value, c.quote, c.confidence FROM public.claim c JOIN public.mention m ON m.id = c.mention_id WHERE m.entity_id = %s AND c.workspace_id = %s AND c.status = 'active'`
- If `len(claims) == 0` → call `upsert_verdict(..., value='Unverified', claim_ids=[], quotes=[], inputs={})`; return 'Unverified'
- Build `claims_text` from claims list
- Call `gateway.run(Capability.JUDGE, prompt, {"hypothesis": hypothesis, "entity_name": display_name, "claims_text": claims_text}, VerdictOutput, ctx)`
- Build `claim_ids` from `supporting_claim_indices` mapped to actual claim UUIDs
- If value == 'Confirmed' and len(claim_ids) == 0: downgrade to 'Likely' (guard against prompt bug)
- Call `upsert_verdict(pool, brief_version_id, entity_id, value, claim_ids, quotes_list, inputs_dict)`
- Catch `AiOutputInvalid`, `AiProviderError` → log warning, return 'Unverified' without writing

Module-level: `verdict_compute_job: object | None = None`

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m05_verdict.py -v
```
Expected: 2 PASSED

- [ ] **Step 6: Commit**

```bash
git add packages/signal_core/src/signal_core/ai_gateway/prompts/verdict/ packages/signal_core/src/signal_core/modules/m05_verdict.py packages/signal_core/tests/test_m05_verdict.py
git commit -m "feat(py): M5 verdict computation + verdict prompt"
```

---

### Task 4: S9 source-record indexer (chunk + embed)

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m05_index.py`
- Test: `packages/signal_core/tests/test_m05_index.py`

**Interfaces:**
- Consumes: `upsert_memory_chunk` from Task 2; `AiGateway.embed()` from gateway
- Produces: `index_source_record(pool, gateway, source_record_id: UUID, ctx: CallContext) -> int`
  — returns number of chunks written; 0 if no content or embedder not configured
- Produces: `index_chunk_embed_job: object | None = None` module-level handle

- [ ] **Step 1: Write tests**

```python
# packages/signal_core/tests/test_m05_index.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from signal_core.modules.m05_index import chunk_text, index_source_record
from signal_core.ai_gateway.errors import AiNotConfigured
from signal_core.metering import CallContext

def test_chunk_text_single_chunk():
    text = "hello world"
    chunks = chunk_text(text)
    assert chunks == ["hello world"]

def test_chunk_text_splits_at_size():
    text = "a" * 2000
    chunks = chunk_text(text)
    assert len(chunks) == 2
    assert len(chunks[0]) == 1500
    assert chunks[1] == "a" * 700  # 2000 - 1500 + 200 overlap

def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []

@pytest.mark.asyncio
async def test_index_no_content_returns_zero():
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.fetchone = AsyncMock(return_value=None)  # source_record not found
    gateway = MagicMock()
    ctx = CallContext(uuid4(), uuid4(), None, None, "index.chunk_embed", {})
    result = await index_source_record(pool, gateway, uuid4(), ctx)
    assert result == 0

@pytest.mark.asyncio
async def test_index_embedder_not_configured_returns_zero(monkeypatch):
    pool = AsyncMock()
    conn = AsyncMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    cur = AsyncMock()
    conn.cursor.return_value.__aenter__ = AsyncMock(return_value=cur)
    conn.cursor.return_value.__aexit__ = AsyncMock(return_value=False)
    cur.fetchone = AsyncMock(return_value=(uuid4(), uuid4(), None, None, "source_record", "some content"))
    gateway = MagicMock()
    gateway.embed = AsyncMock(side_effect=AiNotConfigured("no embedder"))
    gateway.model_for = MagicMock(return_value="voyage-3")
    ctx = CallContext(uuid4(), uuid4(), None, None, "index.chunk_embed", {})
    result = await index_source_record(pool, gateway, uuid4(), ctx)
    assert result == 0
```

Note: `chunk_text` overlap semantics — chunk 0: `text[0:1500]`, chunk 1: `text[1300:2800]` (step = 1500-200 = 1300). The test for `"a" * 2000`: chunk 0 = `text[0:1500]`, chunk 1 = `text[1300:2000]` = `"a" * 700`. Correct.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m05_index.py -v
```
Expected: ImportError

- [ ] **Step 3: Implement `m05_index.py`**

`packages/signal_core/src/signal_core/modules/m05_index.py`:

```python
"""S9 Indexer — chunk source_record content and embed for semantic search.

Flow:
  1. Fetch source_record (org_id, workspace_id, entity_id, brief_id, content).
  2. chunk_text(content) → list of overlapping text chunks.
  3. gateway.embed(chunks, ctx, input_type="document") → list[list[float]].
  4. upsert_memory_chunk for each chunk.
  Returns count of chunks written.
"""
```

Key implementation:
- `CHUNK_SIZE = 1500`, `CHUNK_STEP = 1300` (= 1500 - 200 overlap)
- `chunk_text(text: str) -> list[str]`: strip, return `[]` if empty; `[text[i:i+CHUNK_SIZE] for i in range(0, len(text), CHUNK_STEP)]`
- Fetch source_record: `SELECT org_id, workspace_id, entity_id, brief_id, content FROM public.source_record WHERE id = %s`
  — `content` may be None; return 0 if None or empty
- Try `gateway.embed(chunks, ctx, input_type="document")`; catch `AiNotConfigured` → log info, return 0; catch other → re-raise
- `embedding_model = gateway.model_for(Capability.EMBED)` — call before `gateway.embed` to get model name
- Call `upsert_memory_chunk` for each (chunk, vector) pair; `scope='tenant'`, `doc_type='source_record'`, `doc_id=source_record_id`, `occurred_at=None` (NULL)
- Returns count

Module-level: `index_chunk_embed_job: object | None = None`

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m05_index.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m05_index.py packages/signal_core/tests/test_m05_index.py
git commit -m "feat(py): S9 indexer — chunk_text + index_source_record"
```

---

### Task 5: Worker wiring — verdict + index jobs + dispatch hooks

**Files:**
- Modify: `services/worker/src/worker/app.py`
- Modify: `packages/signal_core/src/signal_core/modules/m03_extraction.py`
- Modify: `packages/signal_core/src/signal_core/modules/m04_identity.py`
- Test: `services/worker/tests/test_worker.py` (add 2 tests)

**Interfaces:**
- Consumes: `compute_verdict`, `verdict_compute_job` from Task 3; `index_source_record`, `index_chunk_embed_job` from Task 4
- Consumes: `extract_structured` and `resolve_mention` from m03/m04
- Produces: `verdict.compute` Procrastinate job in `app.tasks`; `index.chunk_embed` Procrastinate job in `app.tasks`

- [ ] **Step 1: Write tests**

Add to `services/worker/tests/test_worker.py`:
```python
def test_build_app_registers_verdict_compute(app_fixture):
    assert "verdict.compute" in app_fixture.tasks

def test_build_app_registers_index_chunk_embed(app_fixture):
    assert "index.chunk_embed" in app_fixture.tasks
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/worker && uv run pytest tests/test_worker.py::test_build_app_registers_verdict_compute tests/test_worker.py::test_build_app_registers_index_chunk_embed -v
```
Expected: FAIL (KeyError or AssertionError)

- [ ] **Step 3: Modify `app.py` — add verdict + index jobs**

In `build_app()`, after the identity section, add:

```python
# ------------------------------------------------------------------
# Verdict computation job (Phase 1e / M5)
# ------------------------------------------------------------------

@job(app, type="verdict.compute", kill_switch=kill_switch, queue="derive", retry=2)
async def verdict_compute_task(*, entity_id: str, workspace_id: str, org_id: str, **_: Any) -> str | None:
    assert pool is not None and gateway is not None, "pool and gateway required"
    from uuid import UUID as _UUID
    import signal_core.modules.m05_verdict as _m05_verdict
    ctx = CallContext(
        org_id=_UUID(org_id),
        workspace_id=_UUID(workspace_id),
        brief_id=None,
        job_id=None,
        job_type="verdict.compute",
        job_args={"entity_id": entity_id, "workspace_id": workspace_id},
    )
    return await _m05_verdict.compute_verdict(pool, gateway, _UUID(entity_id), _UUID(workspace_id), ctx)

# ------------------------------------------------------------------
# Indexer job (Phase 1e / S9)
# ------------------------------------------------------------------

@job(app, type="index.chunk_embed", kill_switch=kill_switch, queue="derive", retry=2)
async def index_chunk_embed_task(*, source_record_id: str, **_: Any) -> int:
    assert pool is not None and gateway is not None, "pool and gateway required"
    from uuid import UUID as _UUID
    import signal_core.modules.m05_index as _m05_index
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT org_id, workspace_id FROM public.source_record WHERE id = %s",
                (source_record_id,),
            )
            row = await cur.fetchone()
    if row is None:
        log.warning("index.source_record_not_found", source_record_id=source_record_id)
        return 0
    ctx = CallContext(
        org_id=_UUID(str(row[0])),
        workspace_id=_UUID(str(row[1])),
        brief_id=None,
        job_id=None,
        job_type="index.chunk_embed",
        job_args={"source_record_id": source_record_id},
    )
    return await _m05_index.index_source_record(pool, gateway, _UUID(source_record_id), ctx)
```

Wire module handles after `build_app` sections:
```python
import signal_core.modules.m05_verdict as _m05_verdict
import signal_core.modules.m05_index as _m05_index
_m05_verdict.verdict_compute_job = verdict_compute_task
_m05_index.index_chunk_embed_job = index_chunk_embed_task
```

- [ ] **Step 4: Modify `m03_extraction.py` — defer index job after extraction**

In `extract_structured`, at the end of the try block, after updating `extraction_status = 'extracted'`, add:
```python
# Defer indexing — silently skip if job handle not yet wired
try:
    if _m05_index.index_chunk_embed_job is not None:
        import contextlib
        from procrastinate.exceptions import AlreadyEnqueued
        with contextlib.suppress(AlreadyEnqueued):
            await _m05_index.index_chunk_embed_job.configure(
                queueing_lock=str(source_record_id)
            ).defer_async(source_record_id=str(source_record_id))
except (ImportError, AttributeError):
    pass
```

Add module-level import of m05_index via lazy reference (same pattern as m04_identity):
```python
# At module level in m03_extraction.py:
import signal_core.modules.m05_index as _m05_index
# (This module is imported lazily by build_app — don't wire at import time)
```

Actually, to follow the same lazy pattern as m04_identity.py uses for `identity_resolve_job`, use the same approach: import the module at top-level (the module object), and the job handle is accessed via `_m05_index.index_chunk_embed_job`. The import of the module is fine since m05_index doesn't import back into m03.

- [ ] **Step 5: Modify `m04_identity.py` — defer verdict job after resolution**

In `resolve_mention`, after the final `return "attached"` and before `return "created"` (at end of `_create_new`), add a deferred call. Actually, it's cleaner to add it after `resolve_mention` returns in the main function. The best place is at the end of `resolve_mention()` and at the end of `_create_new()`.

For the 'attached' path (confidence >= 90): after the conn.execute block + log.info, before `return "attached"`, add:
```python
await _defer_verdict(org_id, workspace_id)
```

For _create_new, before `return "created"`, add:
```python
await _defer_verdict(org_id, workspace_id)
```

Add helper at module level:
```python
async def _defer_verdict(org_id: UUID, workspace_id: UUID) -> None:
    """Defer verdict computation after a mention is resolved to an entity."""
    import signal_core.modules.m05_verdict as _m05_verdict
    import contextlib
    from procrastinate.exceptions import AlreadyEnqueued
    try:
        if _m05_verdict.verdict_compute_job is not None:
            with contextlib.suppress(AlreadyEnqueued):
                await _m05_verdict.verdict_compute_job.configure(
                    queueing_lock=f"verdict:{org_id}:{workspace_id}"
                ).defer_async(
                    entity_id=str(org_id),  # NOTE: incorrect — see below
                    workspace_id=str(workspace_id),
                    org_id=str(org_id),
                )
    except (ImportError, AttributeError):
        pass
```

Wait — this is wrong. I need entity_id, not org_id. The entity_id is returned from `_create_new` and is the entity being resolved. I need to refactor to pass it.

Better approach: add `entity_id` parameter to `_defer_verdict`, and call it with the actual entity_id:
- In 'attached' path: `entity_id = entity_id` (already in scope)
- In `_create_new`: `entity_id = entity_id` (already in scope, that's what the function creates/reuses)

```python
async def _defer_verdict(entity_id_str: str, workspace_id: UUID) -> None:
    ...
    await _m05_verdict.verdict_compute_job.configure(
        queueing_lock=f"verdict:{entity_id_str}:{workspace_id}"
    ).defer_async(
        entity_id=entity_id_str,
        workspace_id=str(workspace_id),
        org_id=str(org_id),  # NOT AVAILABLE HERE
    )
```

Actually `org_id` is not available inside `_defer_verdict`. Let me include it as a parameter.

```python
async def _defer_verdict(entity_id_str: str, org_id: UUID, workspace_id: UUID) -> None:
```

Call it with the entity_id string from both paths.

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd services/worker && uv run pytest tests/test_worker.py -v
```
Expected: 2 new tests PASS + all prior tests still passing

- [ ] **Step 7: Commit**

```bash
git add services/worker/src/worker/app.py packages/signal_core/src/signal_core/modules/m03_extraction.py packages/signal_core/src/signal_core/modules/m04_identity.py services/worker/tests/test_worker.py
git commit -m "feat(py): worker — verdict.compute + index.chunk_embed jobs, dispatch wiring"
```

---

### Task 6: Web — Verdict panel (entity list with verdict badges)

**Files:**
- Create: `apps/web/src/app/(app)/workspace/[wsid]/verdicts/page.tsx`

**Interfaces:**
- Consumes: `public.verdict` table (via `supabase-js` with user JWT, RLS enforced — Viewer can read)
- Produces: a Next.js 15 App Router Server Component page at `/workspace/[wsid]/verdicts`

- [ ] **Step 1: Implement the page**

`apps/web/src/app/(app)/workspace/[wsid]/verdicts/page.tsx`:
- Server component — `createClient()` from `@/lib/supabase/server`
- Query: `supabase.from('verdict').select('id, entity_id, value, computed_at, brief_version_id, public_entity:entity(display_name)').eq('workspace_id', wsid).order('computed_at', { ascending: false })`
- Render table: columns Entity, Verdict badge, Computed at
- Verdict badge uses `data-verdict` attribute for CSS theming matching `verdict_value` enum values
- Empty state: "No verdicts yet — extraction and identity resolution must complete first."
- No edit controls (derived value)

- [ ] **Step 2: Type-check**

```bash
cd apps/web && pnpm tsc --noEmit
```
Expected: 0 errors

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/app/\(app\)/workspace/
git commit -m "feat(web): verdict panel — entity list with verdict badges"
```

---

### Task 7: pgTAP suite 10 — derivation RPCs + guard bypass

**Files:**
- Create: `supabase/tests/database/10_derivation_verdicts.test.sql`

**Interfaces:**
- Consumes: `app.upsert_verdict`, `app.upsert_memory_chunk` from Task 1

- [ ] **Step 1: Write the test file**

Assertions (plan(N)):
1. `app.upsert_verdict` exists (`has_function`)
2. `app.upsert_memory_chunk` exists (`has_function`)
3. `authenticated` CANNOT execute `app.upsert_verdict` — `throws_ok` with a service-role context showing function exists but calling as authenticated should fail or not be grantable
4. Direct INSERT into `public.verdict` without `app.writer = 'derivation'` raises exception 42501 — `throws_ok('INSERT INTO public.verdict ...', '42501')`
5. Direct INSERT into `public.memory_chunk` without derivation writer raises 42501
6. `app.upsert_verdict` with valid inputs succeeds — returns a UUID
7. Re-calling `app.upsert_verdict` with same (brief_version_id, entity_id) but different value → updates (ON CONFLICT DO UPDATE)
8. `app.upsert_verdict` with value='Confirmed' and empty claim_ids raises check violation — 23514
9. `app.upsert_verdict` with mismatched entity org raises 42501
10. `app.upsert_memory_chunk` with valid inputs succeeds
11. Re-calling `app.upsert_memory_chunk` with same (doc_type, doc_id, chunk_index, embedding_model) → updates
12. Verdict RLS: Viewer can SELECT from public.verdict — `is((SELECT count(*) FROM public.verdict WHERE workspace_id = ...), 1)`
13. `public.outbox` has a `verdict.changed` row after `app.upsert_verdict` call

Plan: 13 assertions.

Fixture follows suite 09 pattern: `app.create_organisation`, `app.create_workspace`, `app.create_brief`, `app.create_brief_version`, `app.create_entity`.

- [ ] **Step 2: Run via supabase test db (if available)**

```bash
supabase test db
```
Expected: suite 10 passes (or note it requires Supabase CLI + Docker in CI)

- [ ] **Step 3: Commit**

```bash
git add supabase/tests/database/10_derivation_verdicts.test.sql
git commit -m "test(db): pgTAP suite 10 — derivation RPCs + guard bypass"
```

---

## Post-implementation: update SDD ledger

After all 7 tasks complete, update `.superpowers/sdd/2026-11-09-phase1e-derivation-verdicts-search/progress.md` to mark Phase 1e COMPLETE.
