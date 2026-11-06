-- M18 audit log (append-only) + S4 transactional outbox + the first audited write paths.

create table public.audit_log (
  id              bigint generated always as identity primary key,
  org_id          uuid not null references public.organisation (id) on delete restrict,
  workspace_id    uuid references public.workspace (id) on delete restrict,
  actor_user_id   uuid,                 -- null = system
  actor_member_id uuid,
  channel         text not null check (channel in ('interface', 'api', 'mcp', 'system')),
  action          text not null,
  target_type     text not null,
  target_id       uuid,
  before          jsonb,
  after           jsonb,
  occurred_at     timestamptz not null default now()
);
create index audit_log_org_time_idx on public.audit_log (org_id, occurred_at desc);
create index audit_log_target_idx   on public.audit_log (target_type, target_id);

create function app.forbid_mutation() returns trigger
language plpgsql set search_path = '' as $$
begin
  raise exception '% is append-only', tg_table_name using errcode = '42501';
end $$;

create trigger audit_log_append_only
  before update or delete on public.audit_log
  for each row execute function app.forbid_mutation();
create trigger audit_log_no_truncate
  before truncate on public.audit_log
  for each statement execute function app.forbid_mutation();

alter table public.audit_log enable row level security;
create policy audit_log_read on public.audit_log for select to authenticated
  using (app.has_org_role(org_id, 'Admin'));
grant select on public.audit_log to authenticated;

-- Channel comes from the caller: the API and MCP services set `app.channel`; the worker sets 'system'.
create function app.current_channel() returns text
language sql stable set search_path = '' as $$
  select coalesce(nullif(current_setting('app.channel', true), ''),
                  case when (select auth.uid()) is null then 'system' else 'interface' end)
$$;

create function app.audit(
  p_org uuid, p_ws uuid, p_action text, p_target_type text, p_target_id uuid,
  p_before jsonb default null, p_after jsonb default null
) returns void
language sql security definer set search_path = '' as $$
  insert into public.audit_log (org_id, workspace_id, actor_user_id, actor_member_id, channel,
                                action, target_type, target_id, before, after)
  values (p_org, p_ws, (select auth.uid()), app.current_member_id(p_org), app.current_channel(),
          p_action, p_target_type, p_target_id, p_before, p_after)
$$;

-- ── S4 outbox: written in the same transaction as the change; relayed by the worker. ──
create table public.outbox (
  id             uuid primary key default gen_random_uuid(),
  event          public.event_type not null,           -- closed catalogue (A.4)
  org_id         uuid not null references public.organisation (id) on delete restrict,
  workspace_id   uuid references public.workspace (id) on delete restrict,
  aggregate_type text not null,
  aggregate_id   uuid not null,
  payload        jsonb not null default '{}',
  created_at     timestamptz not null default now(),
  published_at   timestamptz,
  attempts       int not null default 0,
  last_error     text
);
create index outbox_pending_idx on public.outbox (created_at) where published_at is null;
alter table public.outbox enable row level security;   -- no policies: service role only

create function app.notify_outbox() returns trigger
language plpgsql set search_path = '' as $$
begin
  perform pg_notify('outbox', new.id::text);
  return new;
end $$;
create trigger outbox_notify after insert on public.outbox
  for each row execute function app.notify_outbox();

create function app.emit(
  p_event public.event_type, p_org uuid, p_ws uuid, p_aggregate_type text, p_aggregate_id uuid,
  p_payload jsonb default '{}'
) returns uuid
language sql security definer set search_path = '' as $$
  insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  values (p_event, p_org, p_ws, p_aggregate_type, p_aggregate_id, coalesce(p_payload, '{}'))
  returning id
$$;

revoke execute on function app.audit(uuid, uuid, text, text, uuid, jsonb, jsonb) from authenticated;
revoke execute on function app.emit(public.event_type, uuid, uuid, text, uuid, jsonb) from authenticated;
grant execute on function app.audit(uuid, uuid, text, text, uuid, jsonb, jsonb),
  app.emit(public.event_type, uuid, uuid, text, uuid, jsonb), app.current_channel() to service_role;

-- ── Audited write paths (the only way the interface changes tenancy data). ──

-- Sign-up: the first user of a new customer creates the organisation and becomes its Owner.
create function public.create_organisation(p_name text, p_workspace_name text default 'Default')
returns uuid
language plpgsql security definer set search_path = '' as $$
declare
  v_uid uuid := (select auth.uid());
  v_org uuid; v_member uuid; v_ws uuid; v_email text;
begin
  if v_uid is null then
    raise exception 'sign in required' using errcode = '28000';
  end if;
  select u.email into v_email from auth.users u where u.id = v_uid;

  insert into public.organisation (name) values (p_name) returning id into v_org;
  insert into public.member (org_id, user_id, email) values (v_org, v_uid, coalesce(v_email, ''))
    returning id into v_member;
  insert into public.role_assignment (org_id, member_id, role) values (v_org, v_member, 'Owner');
  insert into public.workspace (org_id, name) values (v_org, p_workspace_name) returning id into v_ws;

  perform app.audit(v_org, null, 'organisation.created', 'organisation', v_org, null,
                    jsonb_build_object('name', p_name));
  perform app.audit(v_org, v_ws, 'workspace.created', 'workspace', v_ws, null,
                    jsonb_build_object('name', p_workspace_name));
  return v_org;
end $$;

create function public.create_workspace(p_org uuid, p_name text)
returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_ws uuid;
begin
  if not app.has_org_role(p_org, 'Admin') then
    raise exception 'only an Owner or Admin can create workspaces' using errcode = '42501';
  end if;
  insert into public.workspace (org_id, name) values (p_org, p_name) returning id into v_ws;
  perform app.audit(p_org, v_ws, 'workspace.created', 'workspace', v_ws, null,
                    jsonb_build_object('name', p_name));
  return v_ws;
end $$;

grant execute on function public.create_organisation(text, text), public.create_workspace(uuid, text)
  to authenticated;
