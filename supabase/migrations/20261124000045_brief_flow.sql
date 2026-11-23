-- ── P7 Task 1: Generate a Brief from a website ──────────────────────────────
-- New event type for the worker job.
alter type public.event_type add value if not exists 'brief.generate_requested';

-- Progress steps written by the worker as it reads the site and drafts the Brief.
alter table public.brief
  add column if not exists intake_steps jsonb not null default '[]'::jsonb;

-- Reset helpers: keep intake_status in sync when a draft is saved (unchanged behaviour).
-- (no change to save_brief_draft)

-- Create a Brief from a domain and ask the worker to generate its draft from the site.
-- Editor+ only; the domain is normalised by the worker (FetchGuard blocks private hosts).
create or replace function app.generate_brief(
  p_ws uuid, p_domain text, p_description text default null,
  p_name_auto boolean default true
) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_brief uuid;
  v_desc  text := nullif(btrim(coalesce(p_description, '')), '');
  v_domain text := nullif(btrim(coalesce(p_domain, '')), '');
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if v_domain is null then
    raise exception 'a domain is required' using errcode = '22023';
  end if;
  if length(v_desc) > 4000 then
    raise exception 'keep the description under 4000 characters' using errcode = '22023';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.brief (org_id, workspace_id, name, name_auto, state, intake_status, intake_steps, created_by)
  values (v_org, p_ws, v_domain, coalesce(p_name_auto, true), 'Draft', 'pending', '[]'::jsonb, app.current_member_id(v_org))
  returning id into v_brief;
  perform app.audit(v_org, p_ws, 'brief.created', 'brief', v_brief, null,
                    jsonb_build_object('name', v_domain, 'name_auto', coalesce(p_name_auto, true),
                                       'generate_from_domain', true));
  perform app.emit('brief.generate_requested'::public.event_type, v_org, p_ws, 'brief', v_brief,
                   jsonb_build_object('brief_id', v_brief, 'domain', v_domain,
                                      'description', coalesce(v_desc, ''),
                                      'name_auto', coalesce(p_name_auto, true),
                                      'org_id', v_org, 'workspace_id', p_ws));
  return v_brief;
end $$;

create or replace function public.generate_brief(
  p_ws uuid, p_domain text, p_description text default null,
  p_name_auto boolean default true
) returns uuid
language plpgsql security definer set search_path = ''
as $$
begin
  return app.generate_brief(p_ws, p_domain, p_description, p_name_auto);
end $$;

revoke all on function app.generate_brief(uuid, text, text, boolean) from public, anon, authenticated;
revoke all on function public.generate_brief(uuid, text, text, boolean) from public, anon;
grant execute on function public.generate_brief(uuid, text, text, boolean) to authenticated;
grant execute on function app.generate_brief(uuid, text, text, boolean) to authenticated;

-- Kill-switch seed for the new job type.
insert into public.job_type_config (job_type, enabled) values ('brief.generate', true)
  on conflict (job_type) do nothing;

-- Worker writes progress steps while it reads the site and drafts the Brief.
create or replace function app.update_intake_steps(p_brief uuid, p_steps jsonb) returns void
language plpgsql security definer set search_path = ''
as $$
begin
  update public.brief set intake_steps = p_steps, updated_at = now() where id = p_brief;
  if not found then raise exception 'brief not found' using errcode = 'P0002'; end if;
end $$;

revoke all on function app.update_intake_steps(uuid, jsonb) from public, anon, authenticated;
grant execute on function app.update_intake_steps(uuid, jsonb) to service_role;
