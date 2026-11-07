-- M1/F5 Briefs and immutable Brief versions; F6 activity (append-only) and feedback (ground truth).

create table public.brief (
  id                 uuid primary key default gen_random_uuid(),
  org_id             uuid not null references public.organisation (id) on delete restrict,
  workspace_id       uuid not null references public.workspace (id) on delete restrict,
  name               text not null check (length(btrim(name)) between 1 and 200),
  state              public.brief_state not null default 'Draft',
  current_version_id uuid,                               -- FK added below (circular)
  created_by         uuid references public.member (id),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (workspace_id, name)
);

-- F5: the full Brief at a point in time. Immutable once published; edits publish a new version.
create table public.brief_version (
  id           uuid primary key default gen_random_uuid(),
  brief_id     uuid not null references public.brief (id) on delete restrict,
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  version      int not null check (version > 0),
  config       jsonb not null,          -- offer, hypothesis, examples, sources, ICP, personas, predicates…
  rubric       jsonb not null,          -- verdict rules (rubric_version is carried inside the Brief version)
  weights      jsonb not null,          -- scoring weights, mappings, band thresholds, gates
  edit_tier    public.edit_tier,        -- tier of the edit that produced it (null for v1)
  change_note  text,
  published_by uuid references public.member (id),
  published_at timestamptz not null default now(),
  unique (brief_id, version)
);
create trigger brief_version_immutable before update or delete on public.brief_version
  for each row execute function app.forbid_mutation();

alter table public.brief
  add constraint brief_current_version_fk foreign key (current_version_id) references public.brief_version (id);

create function app.validate_brief_version() returns trigger
language plpgsql set search_path = '' as $$
declare b public.brief%rowtype;
begin
  select * into b from public.brief where id = new.brief_id;
  if b.org_id <> new.org_id or b.workspace_id <> new.workspace_id then
    raise exception 'brief_version tenancy must match its brief' using errcode = '23514';
  end if;
  if new.version <> coalesce((select max(version) from public.brief_version where brief_id = new.brief_id), 0) + 1 then
    raise exception 'brief versions are sequential: expected %', coalesce(
      (select max(version) from public.brief_version where brief_id = new.brief_id), 0) + 1 using errcode = '23514';
  end if;
  return new;
end $$;
create trigger brief_version_validate before insert on public.brief_version
  for each row execute function app.validate_brief_version();

-- ── F6 activity: append-only log of every touch and outcome ──────────────
create table public.activity (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  account_id       uuid references public.account (id),
  person_entity_id uuid references public.entity (id),
  kind             public.activity_kind not null,
  channel          text,
  campaign_id      uuid,                 -- FK added with M9
  integration_id   uuid,                 -- FK added with M13
  external_id      text,
  brief_version_id uuid references public.brief_version (id),   -- Part 8.1: version active at send time
  occurred_at      timestamptz not null,
  payload          jsonb not null default '{}',
  created_at       timestamptz not null default now(),
  -- Replays and backfills never double count (F6).
  constraint activity_external_unique unique (integration_id, external_id),
  constraint external_needs_integration check (external_id is null or integration_id is not null)
);
create index activity_account_time_idx on public.activity (account_id, occurred_at desc);
create index activity_ws_kind_time_idx on public.activity (workspace_id, kind, occurred_at desc);
create trigger activity_append_only before update or delete on public.activity
  for each row execute function app.forbid_mutation();

-- ── F6 feedback: every human correction or confirmation (ground truth) ───
create table public.feedback (
  id              uuid primary key default gen_random_uuid(),
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid references public.workspace (id) on delete restrict,
  member_id       uuid not null references public.member (id),
  target_type     text not null check (target_type in
                    ('verdict', 'claim', 'identity_match', 'entity_merge', 'reply_class', 'score', 'label',
                     'research_doc', 'meeting', 'next_step', 'gated_action')),
  target_id       uuid not null,
  action          text not null check (action in ('confirm', 'reject', 'correct')),
  corrected_value jsonb,
  reason          text,
  created_at      timestamptz not null default now(),
  constraint correct_has_value check (action <> 'correct' or corrected_value is not null)
);
create index feedback_target_idx on public.feedback (target_type, target_id, created_at desc);
create trigger feedback_append_only before update or delete on public.feedback
  for each row execute function app.forbid_mutation();

-- ── RLS ─────────────────────────────────────────────────────────────────
alter table public.brief         enable row level security;
alter table public.brief_version enable row level security;
alter table public.activity      enable row level security;
alter table public.feedback      enable row level security;

create policy brief_read         on public.brief         for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy brief_version_read on public.brief_version for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy activity_read      on public.activity      for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy feedback_read      on public.feedback      for select to authenticated
  using (case when workspace_id is null then app.has_org_role(org_id, 'Admin')
              else app.has_workspace_role(workspace_id, 'Viewer') end);

grant select on public.brief, public.brief_version, public.activity, public.feedback to authenticated;
