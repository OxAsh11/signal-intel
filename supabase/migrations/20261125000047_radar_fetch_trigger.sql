-- Allow Editors to manually queue a re-fetch for a radar from the UI.
-- Writes to the outbox which the worker polls; never touches radar/source_record directly.
create function public.trigger_radar_fetch(p_radar uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org      uuid;
  v_ws       uuid;
  v_brief    uuid;
  v_type     text;
begin
  select org_id, workspace_id, brief_id, connector_type
    into v_org, v_ws, v_brief, v_type
    from public.radar
   where id = p_radar;

  if not found then
    raise exception 'radar not found' using errcode = 'P0002';
  end if;

  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;

  insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  values (
    'radar.fetch_queued', v_org, v_ws, 'radar', p_radar,
    jsonb_build_object(
      'radar_id',       p_radar,
      'connector_type', v_type,
      'org_id',         v_org,
      'workspace_id',   v_ws,
      'brief_id',       v_brief
    )
  );
end $$;

revoke execute on function public.trigger_radar_fetch(uuid) from public, anon;
grant  execute on function public.trigger_radar_fetch(uuid) to   authenticated;
