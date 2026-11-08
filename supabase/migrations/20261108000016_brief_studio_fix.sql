-- Phase 1b fixes: re-publish for Active/Paused briefs; update_brief_draft without intake_status side-effect.

-- FR1 + FR2b: replace app.publish_brief to accept Active/Paused briefs that have a non-null draft_config.
-- A Draft brief transitions to Active; Active/Paused briefs stay in their current state.
create or replace function app.publish_brief(p_brief uuid, p_change_note text default null) returns uuid
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
  if b.state not in ('Draft', 'Active', 'Paused') then
    raise exception 'only Draft, Active, or Paused briefs can be published' using errcode = '42501';
  end if;
  if b.state in ('Active', 'Paused') and b.draft_config is null then
    raise exception 'no draft changes to publish' using errcode = '42501';
  end if;
  v_cfg := coalesce(b.draft_config, '{}');
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
  insert into public.brief_version
    (brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_by)
  values
    (p_brief, b.org_id, b.workspace_id, v_ver, v_cfg,
     '{}'::jsonb, '{}'::jsonb, p_change_note, app.current_member_id(b.org_id))
  returning id into v_vid;
  update public.brief
     set state              = case when b.state = 'Draft' then 'Active'::public.brief_state else b.state end,
         current_version_id = v_vid,
         intake_status      = 'none',
         draft_config       = null,
         updated_at         = now()
   where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.version_published', 'brief', p_brief,
                    jsonb_build_object('state', b.state),
                    jsonb_build_object('state', case when b.state = 'Draft' then 'Active' else b.state end, 'version', v_ver));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', v_vid,
                   jsonb_build_object('brief_id', p_brief, 'version', v_ver));
  return v_vid;
end $$;

-- FR2b: app.update_brief_draft — UI-path draft save that only sets draft_config,
-- does not touch intake_status, and rejects Archived briefs.
create function app.update_brief_draft(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
begin
  select * into b from public.brief where id = p_brief;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if b.state = 'Archived' then
    raise exception 'cannot edit an archived brief' using errcode = '42501';
  end if;
  update public.brief set draft_config = p_config, updated_at = now() where id = p_brief;
end $$;

-- FR2b: replace public.save_brief_draft_ui to call app.update_brief_draft instead of
-- app.save_brief_draft, so UI saves no longer flip intake_status to 'done'.
create or replace function public.save_brief_draft_ui(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare v_ws uuid;
begin
  select workspace_id into v_ws from public.brief where id = p_brief;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  perform app.update_brief_draft(p_brief, p_config);
end $$;

grant execute on function app.update_brief_draft(uuid, jsonb) to postgres;
