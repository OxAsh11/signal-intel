-- AI-first Brief Studio: the intake drafts the whole Brief from a plain-language description
-- (what you sell, who you want to find, your website, a customer you won), not from the name alone.

drop function public.create_brief(uuid, text);
drop function app.create_brief(uuid, text);

create function app.create_brief(p_ws uuid, p_name text, p_description text default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_brief uuid;
  v_desc  text := nullif(btrim(coalesce(p_description, '')), '');
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if length(v_desc) > 4000 then
    raise exception 'keep the description under 4000 characters' using errcode = '22023';
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
  perform app.emit('brief.intake_requested'::public.event_type, v_org, p_ws, 'brief', v_brief,
                   jsonb_build_object('brief_id', v_brief, 'name', p_name,
                                      'description', coalesce(v_desc, p_name),
                                      'org_id', v_org, 'workspace_id', p_ws));
  return v_brief;
end $$;

create function public.create_brief(p_ws uuid, p_name text, p_description text default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
begin
  return app.create_brief(p_ws, p_name, p_description);
end $$;

revoke execute on function app.create_brief(uuid, text, text) from public, anon, authenticated;
grant execute on function app.create_brief(uuid, text, text) to postgres, service_role;
revoke execute on function public.create_brief(uuid, text, text) from public, anon;
grant execute on function public.create_brief(uuid, text, text) to authenticated;
