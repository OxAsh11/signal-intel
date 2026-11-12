# Phase 1c — Connectors and Radars (S2, M2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the connector framework (S2) and Source/Radar Hub (M2) so a Brief can collect source records from CSV, websites, news, and job posts — with re-fetch deduplication, visible processing status, and health alerts for silent radars.

**Architecture:** A `radar` row binds a Brief to one connector type (CSV, website, news, job posts). The Python worker fetches via typed `Connector` adapters behind a `FetchGuard` (private-IP block + robots.txt). Every fetched item becomes a `source_record` row — a unique content hash prevents re-fetch duplicates. A periodic health-check cron opens an M17 health incident when a radar produces no records in its expected window.

**Tech Stack:** Postgres + pgTAP, Python 3.13 + psycopg3 + aiohttp, Procrastinate, Next.js 16 App Router + server actions.

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §6 (Phase 1 → S2, M2) · PRD S2, M2

## Global Constraints

- **S8 write path:** every mutation from a server action calls `supabase.rpc()` to a `SECURITY DEFINER` function in `app.*`; no direct table writes for `authenticated`.
- **No private IPs ever fetched.** `FetchGuard` must reject RFC 1918, loopback, link-local and all IPv6 private ranges before any HTTP call.
- **Re-fetch never duplicates.** `app.create_source_record` deduplicates on `(org_id, content_hash)` — unique constraint in migration 0007 already exists; RPC returns existing row id rather than raising.
- **Source records are immutable.** Only `extraction_status` and `status_detail` may be updated after insert (trigger already in place from migration 0007).
- **Kill switches before launch.** Every new job type must have a row in `job_type_config.enabled = true` in the migration before the worker registers it.
- **Event catalogue is closed.** `radar.fetch_queued` is added to `enums.json` + Postgres enum in Task 1; no other new events in this phase.
- **Minimal dependencies.** `aiohttp` (already in `pyproject.toml`); no new heavy parsing libs — use stdlib `html.parser` for website text, stdlib `xml.etree` for RSS.
- **Fake/stub paths for keyless testing.** A `FakeNewsConnector` and `FakeJobPostConnector` return deterministic fixture rows when `ANTHROPIC_API_KEY` is absent (same fake-adapter pattern as the AI gateway).
- **Next.js 16 patterns.** `params` is a Promise; use `PageProps<'/briefs/[id]/radars'>` etc. `useActionState` from `'react'`; `revalidatePath`/`redirect` from `'next/navigation'`.
- **Radar types (V1):** `csv_upload`, `website`, `news`, `job_post`. Stored as text; no Postgres enum.

## Review Focus

- **Duplicate source_record on hash collision:** two simultaneous fetch jobs hash the same item — one must win and both must succeed (ON CONFLICT / upsert return existing id, not raise).
- **Private-IP bypass:** a redirect from an allow-listed domain to `192.168.x.x` must be caught after every redirect, not just the original URL.
- **robots.txt false-positive:** a site serving an empty 404 for `/robots.txt` must be treated as "no rules", not "disallowed".
- **Empty GDELT result:** a news fetch for a niche query may return 0 articles — the radar stays healthy; zero records is not a health incident.
- **CSV upload with header-only or empty file:** must produce zero `source_record` rows and return a clear error, not silently succeed.

---

### Task 1: DB migration 0017 — Radar schema + event catalogue

**Files:**
- Create: `supabase/migrations/20261108000017_connectors_radars.sql`
- Modify: `packages/contracts/schema/enums.json` (add `radar.fetch_queued` to EventType)
- Modify: `packages/signal_core/src/signal_core/contracts/enums.py` (regenerate or hand-edit to match)

**Interfaces:**
- Produces:
  - `public.radar(id, org_id, workspace_id, brief_id, connector_type, name, config, cursor_state, status, last_fetched_at, last_error, record_count, yield_count, created_at)`
  - `public.radar_query(id, org_id, radar_id, query_text, language, source, edited_by_member_id, created_at)`
  - `app.create_radar(p_ws uuid, p_brief uuid, p_connector_type text, p_name text, p_config jsonb) returns uuid` — Editor+, fires outbox `radar.fetch_queued`
  - `app.remove_radar(p_radar uuid) returns void` — Editor+
  - `app.update_radar_cursor(p_radar uuid, p_cursor_state jsonb, p_fetched int, p_yield int) returns void` — service_role only
  - `app.upsert_radar_queries(p_radar uuid, p_queries jsonb) returns void` — service_role only; queries is `[{query_text, language, source}]`
  - `app.create_source_record(p_org uuid, p_workspace uuid, p_connector_id text, p_source_type text, p_url text, p_fetched_at timestamptz, p_content_hash text, p_payload_ref text, p_language text) returns uuid` — service_role; returns existing id on hash collision
  - `app.mark_radar_error(p_radar uuid, p_error text) returns void` — service_role
  - `EventType.RADAR_FETCH_QUEUED = "radar.fetch_queued"` added to closed catalogue

- [ ] **Step 1: Add `radar.fetch_queued` to enums.json EventType values array**

Add `"radar.fetch_queued"` at the end of the `EventType.values` array in `packages/contracts/schema/enums.json`.

- [ ] **Step 2: Update `signal_core/contracts/enums.py` EventType to match**

Add `RADAR_FETCH_QUEUED = "radar.fetch_queued"` to `EventType` in `packages/signal_core/src/signal_core/contracts/enums.py` (before the closing of the class).

- [ ] **Step 3: Write migration `20261108000017_connectors_radars.sql`**

```sql
-- Phase 1c: Connector framework (S2) + Source/Radar Hub (M2).

-- 1. Event catalogue: add radar.fetch_queued (reviewed addition, see plan).
alter type public.event_type add value 'radar.fetch_queued';

-- 2. radar table (M2: a configured source connector bound to a Brief).
create table public.radar (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid not null references public.workspace (id) on delete restrict,
  brief_id        uuid not null references public.brief (id) on delete restrict,
  connector_type  text not null check (connector_type in ('csv_upload', 'website', 'news', 'job_post')),
  name            text not null check (length(btrim(name)) between 1 and 200),
  config          jsonb not null default '{}',
  cursor_state    jsonb not null default '{}',
  status          text not null default 'active' check (status in ('active', 'paused', 'error', 'silent')),
  last_fetched_at timestamptz,
  last_error      text,
  record_count    int not null default 0,
  yield_count     int not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index radar_brief_idx    on public.radar (brief_id);
create index radar_ws_type_idx  on public.radar (workspace_id, connector_type);

-- 3. radar_query table.
create table public.radar_query (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null references public.organisation (id) on delete restrict,
  radar_id          uuid not null references public.radar (id) on delete cascade,
  query_text        text not null check (length(btrim(query_text)) between 1 and 500),
  language          text not null default 'en',
  source            text not null default 'hypothesis' check (source in ('hypothesis', 'icp', 'user')),
  edited_by_member  uuid references public.member (id),
  created_at        timestamptz not null default now()
);
create index radar_query_radar_idx on public.radar_query (radar_id);

-- 4. RLS.
alter table public.radar       enable row level security;
alter table public.radar_query enable row level security;

create policy radar_read       on public.radar       for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy radar_query_read on public.radar_query for select to authenticated using (app.is_org_member(org_id));

grant select on public.radar, public.radar_query to authenticated;

-- 5. Kill switches for new job types.
insert into public.job_type_config (job_type, enabled) values
  ('source.fetch_csv',       true),
  ('source.fetch_website',   true),
  ('source.fetch_news',      true),
  ('source.fetch_jobs',      true),
  ('radar.generate_queries', true),
  ('radar.health_check',     true);

-- 6. Write-path RPCs.
-- app.create_radar: Editor+ creates a radar and fires fetch via outbox.
create function app.create_radar(
  p_ws             uuid,
  p_brief          uuid,
  p_connector_type text,
  p_name           text,
  p_config         jsonb default '{}'
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_org    uuid;
  v_radar  uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  insert into public.radar (org_id, workspace_id, brief_id, connector_type, name, config)
  values (v_org, p_ws, p_brief, p_connector_type, p_name, p_config)
  returning id into v_radar;
  -- Outbox: triggers the fetch worker.
  insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  values ('radar.fetch_queued', v_org, p_ws, 'radar', v_radar,
          jsonb_build_object('radar_id', v_radar, 'connector_type', p_connector_type,
                             'org_id', v_org, 'workspace_id', p_ws, 'brief_id', p_brief));
  return v_radar;
end $$;

-- app.remove_radar: Editor+ soft-deletes by setting status=paused (keeps records).
create function app.remove_radar(p_radar uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare v_ws uuid;
begin
  select workspace_id into v_ws from public.radar where id = p_radar;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  update public.radar set status = 'paused', updated_at = now() where id = p_radar;
end $$;

-- app.update_radar_cursor: service_role only — called after a successful fetch.
create function app.update_radar_cursor(
  p_radar        uuid,
  p_cursor_state jsonb,
  p_fetched      int,
  p_yield        int
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.radar set
    cursor_state    = p_cursor_state,
    last_fetched_at = now(),
    record_count    = record_count + p_fetched,
    yield_count     = yield_count  + p_yield,
    status          = 'active',
    last_error      = null,
    updated_at      = now()
  where id = p_radar;
end $$;

-- app.upsert_radar_queries: service_role only — called by generate_queries job.
create function app.upsert_radar_queries(p_radar uuid, p_queries jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org  uuid;
  q      jsonb;
begin
  select org_id into v_org from public.radar where id = p_radar;
  delete from public.radar_query where radar_id = p_radar and source <> 'user';
  for q in select * from jsonb_array_elements(p_queries) loop
    insert into public.radar_query (org_id, radar_id, query_text, language, source)
    values (v_org, p_radar,
            q->>'query_text', coalesce(q->>'language', 'en'), coalesce(q->>'source', 'hypothesis'));
  end loop;
end $$;

-- app.create_source_record: service_role; deduplicates on (org_id, content_hash).
create function app.create_source_record(
  p_org          uuid,
  p_workspace    uuid,
  p_connector_id text,
  p_source_type  text,
  p_url          text,
  p_fetched_at   timestamptz,
  p_content_hash text,
  p_payload_ref  text,
  p_language     text
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.source_record
    (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, payload_ref, language)
  values (p_org, p_workspace, p_connector_id, p_source_type, p_url, p_fetched_at, p_content_hash, p_payload_ref, p_language)
  on conflict (org_id, content_hash) do nothing
  returning id into v_id;
  if v_id is null then
    -- Duplicate: return existing id.
    select id into v_id from public.source_record where org_id = p_org and content_hash = p_content_hash;
  end if;
  return v_id;
end $$;

-- app.mark_radar_error: service_role.
create function app.mark_radar_error(p_radar uuid, p_error text) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.radar set status = 'error', last_error = p_error, updated_at = now() where id = p_radar;
end $$;

-- app.mark_radar_silent: service_role — records health incident.
create function app.mark_radar_silent(p_radar uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare v_ws uuid; v_name text;
begin
  select workspace_id, name into v_ws, v_name from public.radar where id = p_radar;
  update public.radar set status = 'silent', updated_at = now() where id = p_radar;
  perform app.record_health('radar:' || p_radar::text, 'incident',
    jsonb_build_object('radar_id', p_radar, 'name', v_name), 'M17');
end $$;

-- Grant to service_role (the Python worker), not to authenticated.
grant execute on function
  app.create_radar(uuid, uuid, text, text, jsonb),
  app.remove_radar(uuid),
  app.update_radar_cursor(uuid, jsonb, int, int),
  app.upsert_radar_queries(uuid, jsonb),
  app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text),
  app.mark_radar_error(uuid, text),
  app.mark_radar_silent(uuid)
to postgres, service_role;

-- Public wrappers for client-callable RPCs (create_radar, remove_radar).
create function public.create_radar(p_ws uuid, p_brief uuid, p_connector_type text, p_name text, p_config jsonb default '{}')
  returns uuid language sql security definer set search_path = '' as $$
  select app.create_radar(p_ws, p_brief, p_connector_type, p_name, p_config)
$$;
create function public.remove_radar(p_radar uuid)
  returns void language sql security definer set search_path = '' as $$
  select app.remove_radar(p_radar)
$$;
grant execute on function public.create_radar(uuid, uuid, text, text, jsonb),
                          public.remove_radar(uuid) to authenticated;
```

- [ ] **Step 4: Run pgTAP (the existing suite must stay green)**

```bash
supabase test db
```

Expected: `ok 1..20` (suite 07 unchanged count), no failures.

- [ ] **Step 5: Commit**

```bash
git add packages/contracts/schema/enums.json \
        packages/signal_core/src/signal_core/contracts/enums.py \
        supabase/migrations/20261108000017_connectors_radars.sql
git commit -m "feat(db): radar/radar_query schema + S2 write-path RPCs (migration 0017)"
```

---

### Task 2: Python connector framework — FetchGuard + base + m02_radar

**Files:**
- Create: `packages/signal_core/src/signal_core/connectors/__init__.py`
- Create: `packages/signal_core/src/signal_core/connectors/base.py`
- Create: `packages/signal_core/src/signal_core/modules/m02_radar.py`
- Create: `packages/signal_core/tests/test_fetch_guard.py`

**Interfaces:**
- Consumes: `signal_core.contracts.enums.EventType.RADAR_FETCH_QUEUED` (Task 1)
- Produces:
  - `FetchGuard` class: `async def check(url: str) -> None` — raises `FetchBlocked` for private IPs; raises `FetchDisallowed` if robots.txt disallows the user agent.
  - `SourcePayload` dataclass: `url: str, fetched_at: datetime, content_hash: str, payload_ref: str | None, language: str, raw: bytes`
  - `Connector` Protocol: `source_type: str`; `async def fetch(config: dict, queries: list[str], cursor: dict, guard: FetchGuard, limit: int) -> tuple[list[SourcePayload], dict]` — returns (payloads, next_cursor).
  - `content_hash(raw: bytes) -> str` utility function (SHA-256 hex).
  - `save_payload(raw: bytes, storage_path_prefix: str) -> str | None` — writes to object storage if `STORAGE_URL` env set, else returns `None`.
  - `m02_radar.create_source_records(pool, org_id, workspace_id, connector_id, payloads) -> int` — calls `app.create_source_record` for each payload, returns new-record count.

- [ ] **Step 1: Write failing tests for FetchGuard**

`packages/signal_core/tests/test_fetch_guard.py`:

```python
import pytest
from signal_core.connectors.base import FetchGuard, FetchBlocked

@pytest.mark.asyncio
async def test_private_ip_blocked():
    g = FetchGuard()
    with pytest.raises(FetchBlocked):
        await g.check("http://192.168.1.1/page")

@pytest.mark.asyncio
async def test_loopback_blocked():
    g = FetchGuard()
    with pytest.raises(FetchBlocked):
        await g.check("http://127.0.0.1/page")

@pytest.mark.asyncio
async def test_public_ip_allowed(httpserver):
    # httpserver (pytest-localserver or aioresponses mock) responds with no robots rules.
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    g = FetchGuard()
    await g.check(httpserver.url_for("/page"))  # must not raise

@pytest.mark.asyncio
async def test_robots_disallowed(httpserver):
    httpserver.expect_request("/robots.txt").respond_with_data(
        "User-agent: *\nDisallow: /page\n", content_type="text/plain"
    )
    from signal_core.connectors.base import FetchDisallowed
    g = FetchGuard()
    with pytest.raises(FetchDisallowed):
        await g.check(httpserver.url_for("/page"))

@pytest.mark.asyncio
async def test_robots_404_treated_as_no_rules(httpserver):
    httpserver.expect_request("/robots.txt").respond_with_data("Not Found", status=404)
    g = FetchGuard()
    await g.check(httpserver.url_for("/page"))  # must not raise

def test_content_hash_stable():
    from signal_core.connectors.base import content_hash
    assert content_hash(b"hello") == content_hash(b"hello")
    assert content_hash(b"hello") != content_hash(b"world")
```

Run: `pytest packages/signal_core/tests/test_fetch_guard.py -v`
Expected: FAIL (module not found).

- [ ] **Step 2: Implement `signal_core/connectors/base.py`**

```python
class FetchBlocked(Exception): ...
class FetchDisallowed(Exception): ...

def content_hash(raw: bytes) -> str:
    # SHA-256 hex of raw bytes.
    ...

class FetchGuard:
    # Cache: robots rules per origin, TTL 1 hour.
    async def check(self, url: str) -> None:
        # 1. Parse URL → resolve host → getaddrinfo.
        # 2. Reject RFC 1918 (10.x, 172.16-31.x, 192.168.x), loopback (127.x, ::1),
        #    link-local (169.254.x, fe80::), and any other private/reserved range.
        # 3. Fetch /robots.txt with aiohttp (10s timeout); 404/5xx → no rules.
        # 4. Parse with urllib.robotparser; check User-agent='*' and the given path.
        ...

def save_payload(raw: bytes, prefix: str) -> str | None:
    # Write to object storage if STORAGE_URL is set; return path.
    # In tests (no STORAGE_URL), return None.
    ...
```

- [ ] **Step 3: Implement `signal_core/connectors/__init__.py`**

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

@dataclass
class SourcePayload:
    url: str
    fetched_at: datetime
    content_hash: str
    payload_ref: str | None
    language: str
    raw: bytes

class Connector(Protocol):
    source_type: str
    async def fetch(
        self, config: dict, queries: list[str], cursor: dict,
        guard: "FetchGuard", limit: int
    ) -> tuple[list[SourcePayload], dict]: ...
```

- [ ] **Step 4: Implement `signal_core/modules/m02_radar.py`**

```python
async def create_source_records(
    pool: AsyncConnectionPool,
    org_id: UUID,
    workspace_id: UUID,
    connector_id: str,
    payloads: list[SourcePayload],
) -> int:
    # For each payload, call app.create_source_record. Count new rows (non-null returns).
    ...
```

- [ ] **Step 5: Run tests**

```bash
pytest packages/signal_core/tests/test_fetch_guard.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full Python test suite**

```bash
pytest packages/signal_core/tests/ -v
```

Expected: existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add packages/signal_core/src/signal_core/connectors/ \
        packages/signal_core/src/signal_core/modules/m02_radar.py \
        packages/signal_core/tests/test_fetch_guard.py
git commit -m "feat(py): connector framework — FetchGuard, Connector protocol, m02_radar base"
```

---

### Task 3: Python CSV upload connector + web upload UI

**Files:**
- Create: `packages/signal_core/src/signal_core/connectors/csv_upload.py`
- Modify: `packages/signal_core/tests/test_connectors.py` (new file)
- Modify: `apps/web/src/app/actions/radars.ts` (new file)
- Create: `apps/web/src/app/(app)/briefs/[id]/radars/upload/page.tsx`

**Interfaces:**
- Consumes: `Connector` protocol, `content_hash`, `save_payload` (Task 2); `app.create_source_record` RPC (Task 1)
- Produces:
  - `CsvConnector` — `source_type = "csv"`. `fetch()` takes `config["storage_path"]` (a path in object storage to a previously uploaded CSV file); parses each data row (skip header); each row → `SourcePayload` with `url = "csv:{storage_path}:{row_index}"`, content_hash of raw row bytes, raw = row bytes as UTF-8.
  - `createRadar(formData)` server action in `radars.ts` — calls `supabase.rpc('create_radar', {p_ws, p_brief, p_connector_type, p_name})`
  - `removeRadar(formData)` server action — calls `supabase.rpc('remove_radar', {p_radar})`
  - CSV upload page: accepts a `radar_id` query param, uploads the CSV file to Supabase storage `uploads/{workspace_id}/{radar_id}/{filename}`, then queues the fetch by calling `triggerFetch` server action (or simply creating the radar stores the path in `config`).

- [ ] **Step 1: Write failing tests for CsvConnector**

`packages/signal_core/tests/test_connectors.py`:

```python
import io, csv
import pytest
from signal_core.connectors.csv_upload import CsvConnector
from signal_core.connectors.base import FetchGuard

@pytest.mark.asyncio
async def test_csv_produces_one_payload_per_data_row(tmp_path):
    rows = [["name", "domain"], ["Acme", "acme.com"], ["Beta Inc", "beta.io"]]
    path = tmp_path / "data.csv"
    path.write_text("\n".join(",".join(r) for r in rows))
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, cursor = await conn.fetch(
        config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000
    )
    assert len(payloads) == 2
    assert payloads[0].url.startswith("csv:")
    assert payloads[0].content_hash != payloads[1].content_hash

@pytest.mark.asyncio
async def test_csv_header_only_returns_zero_payloads(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("name,domain\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    assert payloads == []

@pytest.mark.asyncio
async def test_csv_deduplicates_identical_rows(tmp_path):
    path = tmp_path / "dupe.csv"
    path.write_text("name,domain\nAcme,acme.com\nAcme,acme.com\n")
    conn = CsvConnector()
    guard = FetchGuard()
    payloads, _ = await conn.fetch(config={"storage_path": str(path)}, queries=[], cursor={}, guard=guard, limit=1000)
    hashes = [p.content_hash for p in payloads]
    assert len(set(hashes)) == len(hashes)  # all unique hashes (deduped at upload layer)
```

Run: `pytest packages/signal_core/tests/test_connectors.py -v`
Expected: FAIL.

- [ ] **Step 2: Implement `signal_core/connectors/csv_upload.py`**

`source_type = "csv_upload"`. `fetch()` opens `config["storage_path"]`, reads with `csv.DictReader`, skips header, hashes each row's raw bytes (sorted keys → canonical repr), returns payloads with `cursor = {}` (CSV is fully read in one pass).

- [ ] **Step 3: Run tests**

```bash
pytest packages/signal_core/tests/test_connectors.py -v
```

Expected: all pass.

- [ ] **Step 4: Create `apps/web/src/app/actions/radars.ts`**

Server actions (use `'use server'`):
- `createRadar(prevState, formData)`: reads `brief` (hidden), `workspace` (hidden), `connector_type`, `name` from formData; calls `supabase.rpc('create_radar', {...})`; on success `revalidatePath('/briefs/[id]/radars')`.
- `removeRadar(prevState, formData)`: reads `id` from formData; calls `supabase.rpc('remove_radar', {...})`; revalidatePath.

- [ ] **Step 5: Create CSV upload page**

`apps/web/src/app/(app)/briefs/[id]/radars/upload/page.tsx` — simple `<form>` for `<input type="file" accept=".csv" />`. On submit (server action `uploadCsv`): upload to Supabase storage at `uploads/{workspaceId}/{radarId}/{filename}`, then call `supabase.rpc('update_radar_cursor', {p_radar, p_cursor_state: {storage_path: storagePath}, p_fetched: 0, p_yield: 0})` via service role, then redirect to `/briefs/[id]/radars`. Note: the actual parse job is triggered by `radar.fetch_queued` already fired when the radar was created; `update_radar_cursor` here just stores the file path.

- [ ] **Step 6: Type-check**

```bash
pnpm --filter web tsc --noEmit
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add packages/signal_core/src/signal_core/connectors/csv_upload.py \
        packages/signal_core/tests/test_connectors.py \
        apps/web/src/app/actions/radars.ts \
        apps/web/src/app/(app)/briefs/[id]/radars/upload/
git commit -m "feat(py,web): CSV upload connector + radar server actions"
```

---

### Task 4: Python connectors — website, news, job posts (batch)

**Files:**
- Create: `packages/signal_core/src/signal_core/connectors/website.py`
- Create: `packages/signal_core/src/signal_core/connectors/news.py`
- Create: `packages/signal_core/src/signal_core/connectors/job_posts.py`
- Modify: `packages/signal_core/tests/test_connectors.py`

**Interfaces:**
- Consumes: `Connector` protocol, `FetchGuard`, `content_hash`, `save_payload` (Task 2)
- Produces:
  - `WebsiteConnector` — `source_type = "website"`. `fetch()` takes `config["urls"] = [str]`; for each URL runs `guard.check(url)`, fetches with aiohttp (15s timeout), strips HTML tags via `html.parser` → plain text, returns one `SourcePayload` per URL with `language = "en"` default. Cursor: `{done: True}` after a single pass. Rate: 2 req/s max (1 concurrent).
  - `NewsConnector` — `source_type = "news"`. `fetch()` takes `queries: list[str]` (from radar_query rows); calls GDELT DOC 2.0 API (`https://api.gdeltproject.org/api/v2/doc/doc?query=…&mode=artlist&maxrecords=250&format=json`); also polls RSS feeds from `config.get("rss_feeds", [])` with stdlib `xml.etree`. Returns up to `limit` payloads. Cursor: `{last_datetime: "YYYYMMDDHHMMSS"}` — only items newer than last fetch. `FakeNewsConnector` returns 2 deterministic fixture payloads when `config.get("fake")`.
  - `JobPostConnector` — `source_type = "job_post"`. `fetch()` takes `config["boards"] = [{source: "greenhouse"|"lever"|"ashby", token: str}]`; hits respective public APIs (no auth required); returns one `SourcePayload` per job posting. Cursor: `{max_id_seen: ...}` per board. `FakeJobPostConnector` returns 2 deterministic fixture payloads when `config.get("fake")`.

- [ ] **Step 1: Write failing tests**

Add to `packages/signal_core/tests/test_connectors.py`:

```python
# Website connector
@pytest.mark.asyncio
async def test_website_strips_html(httpserver):
    httpserver.expect_request("/robots.txt").respond_with_data("", content_type="text/plain")
    httpserver.expect_request("/page").respond_with_data(
        "<html><body><h1>Hello</h1><p>World</p></body></html>", content_type="text/html"
    )
    from signal_core.connectors.website import WebsiteConnector
    guard = FetchGuard()
    conn = WebsiteConnector()
    payloads, _ = await conn.fetch(
        config={"urls": [httpserver.url_for("/page")]}, queries=[], cursor={}, guard=guard, limit=10
    )
    assert len(payloads) == 1
    assert b"<h1>" not in payloads[0].raw
    assert b"Hello" in payloads[0].raw

# News connector (fake mode)
@pytest.mark.asyncio
async def test_news_fake_returns_two_payloads():
    from signal_core.connectors.news import NewsConnector
    guard = FetchGuard()
    conn = NewsConnector()
    payloads, _ = await conn.fetch(
        config={"fake": True}, queries=["CAD software hiring"], cursor={}, guard=guard, limit=10
    )
    assert len(payloads) == 2

# Job posts connector (fake mode)
@pytest.mark.asyncio
async def test_jobs_fake_returns_two_payloads():
    from signal_core.connectors.job_posts import JobPostConnector
    guard = FetchGuard()
    conn = JobPostConnector()
    payloads, _ = await conn.fetch(
        config={"fake": True}, queries=[], cursor={}, guard=guard, limit=10
    )
    assert len(payloads) == 2
```

Run: `pytest packages/signal_core/tests/test_connectors.py -v`
Expected: FAIL (new tests).

- [ ] **Step 2: Implement `website.py`**

Use `aiohttp.ClientSession` (1-connection semaphore, 15s timeout, follow redirects, re-check guard after each redirect). Strip HTML with `html.parser` HTMLParser subclass that collects only text nodes from `<body>`. content_hash of raw HTML bytes (not stripped text, to detect page changes). `source_type = "website"`.

- [ ] **Step 3: Implement `news.py`**

GDELT DOC 2.0 API call per query string; parse `articles[].url` and `articles[].title`; fetch article text via WebsiteConnector; cursor is `{last_datetime}`. RSS: parse `<item>/<entry>` elements, extract `<link>` and `<title>`, fetch body via website connector. `FakeNewsConnector` returns 2 payloads with deterministic hashes. Register as `source_type = "news"`.

- [ ] **Step 4: Implement `job_posts.py`**

Greenhouse: `GET https://api.greenhouse.io/v1/boards/{token}/jobs` → `jobs[]`; each job → one payload with `url = job.absolute_url`, raw = JSON of job. Lever: `GET https://api.lever.co/v0/postings/{company}` → array; each → payload. Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{name}` → `jobPostings[]`. Cursor per board: `{max_updated_at}`. FakeJobPostConnector same pattern. `source_type = "job_post"`.

- [ ] **Step 5: Run tests**

```bash
pytest packages/signal_core/tests/test_connectors.py -v
```

Expected: all pass including new ones.

- [ ] **Step 6: Run full suite**

```bash
pytest packages/signal_core/tests/ -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add packages/signal_core/src/signal_core/connectors/
git commit -m "feat(py): website, news, job posts connector adapters"
```

---

### Task 5: Python radar query generation

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/prompts/radar_queries/v1.md`
- Modify: `packages/signal_core/src/signal_core/ai_gateway/outputs.py`
- Modify: `packages/signal_core/src/signal_core/modules/m02_radar.py`
- Modify: `packages/signal_core/tests/test_connectors.py`

**Interfaces:**
- Consumes: `AiGateway.run()`, `Capability.WRITE`, `load_prompt`, `BriefConfig` shape from `apps/web/src/lib/briefs.ts` (identical structure in Python as a dict)
- Produces:
  - `RadarQuery` Pydantic model: `query_text: str`, `language: str = "en"`, `source: Literal["hypothesis","icp"]`
  - `RadarQuerySet` Pydantic model: `queries: list[RadarQuery]` (max 20)
  - `m02_radar.generate_queries(brief_config: dict, gateway: AiGateway, ctx: CallContext) -> RadarQuerySet`
  - `m02_radar.save_queries(pool, radar_id, queries: RadarQuerySet) -> None` — calls `app.upsert_radar_queries`

- [ ] **Step 1: Write failing test**

Add to `packages/signal_core/tests/test_connectors.py`:

```python
@pytest.mark.asyncio
async def test_generate_queries_returns_radar_query_set(fake_gateway, fake_pool):
    from signal_core.modules.m02_radar import generate_queries
    from signal_core.ai_gateway.gateway import CallContext
    from uuid import uuid4
    ctx = CallContext(org_id=uuid4(), workspace_id=uuid4(), brief_id=uuid4(),
                      job_id=None, job_type="radar.generate_queries", job_args={})
    config = {"hypothesis": "firms using CAD software and hiring engineers",
              "icp": {"geographies": ["USA", "Canada"]}}
    result = await generate_queries(config, fake_gateway, ctx)
    assert len(result.queries) >= 1
    assert all(q.query_text for q in result.queries)
```

Run: `pytest packages/signal_core/tests/test_connectors.py::test_generate_queries_returns_radar_query_set -v`
Expected: FAIL.

- [ ] **Step 2: Write prompt `radar_queries/v1.md`**

Format: front matter `capability: write`, `---`, system prompt instructing the AI to generate 5–15 search queries from a Brief's hypothesis, ICP geographies and verticals, `--- user ---`, user template with `{hypothesis}`, `{icp_geographies}`, `{icp_verticals}`. Output: JSON array of `{"query_text": "...", "language": "en", "source": "hypothesis|icp"}`.

- [ ] **Step 3: Add `RadarQuerySet` to `outputs.py`**

```python
class RadarQuery(BaseModel):
    query_text: str
    language: str = "en"
    source: Literal["hypothesis", "icp"] = "hypothesis"

class RadarQuerySet(BaseModel):
    queries: list[RadarQuery] = Field(max_length=20)
```

- [ ] **Step 4: Implement `m02_radar.generate_queries` and `save_queries`**

`generate_queries`: loads prompt `radar_queries/1`, renders with brief_config fields, calls `gateway.run(Capability.WRITE, prompt, vars, RadarQuerySet, ctx)`, returns result.value.

`save_queries`: calls `pool.connection()`, executes `select app.upsert_radar_queries(%s, %s)` with radar_id and `json.dumps([q.model_dump() for q in queries.queries])`.

- [ ] **Step 5: Run test**

```bash
pytest packages/signal_core/tests/test_connectors.py::test_generate_queries_returns_radar_query_set -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add packages/signal_core/src/signal_core/ai_gateway/prompts/radar_queries/ \
        packages/signal_core/src/signal_core/ai_gateway/outputs.py \
        packages/signal_core/src/signal_core/modules/m02_radar.py \
        packages/signal_core/tests/test_connectors.py
git commit -m "feat(py): radar query generation (AI prompt + RadarQuerySet model)"
```

---

### Task 6: Worker — job registration + dispatch routing + health cron

**Files:**
- Modify: `services/worker/src/worker/app.py`
- Modify: `packages/signal_core/tests/test_connectors.py` (integration test for dispatch)

**Interfaces:**
- Consumes: all connectors (Tasks 3–4), m02_radar functions (Tasks 2 + 5), EventType.RADAR_FETCH_QUEUED (Task 1)
- Produces: 6 new Procrastinate job types registered in `app.py`:
  - `source.fetch_csv` — CsvConnector fetch → create_source_records → update_radar_cursor
  - `source.fetch_website` — WebsiteConnector fetch → create_source_records → update_radar_cursor
  - `source.fetch_news` — NewsConnector fetch → create_source_records → update_radar_cursor
  - `source.fetch_jobs` — JobPostConnector fetch → create_source_records → update_radar_cursor
  - `radar.generate_queries` — load radar + brief_config from DB → generate_queries → save_queries
  - `radar.health_check` — periodic cron every hour; find radars not fetched in 25h with status=active; call mark_radar_silent for each
  - `dispatch()` updated: `radar.fetch_queued` → defer appropriate `source.fetch_{type}` job; `brief.version_published` → for each radar in Brief, defer `radar.generate_queries`

- [ ] **Step 1: Write failing integration test for dispatch routing**

```python
# test that dispatch("radar.fetch_queued", connector_type="news") defers source.fetch_news
def test_dispatch_routes_radar_fetch_queued_to_correct_job(mock_app):
    # Use procrastinate's TestConnector to record deferred tasks.
    # Assert source.fetch_news is deferred with radar_id.
    ...
```

Run: `pytest services/worker/tests/test_worker.py -v` (or add to it).
Expected: FAIL.

- [ ] **Step 2: Add 6 job types to `worker/app.py`**

Pattern follows existing `brief_intake` and `dispatch` jobs. For each `source.fetch_{type}`:
```python
@job(app, type="source.fetch_news", kill_switch=kill_switch, queue="fetch", retry=3)
async def fetch_news(radar_id: str, org_id: str, workspace_id: str, brief_id: str, **_) -> dict:
    radar = await _load_radar(pool, UUID(radar_id))
    queries = await _load_radar_queries(pool, UUID(radar_id))
    conn = NewsConnector()
    guard = FetchGuard()
    payloads, next_cursor = await conn.fetch(
        config=radar["config"], queries=[q["query_text"] for q in queries],
        cursor=radar["cursor_state"], guard=guard, limit=500
    )
    created = await create_source_records(pool, UUID(org_id), UUID(workspace_id),
                                          f"radar:{radar_id}", payloads)
    await _update_cursor(pool, UUID(radar_id), next_cursor, len(payloads), created)
    return {"fetched": len(payloads), "created": created}
```

Add helper `_load_radar`, `_load_radar_queries`, `_update_cursor` (private async fns that use the pool directly).

- [ ] **Step 3: Update `dispatch()` routing**

```python
elif evt == "radar.fetch_queued":
    ctype = payload.get("connector_type", "")
    job_name = f"source.fetch_{ctype.replace('_upload', '')}"  # csv_upload → fetch_csv
    if job_name in app.tasks:
        await app.configure_task(name=job_name, queueing_lock=payload["radar_id"]).defer_async(
            radar_id=payload["radar_id"],
            org_id=payload["org_id"],
            workspace_id=payload["workspace_id"],
            brief_id=payload["brief_id"],
        )
elif evt == "brief.version_published":
    # For each active radar in this brief, re-queue query generation.
    brief_id = payload.get("brief_id")
    if brief_id:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select id, org_id, workspace_id from public.radar where brief_id = %s and status = 'active'",
                (brief_id,)
            )
            radars = await cur.fetchall()
        for r in radars:
            with contextlib.suppress(AlreadyEnqueued):
                await app.tasks["radar.generate_queries"].configure(
                    queueing_lock=f"gq:{r[0]}"
                ).defer_async(radar_id=str(r[0]), org_id=str(r[1]), workspace_id=str(r[2]))
```

- [ ] **Step 4: Add `radar.health_check` cron**

```python
RADAR_HEALTH_CRON = "30 * * * *"  # every hour at :30

@app.periodic(cron=RADAR_HEALTH_CRON, periodic_id="radar.health_check")
@job(app, type="radar.health_check", kill_switch=kill_switch, queue="system", retry=0)
async def radar_health_check(timestamp: int) -> dict:
    async with pool.connection() as conn:
        cur = await conn.execute(
            """select id from public.radar
               where status = 'active'
               and (last_fetched_at is null or last_fetched_at < now() - interval '25 hours')"""
        )
        stale = [r[0] for r in await cur.fetchall()]
    for radar_id in stale:
        async with pool.connection() as conn:
            await conn.execute("select app.mark_radar_silent(%s)", (radar_id,))
        log.warning("radar.silent", radar_id=str(radar_id))
    return {"silent": len(stale)}
```

- [ ] **Step 5: Run tests**

```bash
pytest services/worker/tests/ -v
pytest packages/signal_core/tests/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add services/worker/src/worker/app.py \
        packages/signal_core/tests/test_connectors.py
git commit -m "feat(worker): source fetch jobs, radar query gen, dispatch routing, health cron"
```

---

### Task 7: Web — Discovery screen (radars management)

**Files:**
- Create: `apps/web/src/app/(app)/briefs/[id]/radars/page.tsx`
- Create: `apps/web/src/app/(app)/briefs/[id]/radars/radar-add-form.tsx`
- Modify: `apps/web/src/app/(app)/briefs/[id]/page.tsx` (add "Manage radars →" link)

**Interfaces:**
- Consumes: `createRadar`, `removeRadar` server actions (Task 3); `public.radar` and `public.radar_query` via `supabase.from('radar')` (Task 1)
- Produces:
  - `/briefs/[id]/radars` server page — lists all radars for the Brief (sorted by created_at); each row shows: connector type, name, status badge, `record_count`, `yield_count`, last fetched (or "never"), count of queries; "Remove" button (uses `removeRadar`). For CSV radars, shows "Upload CSV →" link to `/radars/upload?radar_id=...`.
  - Radar add form (client component): `connector_type` select (`csv_upload | website | news | job_post`), `name` text; uses `useActionState(createRadar)`.
  - Brief detail page gets a "Radars →" link next to "Version history →".

- [ ] **Step 1: Create `radars/page.tsx`**

Server component. Fetches:
```typescript
const { data: radars } = await supabase
  .from('radar')
  .select('id, connector_type, name, status, record_count, yield_count, last_fetched_at, last_error, radar_query(count)')
  .eq('brief_id', id)
  .neq('status', 'paused')
  .order('created_at', { ascending: true });
```

Renders a table (or list) with one row per radar + status badges (active = green, error = red, silent = orange). `canEdit` check for showing add/remove controls.

- [ ] **Step 2: Create `radar-add-form.tsx`**

`'use client'` component. `useActionState(createRadar, {})`. Includes hidden inputs for `brief` and `workspace` (passed as props). `connector_type` select with 4 options + human labels:
- `csv_upload` → "CSV upload"
- `website` → "Company websites"
- `news` → "News & publications"
- `job_post` → "Job postings"

- [ ] **Step 3: Add "Radars →" link in `briefs/[id]/page.tsx`**

After the existing `<Link href={/briefs/${id}/versions} ...>Version history →</Link>`, add:
```tsx
<Link href={`/briefs/${id}/radars`} className="text-sm text-ink-muted hover:text-ink">
  Radars →
</Link>
```

- [ ] **Step 4: Type-check**

```bash
pnpm --filter web tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/app/(app)/briefs/[id]/radars/ \
        apps/web/src/app/(app)/briefs/[id]/page.tsx
git commit -m "feat(web): Discovery screen — radar list, add/remove, status, CSV upload link"
```

---

### Task 8: pgTAP tests — connectors and radars

**Files:**
- Create: `supabase/tests/database/08_connectors_radars.test.sql`

**Interfaces:**
- Consumes: `app.create_radar`, `app.create_source_record`, `app.update_radar_cursor`, `app.mark_radar_silent` (Task 1); `public.radar`, `public.radar_query` RLS (Task 1)

- [ ] **Step 1: Write tests**

```sql
begin;
create extension if not exists pgtap with schema extensions;
select plan(10);

-- Fixture (reuse org/ws/member pattern from 07_brief_studio.test.sql).
-- ...set up org, workspace, member J (Editor), member K (Viewer), brief B...

-- 1. create_radar: Editor can create.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"...J...","role":"authenticated"}', true);
select ok(
  (select public.create_radar(ws_id, brief_id, 'news', 'Tech News', '{}')) is not null,
  'editor can create radar'
);

-- 2. create_radar: Viewer cannot create.
select set_config('request.jwt.claims', '{"sub":"...K...","role":"authenticated"}', true);
select throws_ok(
  format($$ select public.create_radar(%L, %L, 'news', 'X', '{}') $$, ws_id, brief_id),
  '42501', null, 'viewer cannot create radar'
);

-- 3. Radar outbox event fired.
reset role;
select is(
  (select count(*) from public.outbox where event = 'radar.fetch_queued'),
  1::bigint,
  'create_radar fires radar.fetch_queued outbox event'
);

-- 4. Source record deduplication.
select is(
  app.create_source_record(org_id, ws_id, 'radar:test', 'news', 'https://ex.com/a',
                           now(), 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
                           null, 'en'),
  app.create_source_record(org_id, ws_id, 'radar:test', 'news', 'https://ex.com/a',
                           now(), 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
                           null, 'en'),
  'duplicate hash returns same source_record id'
);

-- 5. Cross-workspace isolation: Viewer in org B cannot read org A radars.
-- ...

-- 6. update_radar_cursor blocked for authenticated role.
set local role authenticated;
select throws_ok(
  $$ select app.update_radar_cursor(radar_id, '{}', 0, 0) $$,
  null, null, 'authenticated role cannot call update_radar_cursor directly'
);

-- 7–10: more coverage (upsert_radar_queries, mark_radar_silent health incident, remove_radar, RLS select).

select * from finish();
rollback;
```

- [ ] **Step 2: Run pgTAP suite**

```bash
supabase test db
```

Expected: `ok 1..30` (suite 07 at 20, suite 08 at 10), no failures.

- [ ] **Step 3: Commit**

```bash
git add supabase/tests/database/08_connectors_radars.test.sql
git commit -m "test(db): pgTAP suite 08 — connector/radar dedup, RLS, dispatch, health"
```

---

## Self-Review

**Spec coverage check:**

| PRD requirement | Covered by task |
|---|---|
| S2: connector kind, auth method, rate limits, cursor, field mapping, health | Tasks 1 (schema), 2–4 (connectors), 6 (health cron) |
| S2: source connectors write only to `source_record` | Tasks 2–4: connectors call `create_source_records` only |
| S2: re-fetch never duplicates | Task 1 (unique constraint + RPC); Task 8 (pgTAP test) |
| S2: allowlisted egress, robots, no private IPs | Task 2 (FetchGuard) |
| S2: every connector instance reports health to M17 | Task 6 (health_check cron → mark_radar_silent → record_health) |
| M2: radar owns `radar`, `radar_query`, `source_record` | Task 1 (schema) |
| M2: query generation from Brief hypothesis + ICP + geographies | Task 5 |
| M2: pre-filter (lexicon check before AI extraction) | Deferred to 1d (extraction module owns the filter step) |
| M2: freshness cursor, only newer items processed | Tasks 3–4: cursors per connector |
| M2: re-fetch never creates duplicate record | Task 1 (content_hash unique), Task 8 (pgTAP) |
| M2: every record has visible processing status | Task 7 (Discovery screen shows extraction_status counts) |
| M2: radar with zero volume → health alert | Task 6 (radar.health_check cron, 25h window) |
| M2: yield tracking | Task 1 (`yield_count` column), Task 6 (increment in fetch jobs) |
| M2: V1 radar types: news, job_post, website, csv_upload | Tasks 3–4 |

**Pre-filter note:** The M2 spec says "a cheap lexicon check runs before AI extraction to save cost." Extraction (M3) has not been built yet; the pre-filter logically belongs between fetching (M2) and extraction (M3). This plan completes the fetch layer; the pre-filter step will be added at the start of Task 1d alongside M3. Recorded as a decision in the overview plan.

**Deferred from this phase:**
- Targeted search (M5 requests a narrow fetch when verdict is Unverified) — M5 lands in 1e.
- User-editable radar queries in the web UI — the data model and RPC are in place (`source = 'user'` rows survive `upsert_radar_queries`); the edit form lands in 1d or 1e when the Discovery screen gets a full refinement pass.
- Settings → Integrations for bring-your-own connector keys — M13, Phase 3.
