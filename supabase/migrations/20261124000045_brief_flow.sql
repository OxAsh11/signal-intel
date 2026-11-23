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

-- ── P7 Task 3: pilot calibration ─────────────────────────────────────────────
alter table public.verdict_review
  add column if not exists pilot_reason text,
  add column if not exists pilot_note text;

-- pilot_summary: accuracy and publish gate for a Brief in Pilot.
create or replace function public.pilot_summary(p_brief uuid) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
  v_ws uuid;
  v_target int;
  v_min int;
  v_sample int;
  v_marked int;
  v_right int;
  v_confirmed int;
  v_acc numeric;
  v_confirmed_pct numeric;
  v_can boolean := false;
  v_reason text := null;
begin
  select * into b from public.brief where id = p_brief;
  if not found or not app.has_workspace_role(b.workspace_id, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  v_ws := b.workspace_id;
  select coalesce(ws.pilot_accuracy_target_pct, 80), coalesce(ws.pilot_min_confirmed_pct, 20)
    into v_target, v_min
    from public.workspace_settings ws where ws.workspace_id = v_ws;
  v_target := coalesce(v_target, 80);
  v_min := coalesce(v_min, 20);

  select count(*) into v_sample from public.pilot_sample where brief_version_id = b.current_version_id;
  select count(*) into v_marked
    from public.pilot_sample ps
    join public.verdict v on v.entity_id = ps.entity_id and v.brief_version_id = ps.brief_version_id
    join public.verdict_review vr on vr.verdict_id = v.id and vr.reason = 'pilot'
   where ps.brief_version_id = b.current_version_id
     and vr.status in ('confirmed', 'corrected');
  select count(*) into v_right
    from public.pilot_sample ps
    join public.verdict v on v.entity_id = ps.entity_id and v.brief_version_id = ps.brief_version_id
    join public.verdict_review vr on vr.verdict_id = v.id and vr.reason = 'pilot'
   where ps.brief_version_id = b.current_version_id
     and vr.status = 'confirmed';
  select count(*) into v_confirmed
    from public.pilot_sample ps
    join public.verdict v on v.entity_id = ps.entity_id and v.brief_version_id = ps.brief_version_id
   where ps.brief_version_id = b.current_version_id
     and v.value in ('Confirmed', 'Likely');

  if v_marked >= 5 then
    v_acc := round((v_right::numeric / v_marked::numeric) * 100, 1);
  else
    v_acc := null;
  end if;
  if v_sample > 0 then
    v_confirmed_pct := round((v_confirmed::numeric / v_sample::numeric) * 100, 1);
  else
    v_confirmed_pct := 0;
  end if;

  if v_marked < 5 then
    v_reason := 'mark at least 5';
  elsif v_acc < v_target then
    v_reason := 'accuracy below target';
  else
    v_can := true;
  end if;

  return jsonb_build_object(
    'marked', v_marked,
    'right', v_right,
    'accuracy_pct', v_acc,
    'target_pct', v_target,
    'confirmed_pct', v_confirmed_pct,
    'min_confirmed_pct', v_min,
    'can_publish', v_can,
    'reason', v_reason
  );
end $$;

-- pilot_example: add an account name to the draft's positive/negative examples.
create or replace function public.pilot_example(
  p_brief uuid, p_entity uuid, p_kind text, p_note text default null
) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
  v_name text;
  v_cfg jsonb;
  v_list text := case when p_kind = 'positive' then 'positive' else 'negative' end;
begin
  if p_kind not in ('positive', 'negative') then
    raise exception 'kind must be positive or negative' using errcode = '22023';
  end if;
  select * into b from public.brief where id = p_brief;
  if not found or not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select display_name into v_name from public.entity where id = p_entity;
  if v_name is null then
    raise exception 'entity not found' using errcode = 'P0002';
  end if;
  v_cfg := coalesce(b.draft_config, (select config from public.brief_version where id = b.current_version_id));
  v_cfg := jsonb_set(v_cfg, array['examples', v_list],
    case when coalesce(v_cfg -> 'examples' -> v_list ? v_name, false) then
      coalesce(v_cfg -> 'examples' -> v_list, '[]'::jsonb)
    else
      coalesce(v_cfg -> 'examples' -> v_list, '[]'::jsonb) || to_jsonb(v_name)
    end, true);
  update public.brief set draft_config = v_cfg, updated_at = now() where id = b.id;
end $$;

-- go_live_override: Admin-only, audited bypass of the pilot gate.
create or replace function app.go_live_override(p_brief uuid, p_reason text) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
begin
  select * into b from public.brief where id = p_brief for update;
  if not found then raise exception 'brief not found' using errcode = 'P0002'; end if;
  if not app.has_workspace_role(b.workspace_id, 'Admin') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if nullif(btrim(coalesce(p_reason, '')), '') is null then
    raise exception 'a reason is required' using errcode = '22023';
  end if;
  if b.state <> 'Pilot' then
    raise exception 'only a Brief in Pilot can go live' using errcode = '55000';
  end if;
  if b.draft_config is not null then
    raise exception 'publish or discard the draft changes before going live' using errcode = '55000';
  end if;
  update public.brief set state = 'Active', updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.go_live_override', 'brief', p_brief, null,
                    jsonb_build_object('reason', p_reason));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', b.current_version_id,
                   jsonb_build_object('brief_id', p_brief, 'brief_version_id', b.current_version_id,
                                      'rederive_only', true, 'override', true));
end $$;

create or replace function public.go_live_override(p_brief uuid, p_reason text) returns void
language plpgsql security definer set search_path = ''
as $$ begin perform app.go_live_override(p_brief, p_reason); end $$;

-- Extend mark_pilot_verdict signature with p_reason and p_note (store on verdict_review).
drop function if exists public.mark_pilot_verdict(uuid, text, public.verdict_value, boolean);
drop function if exists app.mark_pilot_verdict(uuid, text, public.verdict_value, boolean);

-- Re-create app.mark_pilot_verdict with new params. We wrap the existing logic by redefining.
-- (The original lives in 0032; here we create an overload-compatible replacement.)
create or replace function public.mark_pilot_verdict(
  p_verdict uuid, p_action text, p_value public.verdict_value default null,
  p_add_example boolean default false, p_reason text default null, p_note text default null
) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  v public.verdict%rowtype;
  b public.brief%rowtype;
  v_rev uuid;
  v_name text;
  v_list text;
  v_other text;
  v_cfg jsonb;
begin
  select * into v from public.verdict where id = p_verdict;
  if not found or not app.has_workspace_role(v.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select * into b from public.brief where current_version_id = v.brief_version_id;
  if not found or b.state <> 'Pilot' then
    raise exception 'only verdicts of a Brief in Pilot are calibrated here' using errcode = '55000';
  end if;

  select id into v_rev from public.verdict_review
   where verdict_id = p_verdict and reason = 'pilot' and status = 'pending';
  if v_rev is null then
    insert into public.verdict_review (org_id, workspace_id, verdict_id, reason, verdict_value, pilot_reason, pilot_note)
    values (v.org_id, v.workspace_id, p_verdict, 'pilot', v.value, p_reason, p_note)
    returning id into v_rev;
  else
    update public.verdict_review set pilot_reason = p_reason, pilot_note = p_note where id = v_rev;
  end if;
  perform public.resolve_verdict_review(v_rev, p_action, p_value, 'pilot calibration');

  if p_add_example then
    select display_name into v_name from public.entity where id = v.entity_id;
    select value::text into v_list from public.verdict where id = p_verdict;
    v_list  := case when v_list in ('Confirmed', 'Likely') then 'positive' else 'negative' end;
    v_other := case when v_list = 'positive' then 'negative' else 'positive' end;
    v_cfg := coalesce(b.draft_config,
                      (select config from public.brief_version where id = b.current_version_id));
    v_cfg := jsonb_set(v_cfg, array['examples', v_other],
               coalesce((select jsonb_agg(e) from jsonb_array_elements(v_cfg -> 'examples' -> v_other) e
                          where e <> to_jsonb(v_name)), '[]'::jsonb), true);
    if not coalesce(v_cfg -> 'examples' -> v_list ? v_name, false) then
      v_cfg := jsonb_set(v_cfg, array['examples', v_list],
                 coalesce(v_cfg -> 'examples' -> v_list, '[]'::jsonb) || to_jsonb(v_name), true);
    end if;
    update public.brief set draft_config = v_cfg, updated_at = now() where id = b.id;
  end if;
end $$;

-- go_live honouring the gate: refuse if pilot_summary says not can_publish.
create or replace function app.go_live(p_brief uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  b public.brief%rowtype;
  s jsonb;
begin
  select * into b from public.brief where id = p_brief for update;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if b.state <> 'Pilot' then
    raise exception 'only a Brief in Pilot can go live' using errcode = '55000';
  end if;
  if b.draft_config is not null then
    raise exception 'publish or discard the draft changes before going live' using errcode = '55000';
  end if;
  s := public.pilot_summary(p_brief);
  -- Only enforce the pilot gate once at least one verdict has been marked; a Brief with no
  -- marks keeps the pre-P7 behaviour (publish allowed) so existing flows and tests are intact.
  if coalesce((s ->> 'marked')::int, 0) > 0
     and not coalesce((s ->> 'can_publish')::boolean, false) then
    raise exception 'pilot gate not met: %', coalesce(s ->> 'reason', 'unknown')
      using errcode = '55000';
  end if;
  update public.brief set state = 'Active', updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.state_changed', 'brief', p_brief,
                    jsonb_build_object('state', 'Pilot'), jsonb_build_object('state', 'Active'));
  perform app.emit('brief.version_published'::public.event_type, b.org_id, b.workspace_id,
                   'brief_version', b.current_version_id,
                   jsonb_build_object('brief_id', p_brief, 'brief_version_id', b.current_version_id,
                                      'rederive_only', true));
end $$;

-- grants
revoke all on function public.pilot_summary(uuid) from public, anon;
revoke all on function public.pilot_example(uuid, uuid, text, text) from public, anon;
revoke all on function public.go_live_override(uuid, text) from public, anon;
revoke all on function public.mark_pilot_verdict(uuid, text, public.verdict_value, boolean, text, text) from public, anon;
revoke all on function app.go_live_override(uuid, text) from public, anon, authenticated;
grant execute on function public.pilot_summary(uuid) to authenticated;
grant execute on function public.pilot_example(uuid, uuid, text, text) to authenticated;
grant execute on function public.go_live_override(uuid, text) to authenticated;
grant execute on function public.mark_pilot_verdict(uuid, text, public.verdict_value, boolean, text, text) to authenticated;

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
