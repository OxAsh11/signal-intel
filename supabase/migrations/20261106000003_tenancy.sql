-- F1 Tenancy + M14 Organisations and Access.
-- Hierarchy: organisation (customer) → workspace (team / business unit) → brief (later migration).
-- Owner/Admin are organisation-level roles; Editor/Operator/Viewer are per workspace.

create table public.organisation (
  id                    uuid primary key default gen_random_uuid(),
  name                  text not null check (length(btrim(name)) between 1 and 200),
  allowed_email_domains text[] not null default '{}',   -- M14: allowed sign-in domains
  created_at            timestamptz not null default now()
);

create table public.workspace (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null references public.organisation (id) on delete restrict,
  name       text not null check (length(btrim(name)) between 1 and 200),
  created_at timestamptz not null default now(),
  unique (org_id, name)
);
create index workspace_org_idx on public.workspace (org_id);

create table public.member (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  user_id      uuid not null references auth.users (id) on delete cascade,
  email        text not null,
  display_name text,
  status       text not null default 'active' check (status in ('active', 'removed')),
  created_at   timestamptz not null default now(),
  unique (org_id, user_id)
);
create index member_user_idx on public.member (user_id) where status = 'active';

create table public.role_assignment (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  member_id    uuid not null references public.member (id) on delete cascade,
  workspace_id uuid references public.workspace (id) on delete cascade,
  role         public.member_role not null,
  created_at   timestamptz not null default now(),
  -- Owner/Admin at org level only; Editor/Operator/Viewer per workspace only.
  constraint role_level check (
    (role in ('Owner', 'Admin') and workspace_id is null) or
    (role in ('Editor', 'Operator', 'Viewer') and workspace_id is not null)
  ),
  constraint one_role_per_scope unique nulls not distinct (member_id, workspace_id)
);
create index role_assignment_ws_idx on public.role_assignment (workspace_id, member_id);

-- Teams group members for ownership and routing (M11). They grant no permissions.
create table public.team (
  id         uuid primary key default gen_random_uuid(),
  org_id     uuid not null references public.organisation (id) on delete restrict,
  name       text not null,
  created_at timestamptz not null default now(),
  unique (org_id, name)
);
create table public.team_member (
  team_id   uuid not null references public.team (id) on delete cascade,
  member_id uuid not null references public.member (id) on delete cascade,
  org_id    uuid not null references public.organisation (id) on delete restrict,
  primary key (team_id, member_id)
);

create table public.invitation (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid references public.workspace (id) on delete cascade,
  email        text not null,
  role         public.member_role not null,
  invited_by   uuid not null references public.member (id),
  token_hash   text not null unique,      -- only the hash is stored (S8)
  expires_at   timestamptz not null,
  accepted_at  timestamptz,
  created_at   timestamptz not null default now(),
  constraint invitation_role_level check (
    (role in ('Owner', 'Admin') and workspace_id is null) or
    (role in ('Editor', 'Operator', 'Viewer') and workspace_id is not null)
  )
);

-- ── Role helpers ────────────────────────────────────────────────────────
-- STABLE + SECURITY DEFINER: membership is read live on every call, so a role change
-- or removal takes effect immediately, with no token refresh.

create function app.role_rank(r public.member_role) returns int
language sql immutable parallel safe set search_path = '' as $$
  select case r when 'Viewer' then 1 when 'Operator' then 2 when 'Editor' then 3
                when 'Admin' then 4 when 'Owner' then 5 end
$$;

create function app.is_org_member(p_org uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (
    select 1 from public.member m
    where m.org_id = p_org and m.user_id = (select auth.uid()) and m.status = 'active')
$$;

create function app.org_role_rank(p_org uuid) returns int
language sql stable security definer set search_path = '' as $$
  select coalesce(max(app.role_rank(ra.role)), 0)
  from public.role_assignment ra
  join public.member m on m.id = ra.member_id
  where m.org_id = p_org and m.user_id = (select auth.uid()) and m.status = 'active'
    and ra.workspace_id is null
$$;

create function app.workspace_role_rank(p_ws uuid) returns int
language sql stable security definer set search_path = '' as $$
  select greatest(
    coalesce((select max(app.role_rank(ra.role))
              from public.role_assignment ra
              join public.member m on m.id = ra.member_id
              where ra.workspace_id = p_ws and m.user_id = (select auth.uid()) and m.status = 'active'), 0),
    coalesce((select app.org_role_rank(w.org_id) from public.workspace w where w.id = p_ws), 0))
$$;

create function app.has_org_role(p_org uuid, p_min public.member_role) returns boolean
language sql stable security definer set search_path = '' as $$
  select app.org_role_rank(p_org) >= app.role_rank(p_min)
$$;

create function app.has_workspace_role(p_ws uuid, p_min public.member_role) returns boolean
language sql stable security definer set search_path = '' as $$
  select app.workspace_role_rank(p_ws) >= app.role_rank(p_min)
$$;

create function app.current_member_id(p_org uuid) returns uuid
language sql stable security definer set search_path = '' as $$
  select m.id from public.member m
  where m.org_id = p_org and m.user_id = (select auth.uid()) and m.status = 'active'
$$;

grant execute on function app.role_rank(public.member_role), app.is_org_member(uuid),
  app.org_role_rank(uuid), app.workspace_role_rank(uuid), app.has_org_role(uuid, public.member_role),
  app.has_workspace_role(uuid, public.member_role), app.current_member_id(uuid)
  to authenticated, service_role;

-- ── RLS: SELECT only. Writes go through audited functions (S8, M18). ──────
alter table public.organisation    enable row level security;
alter table public.workspace       enable row level security;
alter table public.member          enable row level security;
alter table public.role_assignment enable row level security;
alter table public.team            enable row level security;
alter table public.team_member     enable row level security;
alter table public.invitation      enable row level security;

create policy organisation_read    on public.organisation    for select to authenticated using (app.is_org_member(id));
create policy workspace_read       on public.workspace       for select to authenticated using (app.has_workspace_role(id, 'Viewer'));
create policy member_read          on public.member          for select to authenticated using (app.is_org_member(org_id));
create policy role_assignment_read on public.role_assignment for select to authenticated using (app.is_org_member(org_id));
create policy team_read            on public.team            for select to authenticated using (app.is_org_member(org_id));
create policy team_member_read     on public.team_member     for select to authenticated using (app.is_org_member(org_id));
create policy invitation_read      on public.invitation      for select to authenticated using (app.has_org_role(org_id, 'Admin'));

grant select on public.organisation, public.workspace, public.member, public.role_assignment,
  public.team, public.team_member, public.invitation to authenticated;
