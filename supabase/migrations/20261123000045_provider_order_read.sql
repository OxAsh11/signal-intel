-- Settings must read the waterfall order fresh after an Admin moves a provider. A REST GET to
-- workspace_settings can be served from a server-side fetch cache; this RPC is a POST, like the
-- write itself, so a reload after the write always sees the saved order. Viewer role can read,
-- and the default is returned when a workspace has no settings row yet.
create or replace function public.get_provider_order(p_ws uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_order jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select provider_order into v_order from public.workspace_settings where workspace_id = p_ws;
  return coalesce(nullif(v_order, 'null'::jsonb), '["leadmagic","findymail","prospeo"]'::jsonb);
end $$;

revoke all on function public.get_provider_order(uuid) from public, anon;
grant execute on function public.get_provider_order(uuid) to authenticated;
