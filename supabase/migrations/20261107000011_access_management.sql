-- M14 Organisations and Access: invitations, roles, members, allowed domains and teams.
-- Every function checks the caller's role against the PRD permission matrix and writes audit_log.

alter table public.invitation add column revoked_at timestamptz;

-- Clients never see token hashes.
revoke select on public.invitation from authenticated;
grant select (id, org_id, workspace_id, email, role, invited_by, expires_at, accepted_at, revoked_at, created_at)
  on public.invitation to authenticated;

-- ── Helpers ────────────────────────────────────────────────────────────────
create function app.current_email() returns text
language sql stable security definer set search_path = '' as $$
  select lower(u.email) from auth.users u where u.id = (select auth.uid())
$$;

create function app.email_domain(p_email text) returns text
language sql immutable set search_path = '' as $$
  select lower(split_part(p_email, '@', 2))
$$;

create function app.require_org_role(p_org uuid, p_min public.member_role, p_what text) returns void
language plpgsql stable security definer set search_path = '' as $$
begin
  if (select auth.uid()) is null then
    raise exception 'sign in required' using errcode = '28000';
  end if;
  if not app.has_org_role(p_org, p_min) then
    raise exception 'only % can %', case p_min when 'Owner' then 'an Owner' else 'an Owner or Admin' end, p_what
      using errcode = '42501';
  end if;
end $$;

create function app.assert_has_owner(p_org uuid) returns void
language plpgsql stable security definer set search_path = '' as $$
begin
  if not exists (
    select 1 from public.role_assignment ra join public.member m on m.id = ra.member_id
    where ra.org_id = p_org and ra.role = 'Owner' and ra.workspace_id is null and m.status = 'active') then
    raise exception 'an organisation must keep at least one Owner' using errcode = '23514';
  end if;
end $$;

create function app.domain_allowed(p_org uuid, p_email text) returns boolean
language sql stable security definer set search_path = '' as $$
  select cardinality(o.allowed_email_domains) = 0 or app.email_domain(p_email) = any (o.allowed_email_domains)
  from public.organisation o where o.id = p_org
$$;

create function app.is_owner_member(p_member uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.role_assignment
                 where member_id = p_member and role = 'Owner' and workspace_id is null)
$$;

-- ── Invitations ────────────────────────────────────────────────────────────
-- Returns the invitation token ONCE. Only its SHA-256 hash is stored.
create function public.invite_member(p_org uuid, p_email text, p_role public.member_role, p_workspace uuid default null)
returns text
language plpgsql security definer set search_path = '' as $$
declare
  v_email text := lower(btrim(p_email));
  v_token text := encode(extensions.gen_random_bytes(24), 'hex');
  v_id uuid;
begin
  perform app.require_org_role(p_org, 'Admin', 'invite members');
  if p_role = 'Owner' then
    perform app.require_org_role(p_org, 'Owner', 'invite another Owner');
  end if;
  if v_email !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then
    raise exception 'enter a valid email address' using errcode = '22023';
  end if;
  if p_workspace is not null and not exists (select 1 from public.workspace where id = p_workspace and org_id = p_org) then
    raise exception 'that workspace is not in this organisation' using errcode = '22023';
  end if;
  if not app.domain_allowed(p_org, v_email) then
    raise exception 'email domain % is not allowed for this organisation', app.email_domain(v_email) using errcode = '42501';
  end if;
  if exists (select 1 from public.member where org_id = p_org and lower(email) = v_email and status = 'active') then
    raise exception 'this person is already a member: change their role instead' using errcode = '23505';
  end if;

  insert into public.invitation (org_id, workspace_id, email, role, invited_by, token_hash, expires_at)
  values (p_org, p_workspace, v_email, p_role, app.current_member_id(p_org),
          encode(extensions.digest(v_token, 'sha256'), 'hex'), now() + interval '7 days')
  returning id into v_id;

  perform app.audit(p_org, p_workspace, 'invitation.created', 'invitation', v_id, null,
                    jsonb_build_object('email', v_email, 'role', p_role, 'workspace_id', p_workspace));
  return v_token;
end $$;

create function public.revoke_invitation(p_invitation uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare inv public.invitation%rowtype;
begin
  select * into inv from public.invitation where id = p_invitation;
  if not found then
    raise exception 'invitation not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(inv.org_id, 'Admin', 'revoke invitations');
  update public.invitation set revoked_at = now() where id = p_invitation and revoked_at is null and accepted_at is null;
  perform app.audit(inv.org_id, inv.workspace_id, 'invitation.revoked', 'invitation', inv.id, null, null);
end $$;

-- Shared acceptance path: the invitation row is locked by the caller.
create function app.accept_invitation_row(inv public.invitation) returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_uid uuid := (select auth.uid());
  v_email text := app.current_email();
  v_member uuid;
begin
  if v_uid is null then
    raise exception 'sign in required' using errcode = '28000';
  end if;
  if inv.revoked_at is not null or inv.accepted_at is not null or inv.expires_at <= now() then
    raise exception 'this invitation is no longer valid: ask for a new one' using errcode = '22023';
  end if;
  if v_email is distinct from lower(inv.email) then
    raise exception 'this invitation was sent to a different email address' using errcode = '42501';
  end if;
  if not app.domain_allowed(inv.org_id, v_email) then
    raise exception 'your email domain is not allowed for this organisation' using errcode = '42501';
  end if;

  insert into public.member (org_id, user_id, email, status)
  values (inv.org_id, v_uid, v_email, 'active')
  on conflict (org_id, user_id) do update set status = 'active', email = excluded.email
  returning id into v_member;

  insert into public.role_assignment (org_id, member_id, workspace_id, role)
  values (inv.org_id, v_member, inv.workspace_id, inv.role)
  on conflict on constraint one_role_per_scope do update set role = excluded.role;

  update public.invitation set accepted_at = now() where id = inv.id;
  perform app.audit(inv.org_id, inv.workspace_id, 'invitation.accepted', 'invitation', inv.id, null,
                    jsonb_build_object('member_id', v_member, 'role', inv.role));
  return inv.org_id;
end $$;

create function public.accept_invitation(p_token text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare inv public.invitation%rowtype;
begin
  select * into inv from public.invitation
   where token_hash = encode(extensions.digest(btrim(p_token), 'sha256'), 'hex')
   for update;
  if not found then
    raise exception 'invitation not found' using errcode = 'P0002';
  end if;
  return app.accept_invitation_row(inv);
end $$;

-- Pending invitations for the signed-in user's own email (no token needed).
create function public.my_invitations()
returns table (id uuid, org_id uuid, org_name text, workspace_id uuid, workspace_name text,
               role public.member_role, expires_at timestamptz)
language sql stable security definer set search_path = '' as $$
  select i.id, i.org_id, o.name, i.workspace_id, w.name, i.role, i.expires_at
  from public.invitation i
  join public.organisation o on o.id = i.org_id
  left join public.workspace w on w.id = i.workspace_id
  where lower(i.email) = app.current_email()
    and i.accepted_at is null and i.revoked_at is null and i.expires_at > now()
  order by i.created_at
$$;

create function public.accept_my_invitation(p_invitation uuid) returns uuid
language plpgsql security definer set search_path = '' as $$
declare inv public.invitation%rowtype;
begin
  select * into inv from public.invitation where id = p_invitation for update;
  if not found then
    raise exception 'invitation not found' using errcode = 'P0002';
  end if;
  return app.accept_invitation_row(inv);
end $$;

-- ── Roles and members ─────────────────────────────────────────────────────
-- Owner/Admin: p_workspace must be null. Editor/Operator/Viewer: p_workspace required.
create function public.set_member_role(p_member uuid, p_role public.member_role, p_workspace uuid default null)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  m public.member%rowtype;
  v_before public.member_role;
begin
  select * into m from public.member where id = p_member;
  if not found or m.status <> 'active' then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(m.org_id, 'Admin', 'change roles');
  if p_role in ('Owner', 'Admin') and p_workspace is not null
     or p_role in ('Editor', 'Operator', 'Viewer') and p_workspace is null then
    raise exception 'Owner and Admin are organisation roles; Editor, Operator and Viewer are set per workspace'
      using errcode = '22023';
  end if;
  if p_workspace is not null and not exists (select 1 from public.workspace where id = p_workspace and org_id = m.org_id) then
    raise exception 'that workspace is not in this organisation' using errcode = '22023';
  end if;

  select role into v_before from public.role_assignment
   where member_id = p_member and workspace_id is not distinct from p_workspace;
  if p_role = 'Owner' or v_before = 'Owner' then
    perform app.require_org_role(m.org_id, 'Owner', 'grant or remove the Owner role');
  end if;

  insert into public.role_assignment (org_id, member_id, workspace_id, role)
  values (m.org_id, p_member, p_workspace, p_role)
  on conflict on constraint one_role_per_scope do update set role = excluded.role;

  perform app.assert_has_owner(m.org_id);
  perform app.audit(m.org_id, p_workspace, 'role.changed', 'member', p_member,
                    jsonb_build_object('role', v_before), jsonb_build_object('role', p_role));
end $$;

create function public.remove_role(p_member uuid, p_workspace uuid default null) returns void
language plpgsql security definer set search_path = '' as $$
declare
  m public.member%rowtype;
  v_before public.member_role;
begin
  select * into m from public.member where id = p_member;
  if not found then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(m.org_id, 'Admin', 'change roles');
  select role into v_before from public.role_assignment
   where member_id = p_member and workspace_id is not distinct from p_workspace;
  if v_before = 'Owner' then
    perform app.require_org_role(m.org_id, 'Owner', 'remove the Owner role');
  end if;
  delete from public.role_assignment where member_id = p_member and workspace_id is not distinct from p_workspace;
  perform app.assert_has_owner(m.org_id);
  perform app.audit(m.org_id, p_workspace, 'role.removed', 'member', p_member,
                    jsonb_build_object('role', v_before), null);
end $$;

-- Access ends immediately: every RLS helper checks active membership.
-- (API and MCP token revocation joins this function when api_token lands with M13.)
create function public.remove_member(p_member uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare m public.member%rowtype;
begin
  select * into m from public.member where id = p_member;
  if not found or m.status <> 'active' then
    raise exception 'member not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(m.org_id, 'Admin', 'remove members');
  if app.is_owner_member(p_member) then
    perform app.require_org_role(m.org_id, 'Owner', 'remove an Owner');
  end if;
  update public.member set status = 'removed' where id = p_member;
  delete from public.role_assignment where member_id = p_member;
  delete from public.team_member where member_id = p_member;
  perform app.assert_has_owner(m.org_id);
  perform app.audit(m.org_id, null, 'member.removed', 'member', p_member,
                    jsonb_build_object('status', 'active'), jsonb_build_object('status', 'removed'));
end $$;

-- ── Allowed sign-in domains ────────────────────────────────────────────────
create function public.set_allowed_domains(p_org uuid, p_domains text[]) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_clean text[];
  v_before text[];
  d text;
begin
  perform app.require_org_role(p_org, 'Admin', 'change allowed domains');
  select coalesce(array_agg(distinct lower(btrim(x))) filter (where btrim(x) <> ''), '{}')
    into v_clean from unnest(coalesce(p_domains, '{}')) x;
  foreach d in array v_clean loop
    if d !~ '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$' then
      raise exception '"%" is not a valid domain', d using errcode = '22023';
    end if;
  end loop;
  select allowed_email_domains into v_before from public.organisation where id = p_org;
  update public.organisation set allowed_email_domains = v_clean where id = p_org;
  perform app.audit(p_org, null, 'organisation.domains_changed', 'organisation', p_org,
                    jsonb_build_object('allowed_email_domains', v_before),
                    jsonb_build_object('allowed_email_domains', v_clean));
end $$;

-- ── Teams (routing and ownership only; they grant no permissions) ──────────
create function public.create_team(p_org uuid, p_name text) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  perform app.require_org_role(p_org, 'Admin', 'manage teams');
  insert into public.team (org_id, name) values (p_org, btrim(p_name)) returning id into v_id;
  perform app.audit(p_org, null, 'team.created', 'team', v_id, null, jsonb_build_object('name', btrim(p_name)));
  return v_id;
end $$;

create function public.set_team_member(p_team uuid, p_member uuid, p_in_team boolean) returns void
language plpgsql security definer set search_path = '' as $$
declare t public.team%rowtype;
begin
  select * into t from public.team where id = p_team;
  if not found then
    raise exception 'team not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(t.org_id, 'Admin', 'manage teams');
  if not exists (select 1 from public.member where id = p_member and org_id = t.org_id and status = 'active') then
    raise exception 'member not found in this organisation' using errcode = 'P0002';
  end if;
  if p_in_team then
    insert into public.team_member (team_id, member_id, org_id) values (p_team, p_member, t.org_id) on conflict do nothing;
  else
    delete from public.team_member where team_id = p_team and member_id = p_member;
  end if;
  perform app.audit(t.org_id, null, case when p_in_team then 'team.member_added' else 'team.member_removed' end,
                    'team', p_team, null, jsonb_build_object('member_id', p_member));
end $$;

grant execute on function
  public.invite_member(uuid, text, public.member_role, uuid),
  public.revoke_invitation(uuid),
  public.accept_invitation(text),
  public.my_invitations(),
  public.accept_my_invitation(uuid),
  public.set_member_role(uuid, public.member_role, uuid),
  public.remove_role(uuid, uuid),
  public.remove_member(uuid),
  public.set_allowed_domains(uuid, text[]),
  public.create_team(uuid, text),
  public.set_team_member(uuid, uuid, boolean)
to authenticated;
