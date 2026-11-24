-- ── Account firmographic enrichment ─────────────────────────────────────────
--
-- Stores curated company profile data for accounts that have cleared all
-- scoring gates — i.e. accounts the workspace is actively considering for
-- outreach. The enrichment worker populates this table only when:
--
--   score.gates = '[]'          (no blocking gates fired)
--   score.band IN ('High','Medium')
--   verdict.value IN ('Confirmed','Likely')
--
-- One row per account; upserted by the worker on each enrichment run.
-- Never populated for accounts that are still unscored or gate-blocked.

create table public.account_firmographic (
  id               uuid primary key default gen_random_uuid(),
  account_id       uuid not null references public.account (id) on delete cascade,
  workspace_id     uuid not null references public.workspace (id) on delete cascade,

  -- Company identity
  industry         text,
  sub_industry     text,

  -- Size
  size_band        text,         -- 'SMB' | 'Mid-Market' | 'Enterprise'
  employee_count   integer check (employee_count > 0),
  revenue_range    text,         -- e.g. '$10M–$50M'

  -- Founding
  founded_year     integer check (founded_year > 1800 and founded_year <= 2100),

  -- Curated location (authoritative vs raw mention.raw_country)
  hq_city          text,
  hq_state         text,
  hq_country       text,

  -- Tech and buying signals
  tech_stack       text[] not null default '{}',

  -- Provenance
  enriched_at      timestamptz not null default now(),
  source           text not null default 'ai_research'
                     check (source in ('ai_research', 'manual', 'api')),

  constraint one_firmographic_per_account unique (account_id)
);

create index account_firmographic_ws_idx on public.account_firmographic (workspace_id);

-- ── RLS ──────────────────────────────────────────────────────────────────────

alter table public.account_firmographic enable row level security;

-- Workspace members (Viewer and above) may read firmographic data.
create policy firmographic_read
  on public.account_firmographic
  for select
  to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));

-- Editors may insert/update firmographic data (manual overrides from the UI).
create policy firmographic_write
  on public.account_firmographic
  for all
  to authenticated
  using  (app.has_workspace_role(workspace_id, 'Editor'))
  with check (app.has_workspace_role(workspace_id, 'Editor'));

grant select, insert, update, delete
  on public.account_firmographic
  to authenticated;

grant select, insert, update, delete
  on public.account_firmographic
  to service_role;
