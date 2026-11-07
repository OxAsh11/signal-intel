-- Spend totals for the Settings screen, computed in SQL so they match the cap maths exactly
-- (open reservations count; a released reservation does not) and are not subject to the API row limit.
create function public.workspace_spend(p_ws uuid) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'you do not have access to this workspace' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'day', app.period_spend(p_ws, null, 'day'),
    'month', app.period_spend(p_ws, null, 'month'));
end $$;

revoke execute on function public.workspace_spend(uuid) from public, anon;
grant execute on function public.workspace_spend(uuid) to authenticated;
