-- F2 Identity, F3 Provenance and facts, F4 Relationships.
-- Data moves one way: observed (source_record) → resolved (mention → entity) → facts (claim, relationship).

-- ── Platform switches (F1: turning shared facts on must not need a migration) ──
create table public.platform_setting (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
alter table public.platform_setting enable row level security;   -- service role only
insert into public.platform_setting (key, value) values ('shared_facts_enabled', 'false');

create function app.shared_facts_enabled() returns boolean
language sql stable security definer set search_path = '' as $$
  select coalesce((select (value)::boolean from public.platform_setting where key = 'shared_facts_enabled'), false)
$$;

-- Read rule for organisation-level fact rows.
create function app.can_read_fact(p_org uuid, p_scope public.data_scope) returns boolean
language sql stable security definer set search_path = '' as $$
  select app.is_org_member(p_org) or (p_scope = 'shared' and app.shared_facts_enabled())
$$;

grant execute on function app.shared_facts_enabled(), app.can_read_fact(uuid, public.data_scope)
  to authenticated, service_role;

-- Generic immutability guard: name the columns that may still change in TG_ARGV.
create function app.forbid_update_except() returns trigger
language plpgsql set search_path = '' as $$
declare
  allowed text[] := tg_argv;
  col text;
begin
  for col in select jsonb_object_keys(to_jsonb(new)) loop
    if not (col = any (allowed))
       and (to_jsonb(new) -> col) is distinct from (to_jsonb(old) -> col) then
      raise exception '%.% is immutable', tg_table_name, col using errcode = '42501';
    end if;
  end loop;
  return new;
end $$;

-- ── F2 entity ──────────────────────────────────────────────────────────────
create table public.entity (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  entity_type  public.entity_type not null,
  display_name text not null check (length(btrim(display_name)) between 1 and 500),
  scope        public.data_scope not null default 'tenant',
  status       text not null default 'active' check (status in ('active', 'merged', 'retired')),
  merged_into  uuid references public.entity (id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint person_is_tenant check (entity_type <> 'person' or scope = 'tenant'),           -- F1 rule
  constraint merged_has_target check ((status = 'merged') = (merged_into is not null)),
  constraint not_merged_into_self check (merged_into is null or merged_into <> id)
);
create index entity_org_type_idx on public.entity (org_id, entity_type) where status = 'active';
create index entity_name_trgm_idx on public.entity using gin (lower(display_name) extensions.gin_trgm_ops);
create index entity_merged_into_idx on public.entity (merged_into) where merged_into is not null;
-- F2: entities are never deleted; merges and retirements are status changes.
create trigger entity_no_delete before delete on public.entity
  for each row execute function app.forbid_mutation();
-- Type and owner never change; a "different type" is a different entity.
create trigger entity_fixed_columns before update on public.entity
  for each row execute function app.forbid_update_except('display_name', 'scope', 'status', 'merged_into', 'updated_at');

-- Every reference resolves through the canonical entity (follows merged_into).
create function app.canonical_entity(p_id uuid) returns uuid
language sql stable set search_path = '' as $$
  with recursive chain(id, merged_into, depth) as (
    select e.id, e.merged_into, 0 from public.entity e where e.id = p_id
    union all
    select e.id, e.merged_into, c.depth + 1
    from public.entity e join chain c on e.id = c.merged_into
    where c.depth < 32
  )
  select id from chain where merged_into is null limit 1
$$;
grant execute on function app.canonical_entity(uuid) to authenticated, service_role;

create view public.entity_current with (security_invoker = true) as
  select e.id, app.canonical_entity(e.id) as canonical_id, e.org_id, e.entity_type, e.status
  from public.entity e;

-- ── F3 source_record: one fetched item, stored as fetched ─────────────────
create table public.source_record (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null references public.organisation (id) on delete restrict,
  workspace_id      uuid references public.workspace (id) on delete restrict,
  connector_id      text not null,                 -- S2 connector instance (integration table lands in M13)
  source_type       text not null,                 -- news, job_post, registry, tender, event, directory, social, website, csv, crm, transcript, upload, synthetic
  url               text,
  fetched_at        timestamptz not null,
  content_hash      text not null,                 -- dedupe key (M2: re-fetching never duplicates)
  payload_ref       text,                          -- object storage path; payloads never live in Postgres
  scope             public.data_scope not null default 'tenant',
  language          text,
  extraction_status public.extraction_status not null default 'pending',
  status_detail     text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint source_record_dedupe unique (org_id, content_hash)
);
create index source_record_status_idx on public.source_record (org_id, extraction_status, created_at);
create trigger source_record_immutable before update on public.source_record
  for each row execute function app.forbid_update_except('extraction_status', 'status_detail', 'updated_at');
create trigger source_record_no_delete before delete on public.source_record
  for each row execute function app.forbid_mutation();

-- ── F2 entity_alias: every handle that points at an entity ──────────────
create table public.entity_alias (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  entity_id        uuid not null references public.entity (id),
  alias_type       public.alias_type not null,
  value_normalised text not null check (length(value_normalised) between 1 and 1000),
  confidence       smallint not null check (confidence between 0 and 100),
  source_record_id uuid not null references public.source_record (id),   -- every claim has a receipt
  scope            public.data_scope not null default 'tenant',
  created_at       timestamptz not null default now(),
  -- Domains are not unique keys (parents share them with subsidiaries): unique per entity only.
  constraint alias_unique_per_entity unique (org_id, alias_type, value_normalised, entity_id)
);
create index entity_alias_lookup_idx on public.entity_alias (org_id, alias_type, value_normalised);
create index entity_alias_trgm_idx on public.entity_alias using gin (value_normalised extensions.gin_trgm_ops)
  where alias_type = 'name_country';
create index entity_alias_entity_idx on public.entity_alias (entity_id);

-- ── F2 account: a workspace's working record on a company entity ─────────
create table public.account (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid not null references public.workspace (id) on delete restrict,
  entity_id       uuid not null references public.entity (id),
  owner_member_id uuid references public.member (id),
  team_id         uuid references public.team (id),
  stage           public.stage not null default 'Untouched',   -- derived (M11): guarded in migration 0009
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint one_account_per_entity unique (workspace_id, entity_id)
);
create index account_ws_stage_idx on public.account (workspace_id, stage);

-- ── F3 mention: a reference inside a source record, before identity is decided ──
create table public.mention (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  source_record_id uuid not null references public.source_record (id),
  entity_type      public.entity_type not null,
  raw_name         text,
  raw_domain       text,
  raw_country      text,
  raw_attributes   jsonb not null default '{}',     -- registry id, profile URL, title… as found
  entity_id        uuid references public.entity (id),   -- null until resolved (M4)
  match_method     text,
  match_confidence smallint check (match_confidence between 0 and 100),
  created_at       timestamptz not null default now(),
  constraint resolved_has_method check (entity_id is null or match_method is not null)
);
create index mention_unresolved_idx on public.mention (org_id, created_at) where entity_id is null;
create index mention_source_idx on public.mention (source_record_id);

-- ── F3 predicate registry ─────────────────────────────────────────────────
create table public.predicate (
  id                   uuid primary key default gen_random_uuid(),
  key                  text not null check (key ~ '^[a-z][a-z0-9_.]{1,79}$'),
  org_id               uuid references public.organisation (id) on delete restrict,
  workspace_id         uuid references public.workspace (id) on delete restrict,
  label                text not null,
  value_type           text not null check (value_type in ('boolean', 'number', 'enum', 'text', 'date', 'entity_ref')),
  allowed_values       jsonb,                      -- enum: JSON array of strings
  unit                 text,
  decay_half_life_days int check (decay_half_life_days > 0),
  extraction_hint      text,
  created_at           timestamptz not null default now(),
  constraint predicate_scope check ((workspace_id is null) = (org_id is null)),   -- global, or workspace-owned
  constraint enum_has_values check (value_type <> 'enum' or jsonb_typeof(allowed_values) = 'array'),
  constraint one_key_per_scope unique nulls not distinct (workspace_id, key)
);

-- ── F5 extractor_version: prompt, model and parser that produced mentions and claims ──
create table public.extractor_version (
  id             uuid primary key default gen_random_uuid(),
  capability     text not null,                    -- extract, judge, resolve, research, canary…
  prompt_version text not null,
  model          text not null,
  parser_version text not null,
  prompt_hash    text,
  created_at     timestamptz not null default now(),
  unique (capability, prompt_version, model, parser_version)
);
create trigger extractor_version_immutable before update or delete on public.extractor_version
  for each row execute function app.forbid_mutation();

-- ── F3 claim: one sourced fact about an entity ───────────────────────────
create table public.claim (
  id                   uuid primary key default gen_random_uuid(),
  org_id               uuid not null references public.organisation (id) on delete restrict,
  entity_id            uuid not null references public.entity (id),
  predicate_id         uuid not null references public.predicate (id),
  value                jsonb not null,
  quote                text,                       -- supporting quote (translated when needed)
  quote_original       text,                       -- original-language quote (M3)
  quote_language       text,
  source_record_id     uuid not null references public.source_record (id),
  observed_at          timestamptz not null,
  extracted_at         timestamptz not null default now(),
  extractor_version_id uuid not null references public.extractor_version (id),
  confidence           smallint not null check (confidence between 0 and 100),
  expires_at           timestamptz,
  status               public.claim_status not null default 'active',
  superseded_by        uuid references public.claim (id),
  scope                public.data_scope not null default 'tenant',
  created_at           timestamptz not null default now(),
  constraint retracted_or_superseded check (status <> 'superseded' or superseded_by is not null)
);
create index claim_entity_pred_idx on public.claim (entity_id, predicate_id) where status = 'active';
create index claim_source_idx on public.claim (source_record_id);
create trigger claim_immutable before update on public.claim
  for each row execute function app.forbid_update_except('status', 'superseded_by');
create trigger claim_no_delete before delete on public.claim
  for each row execute function app.forbid_mutation();

-- F3: typed predicates. An extractor cannot write a value outside the registry.
create function app.validate_claim() returns trigger
language plpgsql set search_path = '' as $$
declare
  p public.predicate%rowtype;
  e_type public.entity_type;
  v jsonb := new.value;
begin
  select * into p from public.predicate where id = new.predicate_id;
  if p.workspace_id is not null and p.org_id <> new.org_id then
    raise exception 'predicate % belongs to another organisation', p.key using errcode = '23514';
  end if;

  if p.value_type = 'boolean' and jsonb_typeof(v) <> 'boolean'
     or p.value_type = 'number' and jsonb_typeof(v) <> 'number'
     or p.value_type in ('text', 'date', 'enum', 'entity_ref') and jsonb_typeof(v) <> 'string' then
    raise exception 'claim value % does not match predicate % (%)', v, p.key, p.value_type using errcode = '23514';
  end if;
  if p.value_type = 'enum' and not (p.allowed_values ? (v #>> '{}')) then
    raise exception 'value % is not allowed for predicate %', v, p.key using errcode = '23514';
  end if;
  if p.value_type = 'date' then
    begin
      perform (v #>> '{}')::date;
    exception when others then
      raise exception 'value % is not a date (predicate %)', v, p.key using errcode = '23514';
    end;
  end if;
  if p.value_type = 'entity_ref' and not exists (
       select 1 from public.entity x where x.id::text = (v #>> '{}') and x.org_id = new.org_id) then
    raise exception 'value % is not an entity of this organisation (predicate %)', v, p.key using errcode = '23514';
  end if;

  select entity_type into e_type from public.entity where id = new.entity_id;
  if e_type = 'person' and new.scope <> 'tenant' then
    raise exception 'claims about people are always tenant-scoped' using errcode = '23514';
  end if;

  -- Claims age (F3): default expiry after four half-lives (weight < 6.25%).
  if new.expires_at is null and p.decay_half_life_days is not null then
    new.expires_at := new.observed_at + make_interval(days => 4 * p.decay_half_life_days);
  end if;
  return new;
end $$;
create trigger claim_validate before insert on public.claim
  for each row execute function app.validate_claim();

-- ── F4 relationships ──────────────────────────────────────────────────────
create table public.relation_type (
  key        text primary key check (key ~ '^[a-z][a-z0-9_]{1,63}$'),
  label      text not null,
  from_types public.entity_type[] not null,
  to_types   public.entity_type[] not null
);
insert into public.relation_type (key, label, from_types, to_types) values
  ('works_at',      'works at',      '{person}',         '{company}'),
  ('operated_by',   'operated by',   '{site}',           '{company}'),
  ('resells',       'resells',       '{company}',        '{company}'),
  ('belongs_to',    'belongs to',    '{digital_asset}',  '{company}'),
  ('subsidiary_of', 'subsidiary of', '{company}',        '{company}'),
  ('competes_with', 'competes with', '{company}',        '{company}'),
  ('partner_of',    'partner of',    '{company}',        '{company}');

create table public.relationship (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  from_entity_id   uuid not null references public.entity (id),
  relation         text not null references public.relation_type (key),
  to_entity_id     uuid not null references public.entity (id),
  attributes       jsonb not null default '{}',   -- e.g. title and persona on works_at
  valid_from       date,
  valid_to         date,                           -- employment ends rather than being deleted
  source_record_id uuid not null references public.source_record (id),
  confidence       smallint not null check (confidence between 0 and 100),
  scope            public.data_scope not null default 'tenant',
  created_at       timestamptz not null default now(),
  constraint valid_range check (valid_to is null or valid_from is null or valid_to >= valid_from),
  constraint no_self_link check (from_entity_id <> to_entity_id)
);
create index relationship_from_idx on public.relationship (from_entity_id, relation);
create index relationship_to_idx on public.relationship (to_entity_id, relation);
create trigger relationship_immutable before update on public.relationship
  for each row execute function app.forbid_update_except('valid_to', 'attributes');
create trigger relationship_no_delete before delete on public.relationship
  for each row execute function app.forbid_mutation();

create function app.validate_relationship() returns trigger
language plpgsql set search_path = '' as $$
declare
  rt public.relation_type%rowtype;
  f public.entity%rowtype;
  t public.entity%rowtype;
begin
  select * into rt from public.relation_type where key = new.relation;
  select * into f from public.entity where id = new.from_entity_id;
  select * into t from public.entity where id = new.to_entity_id;
  if f.org_id <> new.org_id or t.org_id <> new.org_id then
    raise exception 'relationship endpoints must belong to the same organisation' using errcode = '23514';
  end if;
  if not (f.entity_type = any (rt.from_types)) or not (t.entity_type = any (rt.to_types)) then
    raise exception '% cannot link % to %', new.relation, f.entity_type, t.entity_type using errcode = '23514';
  end if;
  if (f.entity_type = 'person' or t.entity_type = 'person') and new.scope <> 'tenant' then
    raise exception 'relationships involving people are always tenant-scoped' using errcode = '23514';
  end if;
  return new;
end $$;
create trigger relationship_validate before insert on public.relationship
  for each row execute function app.validate_relationship();

create function app.validate_alias() returns trigger
language plpgsql set search_path = '' as $$
begin
  if (select entity_type from public.entity where id = new.entity_id) = 'person' and new.scope <> 'tenant' then
    raise exception 'aliases of people are always tenant-scoped' using errcode = '23514';
  end if;
  return new;
end $$;
create trigger entity_alias_validate before insert or update on public.entity_alias
  for each row execute function app.validate_alias();

-- ── RLS: SELECT only for clients ──────────────────────────────────────────
alter table public.entity            enable row level security;
alter table public.entity_alias      enable row level security;
alter table public.account           enable row level security;
alter table public.source_record     enable row level security;
alter table public.mention           enable row level security;
alter table public.predicate         enable row level security;
alter table public.extractor_version enable row level security;
alter table public.claim             enable row level security;
alter table public.relation_type     enable row level security;
alter table public.relationship      enable row level security;

create policy entity_read        on public.entity        for select to authenticated using (app.can_read_fact(org_id, scope));
create policy entity_alias_read  on public.entity_alias  for select to authenticated using (app.can_read_fact(org_id, scope));
create policy source_record_read on public.source_record for select to authenticated using (app.can_read_fact(org_id, scope));
create policy claim_read         on public.claim         for select to authenticated using (app.can_read_fact(org_id, scope));
create policy relationship_read  on public.relationship  for select to authenticated using (app.can_read_fact(org_id, scope));
create policy mention_read       on public.mention       for select to authenticated using (app.is_org_member(org_id));
create policy account_read       on public.account       for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy predicate_read     on public.predicate     for select to authenticated
  using (workspace_id is null or app.has_workspace_role(workspace_id, 'Viewer'));
create policy extractor_version_read on public.extractor_version for select to authenticated using (true);
create policy relation_type_read     on public.relation_type     for select to authenticated using (true);

grant select on public.entity, public.entity_current, public.entity_alias, public.account, public.source_record,
  public.mention, public.predicate, public.extractor_version, public.claim, public.relation_type,
  public.relationship to authenticated;
