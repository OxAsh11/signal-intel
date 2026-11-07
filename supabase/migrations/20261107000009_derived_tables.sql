-- F7 Derived tables: never edited directly, recomputed from facts by the derivation engine (S5).
-- A transaction becomes a derivation writer with:  select set_config('app.writer', 'derivation', true);
-- Clients cannot write at all (no grants); this guard also stops the app's own code from
-- "fixing" a derived value by hand. Disagreement is recorded as feedback and flows through facts.

create function app.is_derivation_writer() returns boolean
language sql stable set search_path = '' as $$
  select coalesce(current_setting('app.writer', true), '') = 'derivation'
$$;

create function app.guard_derived() returns trigger
language plpgsql set search_path = '' as $$
begin
  if not app.is_derivation_writer() then
    raise exception '% is derived (F7): write it through the derivation engine', tg_table_name
      using errcode = '42501';
  end if;
  return coalesce(new, old);
end $$;

-- account.stage is derived from activity only (M11); the rest of the account row is not.
create function app.guard_account_stage() returns trigger
language plpgsql set search_path = '' as $$
begin
  if (tg_op = 'INSERT' and new.stage <> 'Untouched' or tg_op = 'UPDATE' and new.stage is distinct from old.stage)
     and not app.is_derivation_writer() then
    raise exception 'account.stage is derived from activity (M11): log the activity instead' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger account_stage_guard before insert or update on public.account
  for each row execute function app.guard_account_stage();

-- ── M5 verdict ──────────────────────────────────────────────────────────
create table public.verdict (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  brief_version_id uuid not null references public.brief_version (id),
  entity_id        uuid not null references public.entity (id),
  value            public.verdict_value not null,
  claim_ids        uuid[] not null default '{}',     -- the claims it rests on
  quotes           jsonb not null default '[]',
  inputs           jsonb not null default '{}',      -- versions and inputs, so it can be reproduced
  computed_at      timestamptz not null default now(),
  constraint one_verdict_per_version unique (brief_version_id, entity_id),
  -- M5: Confirmed needs at least one claim behind it.
  constraint confirmed_has_claims check (value <> 'Confirmed' or cardinality(claim_ids) > 0)
);
create index verdict_ws_value_idx on public.verdict (workspace_id, value);

-- ── M8 score ────────────────────────────────────────────────────────────
create table public.score (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  account_id       uuid not null references public.account (id),
  brief_version_id uuid not null references public.brief_version (id),
  fit              smallint check (fit between 0 and 100),
  evidence         smallint check (evidence between 0 and 100),
  scale            smallint check (scale between 0 and 100),
  timing           smallint check (timing between 0 and 100),
  reachability     smallint check (reachability between 0 and 100),
  priority         numeric(5, 2) check (priority between 0 and 100),
  readiness        smallint check (readiness between 0 and 100),   -- = reachability, kept separate (M8)
  band             public.band not null,
  gates            jsonb not null default '[]',
  explanation      jsonb not null default '{}',
  inputs           jsonb not null default '{}',
  computed_at      timestamptz not null default now(),
  constraint one_score_per_version unique (account_id, brief_version_id)
);
create index score_ws_band_idx on public.score (workspace_id, band, priority desc);

-- ── M8 label: derived lenses (radar, role, vertical, geography, persona coverage…) ──
create table public.label (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  account_id       uuid not null references public.account (id),
  brief_version_id uuid references public.brief_version (id),
  kind             text not null,
  value            text not null,
  inputs           jsonb not null default '{}',
  computed_at      timestamptz not null default now(),
  constraint one_label unique nulls not distinct (account_id, brief_version_id, kind, value)
);
create index label_ws_kind_idx on public.label (workspace_id, kind, value);

-- ── M11 account_stage_event ─────────────────────────────────────────────
create table public.account_stage_event (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  account_id   uuid not null references public.account (id),
  from_stage   public.stage,
  to_stage     public.stage not null,
  activity_id  uuid references public.activity (id),
  occurred_at  timestamptz not null,
  computed_at  timestamptz not null default now()
);
create index account_stage_event_account_idx on public.account_stage_event (account_id, occurred_at);

-- ── M15 account_card ────────────────────────────────────────────────────
create table public.account_card (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  account_id       uuid not null references public.account (id),
  brief_version_id uuid not null references public.brief_version (id),
  content          jsonb not null,          -- every line cites the claim or activity it rests on
  inputs           jsonb not null default '{}',
  computed_at      timestamptz not null default now(),
  constraint one_card_per_version unique (account_id, brief_version_id)
);

-- ── M15 / S9 memory_chunk ───────────────────────────────────────────────
create table public.memory_chunk (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid references public.workspace (id) on delete restrict,
  entity_id       uuid references public.entity (id),     -- follows the survivor on merge
  brief_id        uuid references public.brief (id),
  doc_type        text not null check (doc_type in ('source_record', 'research_doc', 'transcript')),
  doc_id          uuid not null,
  chunk_index     int not null check (chunk_index >= 0),
  content         text not null,
  content_tsv     tsvector generated always as (to_tsvector('simple', content)) stored,
  embedding       extensions.vector(1024),
  embedding_model text,
  scope           public.data_scope not null default 'tenant',
  occurred_at     timestamptz,
  computed_at     timestamptz not null default now(),
  constraint one_chunk unique (doc_type, doc_id, chunk_index, embedding_model)
);
create index memory_chunk_filter_idx on public.memory_chunk (org_id, workspace_id, entity_id);
create index memory_chunk_tsv_idx on public.memory_chunk using gin (content_tsv);
create index memory_chunk_embedding_idx on public.memory_chunk
  using hnsw (embedding extensions.vector_cosine_ops);

-- Guards on every derived table.
create trigger verdict_guard             before insert or update or delete on public.verdict             for each row execute function app.guard_derived();
create trigger score_guard               before insert or update or delete on public.score               for each row execute function app.guard_derived();
create trigger label_guard               before insert or update or delete on public.label               for each row execute function app.guard_derived();
create trigger account_stage_event_guard before insert or update or delete on public.account_stage_event for each row execute function app.guard_derived();
create trigger account_card_guard        before insert or update or delete on public.account_card        for each row execute function app.guard_derived();
create trigger memory_chunk_guard        before insert or update or delete on public.memory_chunk        for each row execute function app.guard_derived();

-- ── RLS ─────────────────────────────────────────────────────────────────
alter table public.verdict             enable row level security;
alter table public.score               enable row level security;
alter table public.label               enable row level security;
alter table public.account_stage_event enable row level security;
alter table public.account_card        enable row level security;
alter table public.memory_chunk        enable row level security;

create policy verdict_read             on public.verdict             for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy score_read               on public.score               for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy label_read               on public.label               for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy account_stage_event_read on public.account_stage_event for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy account_card_read        on public.account_card        for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy memory_chunk_read        on public.memory_chunk        for select to authenticated
  using (case when workspace_id is null then app.can_read_fact(org_id, scope)
              else app.has_workspace_role(workspace_id, 'Viewer') end);

grant select on public.verdict, public.score, public.label, public.account_stage_event,
  public.account_card, public.memory_chunk to authenticated;
