-- Phase 1b Brief Studio: draft_config column, exclusion_list table, and brief RPCs (migration 0015).
-- Depends on: 0001 (default privileges), 0003 (tenancy), 0004 (audit + outbox),
--             0007 (entity, predicate, member), 0008 (brief, brief_version).

-- ── 1. Add brief.intake_requested to the event_type enum ─────────────────────
-- PG 17 (PGlite) supports ALTER TYPE … ADD VALUE inside a transaction.
alter type public.event_type add value 'brief.intake_requested' after 'brief.version_published';

-- ── 2. Brief schema additions ─────────────────────────────────────────────────
alter table public.brief
  add column draft_config    jsonb,
  add column intake_status   text not null default 'none'
    check (intake_status in ('none', 'pending', 'done', 'failed'));

-- ── 3. Exclusion list (F3 / M1): companies a Brief must not target ────────────
create table public.exclusion_list (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  brief_id     uuid not null references public.brief (id) on delete restrict,
  value_type   text not null check (value_type in ('entity', 'name', 'domain')),
  entity_id    uuid references public.entity (id),
  pattern      text,          -- exact name or domain text; null for entity exclusions
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

-- ── 4. app.* functions (core logic, called via SECURITY DEFINER public wrappers or service_role) ──

-- app.create_brief: Editor+ creates a Draft brief and fires AI intake via outbox.
create function app.create_brief(p_ws uuid, p_name text) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_brief uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.brief (org_id, workspace_id, name, state, intake_status, created_by)
  values (v_org, p_ws, p_name, 'Draft', 'pending', app.current_member_id(v_org))
  returning id into v_brief;
  perform app.audit(v_org, p_ws, 'brief.created', 'brief', v_brief, null,
                    jsonb_build_object('name', p_name));
  -- R2: outbox payload includes org_id and workspace_id for the worker's BriefIntakeInput.
  perform app.emit('brief.intake_requested'::public.event_type, v_org, p_ws, 'brief', v_brief,
                   jsonb_build_object('brief_id', v_brief, 'name', p_name,
                                     'org_id', v_org, 'workspace_id', p_ws));
  return v_brief;
end $$;

-- app.save_brief_draft: service_role only — AI intake worker sets draft_config on success.
create function app.save_brief_draft(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
begin
  update public.brief
     set draft_config = p_config,
         intake_status = 'done',
         updated_at = now()
   where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
end $$;

-- app.fail_brief_intake: service_role only — AI intake worker marks failure.
create function app.fail_brief_intake(p_brief uuid) returns void
  language plpgsql security definer set search_path = ''
as $$
begin
  update public.brief
     set intake_status = 'failed',
         updated_at = now()
   where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
end $$;

-- app.publish_brief: Editor+ validates draft_config and publishes version 1 (or N+1).
-- Brief must leave Draft state via this function only (not update_brief_state).
create function app.publish_brief(p_brief uuid, p_change_note text default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b     public.brief%rowtype;
  v_cfg jsonb;
  v_ver int;
  v_vid uuid;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state <> 'Draft' then
    raise exception 'only a Draft brief can be published via publish_brief' using errcode = '42501';
  end if;
  v_cfg := coalesce(b.draft_config, '{}');
  -- Validate required fields (PRD gate before publish).
  if coalesce(trim(v_cfg ->> 'offer'), '') = '' then
    raise exception 'draft_config.offer is required and cannot be empty' using errcode = '42501';
  end if;
  if coalesce(trim(v_cfg ->> 'hypothesis'), '') = '' then
    raise exception 'draft_config.hypothesis is required and cannot be empty' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(v_cfg -> 'examples' -> 'positive', '[]'::jsonb)) < 3 then
    raise exception 'draft_config.examples.positive requires at least 3 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(v_cfg -> 'examples' -> 'negative', '[]'::jsonb)) < 2 then
    raise exception 'draft_config.examples.negative requires at least 2 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(v_cfg -> 'sources' -> 'radar_types', '[]'::jsonb)) < 1 then
    raise exception 'draft_config.sources.radar_types requires at least 1 item' using errcode = '42501';
  end if;
  select coalesce(max(version), 0) + 1 into v_ver
    from public.brief_version where brief_id = p_brief;
  -- rubric and weights have no DB default; supply '{}' explicitly (global constraint).
  insert into public.brief_version
    (brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_by)
  values
    (p_brief, b.org_id, b.workspace_id, v_ver, v_cfg,
     '{}'::jsonb, '{}'::jsonb, p_change_note, app.current_member_id(b.org_id))
  returning id into v_vid;
  update public.brief
     set state              = 'Active',
         current_version_id = v_vid,
         intake_status      = 'none',
         draft_config       = null,
         updated_at         = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.version_published', 'brief', p_brief,
                    jsonb_build_object('state', 'Draft'),
                    jsonb_build_object('state', 'Active', 'version', v_ver));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', v_vid,
                   jsonb_build_object('brief_id', p_brief, 'version', v_ver));
  return v_vid;
end $$;

-- app.update_brief_state: Editor+ transitions state.
-- Allowed: Active↔Paused, Active→Archived, Paused→Archived.
-- Draft→Active is blocked here — use publish_brief.
create function app.update_brief_state(p_brief uuid, p_new_state public.brief_state) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state = p_new_state then return; end if;
  if not (
    (b.state = 'Active' and p_new_state = 'Paused'  ) or
    (b.state = 'Paused' and p_new_state = 'Active'  ) or
    (b.state = 'Active' and p_new_state = 'Archived') or
    (b.state = 'Paused' and p_new_state = 'Archived')
  ) then
    raise exception 'invalid state transition: % → %', b.state, p_new_state using errcode = '42501';
  end if;
  update public.brief
     set state = p_new_state, updated_at = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.state_changed', 'brief', p_brief,
                    jsonb_build_object('state', b.state),
                    jsonb_build_object('state', p_new_state));
end $$;

-- ── 5. public.* wrappers (PostgREST-callable, SECURITY DEFINER → run as postgres) ──

-- public.create_brief: UI wrapper for app.create_brief.
create function public.create_brief(p_ws uuid, p_name text) returns uuid
  language plpgsql security definer set search_path = ''
as $$
begin
  return app.create_brief(p_ws, p_name);
end $$;

-- public.publish_brief: UI wrapper for app.publish_brief.
create function public.publish_brief(p_brief uuid, p_change_note text default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
begin
  return app.publish_brief(p_brief, p_change_note);
end $$;

-- public.update_brief_state: UI wrapper for app.update_brief_state.
create function public.update_brief_state(p_brief uuid, p_new_state public.brief_state) returns void
  language plpgsql security definer set search_path = ''
as $$
begin
  perform app.update_brief_state(p_brief, p_new_state);
end $$;

-- public.save_brief_draft_ui (R1): UI wrapper — checks Editor+ then calls app.save_brief_draft.
-- The worker calls app.save_brief_draft directly (service_role, no JWT); the UI uses this.
create function public.save_brief_draft_ui(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare v_ws uuid;
begin
  select workspace_id into v_ws from public.brief where id = p_brief;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  perform app.save_brief_draft(p_brief, p_config);
end $$;

-- public.create_workspace_predicate: Editor+ adds a workspace-scoped predicate (F3 / M1).
create function public.create_workspace_predicate(
  p_ws              uuid,
  p_key             text,
  p_label           text,
  p_value_type      text,
  p_allowed_values  jsonb default null,
  p_extraction_hint text  default null
) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org  uuid;
  v_pred uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.predicate
    (key, org_id, workspace_id, label, value_type, allowed_values, extraction_hint)
  values
    (p_key, v_org, p_ws, p_label, p_value_type, p_allowed_values, p_extraction_hint)
  returning id into v_pred;
  perform app.audit(v_org, p_ws, 'predicate.created', 'predicate', v_pred, null,
                    jsonb_build_object('key', p_key, 'value_type', p_value_type));
  return v_pred;
end $$;

-- public.add_exclusion: Editor+ adds a company to the Brief's exclusion list.
create function public.add_exclusion(
  p_brief      uuid,
  p_value_type text,
  p_entity_id  uuid default null,
  p_pattern    text default null,
  p_reason     text default null
) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  b    public.brief%rowtype;
  v_id uuid;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  insert into public.exclusion_list
    (org_id, workspace_id, brief_id, value_type, entity_id, pattern, reason, added_by)
  values
    (b.org_id, b.workspace_id, p_brief, p_value_type,
     p_entity_id, p_pattern, p_reason, app.current_member_id(b.org_id))
  returning id into v_id;
  return v_id;
end $$;

-- public.remove_exclusion: Editor+ removes a company from the exclusion list.
create function public.remove_exclusion(p_id uuid) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  e public.exclusion_list%rowtype;
begin
  select * into e from public.exclusion_list where id = p_id;
  if not found then
    raise exception 'exclusion not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(e.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  delete from public.exclusion_list where id = p_id;
end $$;

-- public.list_briefs: Viewer+ lists all briefs in a workspace with version counts.
create function public.list_briefs(p_ws uuid)
  returns table (
    id              uuid,
    name            text,
    state           public.brief_state,
    intake_status   text,
    version_count   bigint,
    current_version int,
    updated_at      timestamptz
  )
  language plpgsql security definer set search_path = ''
as $$
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  return query
    select b.id,
           b.name,
           b.state,
           b.intake_status,
           count(bv.id),
           (select bv2.version
              from public.brief_version bv2
             where bv2.id = b.current_version_id)::int,
           b.updated_at
      from public.brief b
      left join public.brief_version bv on bv.brief_id = b.id
     where b.workspace_id = p_ws
     group by b.id, b.name, b.state, b.intake_status, b.current_version_id, b.updated_at
     order by b.updated_at desc;
end $$;

-- ── 6. Grants ─────────────────────────────────────────────────────────────────
-- Default privileges (migration 0001) already revoke from public/anon/authenticated for new functions.
-- Explicit revokes below are defensive, matching the pattern in migration 0012 (metering).

-- app.save_brief_draft and app.fail_brief_intake: worker-only, service_role only.
revoke execute on function
  app.save_brief_draft(uuid, jsonb),
  app.fail_brief_intake(uuid)
  from public, anon, authenticated;
grant execute on function
  app.save_brief_draft(uuid, jsonb),
  app.fail_brief_intake(uuid)
  to service_role;

-- Core app.* functions: callable via service_role and by SECURITY DEFINER public wrappers (run as postgres).
revoke execute on function
  app.create_brief(uuid, text),
  app.publish_brief(uuid, text),
  app.update_brief_state(uuid, public.brief_state)
  from public, anon, authenticated;
grant execute on function
  app.create_brief(uuid, text),
  app.publish_brief(uuid, text),
  app.update_brief_state(uuid, public.brief_state)
  to service_role;

-- public.* functions: callable by authenticated users via PostgREST RPC.
revoke execute on function
  public.create_brief(uuid, text),
  public.publish_brief(uuid, text),
  public.update_brief_state(uuid, public.brief_state),
  public.save_brief_draft_ui(uuid, jsonb),
  public.create_workspace_predicate(uuid, text, text, text, jsonb, text),
  public.add_exclusion(uuid, text, uuid, text, text),
  public.remove_exclusion(uuid),
  public.list_briefs(uuid)
  from public, anon;
grant execute on function
  public.create_brief(uuid, text),
  public.publish_brief(uuid, text),
  public.update_brief_state(uuid, public.brief_state),
  public.save_brief_draft_ui(uuid, jsonb),
  public.create_workspace_predicate(uuid, text, text, text, jsonb, text),
  public.add_exclusion(uuid, text, uuid, text, text),
  public.remove_exclusion(uuid),
  public.list_briefs(uuid)
  to authenticated;
