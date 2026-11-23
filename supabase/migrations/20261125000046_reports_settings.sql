-- ── P8: Reports and Settings ─────────────────────────────────────────────────

-- ── 1. Hand labels: tag definition + link ────────────────────────────────────
drop table if exists public.account_tag cascade;
create table public.account_tag (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  name text not null,
  colour text not null default '#6b7280',
  created_by uuid references public.member (id),
  created_at timestamptz not null default now(),
  unique (workspace_id, name)
);
create table public.account_tag_link (
  tag_id uuid not null references public.account_tag (id) on delete cascade,
  account_id uuid not null references public.account (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (tag_id, account_id)
);
alter table public.account_tag enable row level security;
alter table public.account_tag_link enable row level security;
create policy account_tag_read on public.account_tag for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy account_tag_link_read on public.account_tag_link for select to authenticated
  using (exists (select 1 from public.account_tag t where t.id = tag_id
                  and app.has_workspace_role(t.workspace_id, 'Viewer')));
grant select on public.account_tag, public.account_tag_link to authenticated;

-- ── 2. Column library ────────────────────────────────────────────────────────
create table if not exists public.column_template (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  name text not null,
  spec jsonb not null,
  created_by uuid references public.member (id),
  created_at timestamptz not null default now()
);
alter table public.column_template enable row level security;
create policy column_template_read on public.column_template for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.column_template to authenticated;

-- ── 3. Business context on workspace_settings ────────────────────────────────
alter table public.workspace_settings
  add column if not exists business_context jsonb not null default '{}'::jsonb;

-- ── 4. Budget alerts ─────────────────────────────────────────────────────────
create table if not exists public.budget_alert (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  budget_id uuid not null references public.budget (id) on delete cascade,
  period_start timestamptz not null,
  threshold int not null check (threshold in (50, 80, 100)),
  created_at timestamptz not null default now(),
  unique (budget_id, period_start, threshold)
);
alter table public.budget_alert enable row level security;
create policy budget_alert_read on public.budget_alert for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.budget_alert to authenticated;

-- ── 5. Reports RPCs ──────────────────────────────────────────────────────────
create or replace function public.report_funnel(p_ws uuid, p_brief uuid default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_found bigint; v_matched bigint; v_confirmed bigint;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select count(*) into v_found from public.account a
    where a.workspace_id = p_ws and (p_brief is null or a.brief_id = p_brief);
  select count(*) into v_matched from public.score s join public.account a on a.id = s.account_id
    where a.workspace_id = p_ws and (p_brief is null or a.brief_id = p_brief);
  select count(*) into v_confirmed from public.verdict v join public.account a on a.entity_id = v.entity_id
    where a.workspace_id = p_ws and v.value in ('Confirmed','Likely')
      and (p_brief is null or a.brief_id = p_brief);
  return jsonb_build_object('found', v_found, 'matched', v_matched, 'confirmed', v_confirmed);
end $$;

create or replace function public.report_coverage(p_ws uuid, p_brief uuid default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rows jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object(
    'key', p.key, 'label', p.label, 'fill_method', p.fill_method,
    'filled', (select count(*) from public.claim c where c.predicate_id = p.id and c.status = 'active'),
    'total', (select count(*) from public.account a where a.workspace_id = p_ws and (p_brief is null or a.brief_id = p_brief))
  )), '[]'::jsonb) into v_rows
  from public.predicate p where p.workspace_id = p_ws;
  return jsonb_build_object('columns', v_rows);
end $$;

create or replace function public.report_cost(p_ws uuid, p_brief uuid default null, p_months int default 3) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_total numeric; v_by_provider jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(sum(t.cost_usd), 0) into v_total
    from public.predicate_run_attempt t
    where t.workspace_id = p_ws and t.created_at >= now() - (p_months || ' months')::interval
      and (p_brief is null or t.brief_id = p_brief);
  select coalesce(jsonb_agg(jsonb_build_object('provider', t.provider, 'cost', sum(t.cost_usd))), '[]'::jsonb)
    into v_by_provider
    from public.predicate_run_attempt t
    where t.workspace_id = p_ws and t.created_at >= now() - (p_months || ' months')::interval
      and (p_brief is null or t.brief_id = p_brief)
    group by t.provider;
  return jsonb_build_object('total', v_total, 'by_provider', coalesce(v_by_provider, '[]'::jsonb));
end $$;

create or replace function public.report_sources(p_ws uuid, p_brief uuid default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rows jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('radar_id', r.id, 'name', r.name)), '[]'::jsonb) into v_rows
    from public.radar r where r.workspace_id = p_ws and (p_brief is null or r.brief_id = p_brief);
  return jsonb_build_object('radars', coalesce(v_rows, '[]'::jsonb));
end $$;

create or replace function public.report_calibration(p_ws uuid, p_brief uuid default null) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_wrong bigint;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select count(*) into v_wrong from public.feedback f
    where f.workspace_id = p_ws and f.action in ('correct', 'claim_wrong');
  return jsonb_build_object('marked_wrong', v_wrong);
end $$;

-- ── 6. Budget alerts RPCs ────────────────────────────────────────────────────
create or replace function public.set_budget(p_ws uuid, p_brief uuid, p_period text, p_cap_usd numeric) returns void
language plpgsql security definer set search_path = ''
as $$
declare v_org uuid;
begin
  if not app.has_workspace_role(p_ws, 'Admin') then raise exception 'permission denied' using errcode = '42501'; end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if p_cap_usd is null then
    delete from public.budget where workspace_id = p_ws and (brief_id is not distinct from p_brief) and period = p_period::public.budget_period;
    perform app.audit(v_org, p_ws, 'budget.removed', 'budget', null, null, jsonb_build_object('period', p_period));
  else
    insert into public.budget (org_id, workspace_id, brief_id, period, cap_usd)
    values (v_org, p_ws, p_brief, p_period::public.budget_period, p_cap_usd)
    on conflict (workspace_id, brief_id, period) do update set cap_usd = excluded.cap_usd;
    perform app.audit(v_org, p_ws, 'budget.set', 'budget', null, null, jsonb_build_object('period', p_period, 'cap_usd', p_cap_usd));
  end if;
end $$;

create or replace function public.budget_alerts(p_ws uuid) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rows jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'budget_id', budget_id, 'threshold', threshold, 'period_start', period_start)), '[]'::jsonb)
    into v_rows from public.budget_alert where workspace_id = p_ws;
  return coalesce(v_rows, '[]'::jsonb);
end $$;

-- check_budget_thresholds: called from settle_reservation. Writes one alert per threshold per period.
create or replace function app.check_budget_thresholds(p_ws uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare
  b record;
  v_spent numeric;
  v_period_start timestamptz;
  v_threshold int;
begin
  for b in select * from public.budget where workspace_id = p_ws loop
    v_period_start := case when b.period = 'day' then date_trunc('day', now()) else date_trunc('month', now()) end;
    v_spent := app.period_spend(b.workspace_id, b.brief_id, b.period);
    foreach v_threshold in array array[50, 80, 100] loop
      if v_spent >= (b.cap_usd * v_threshold / 100) then
        insert into public.budget_alert (workspace_id, budget_id, period_start, threshold)
        values (p_ws, b.id, v_period_start, v_threshold)
        on conflict (budget_id, period_start, threshold) do nothing;
      end if;
    end loop;
  end loop;
end $$;

-- ── 7. Labels RPCs ───────────────────────────────────────────────────────────
create or replace function public.save_tag(p_ws uuid, p_name text, p_colour text default null) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare v_org uuid; v_id uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then raise exception 'permission denied' using errcode = '42501'; end if;
  select org_id into v_org from public.workspace where id = p_ws;
  insert into public.account_tag (org_id, workspace_id, name, colour, created_by)
  values (v_org, p_ws, btrim(p_name), coalesce(p_colour, '#6b7280'), app.current_member_id(v_org))
  on conflict (workspace_id, name) do update set colour = coalesce(p_colour, public.account_tag.colour)
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.delete_tag(p_tag uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare t public.account_tag%rowtype;
begin
  select * into t from public.account_tag where id = p_tag;
  if not found or not app.has_workspace_role(t.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  delete from public.account_tag where id = p_tag;
end $$;

create or replace function public.tag_accounts(p_ws uuid, p_tag uuid, p_account_ids uuid[], p_on boolean) returns void
language plpgsql security definer set search_path = ''
as $$
declare t public.account_tag%rowtype;
begin
  select * into t from public.account_tag where id = p_tag;
  if not found or t.workspace_id <> p_ws or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_on then
    insert into public.account_tag_link (tag_id, account_id)
    select p_tag, unnest(p_account_ids) on conflict do nothing;
  else
    delete from public.account_tag_link where tag_id = p_tag and account_id = any(p_account_ids);
  end if;
end $$;

-- ── 8. Segments ──────────────────────────────────────────────────────────────
create or replace function public.save_segment(p_ws uuid, p_id uuid, p_name text, p_filter jsonb) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare v_org uuid; v_id uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then raise exception 'permission denied' using errcode = '42501'; end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if p_id is null then
    insert into public.segment (org_id, workspace_id, name, filter)
    values (v_org, p_ws, btrim(p_name), p_filter) returning id into v_id;
  else
    update public.segment set name = btrim(p_name), filter = p_filter, updated_at = now()
      where id = p_id and workspace_id = p_ws returning id into v_id;
  end if;
  if v_id is null then raise exception 'segment not found' using errcode = 'P0002'; end if;
  return v_id;
end $$;

create or replace function public.segment_counts(p_ws uuid) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rows jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', s.id, 'name', s.name)), '[]'::jsonb) into v_rows
    from public.segment s where s.workspace_id = p_ws;
  return coalesce(v_rows, '[]'::jsonb);
end $$;

-- ── 9. Column library ────────────────────────────────────────────────────────
create or replace function public.save_column_template(p_ws uuid, p_name text, p_spec jsonb) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare v_org uuid; v_id uuid;
begin
  if not app.has_workspace_role(p_ws, 'Editor') then raise exception 'permission denied' using errcode = '42501'; end if;
  select org_id into v_org from public.workspace where id = p_ws;
  insert into public.column_template (org_id, workspace_id, name, spec, created_by)
  values (v_org, p_ws, btrim(p_name), p_spec, app.current_member_id(v_org)) returning id into v_id;
  return v_id;
end $$;

create or replace function public.delete_column_template(p_template uuid) returns void
language plpgsql security definer set search_path = ''
as $$
declare t public.column_template%rowtype;
begin
  select * into t from public.column_template where id = p_template;
  if not found or not app.has_workspace_role(t.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  delete from public.column_template where id = p_template;
end $$;

create or replace function public.list_column_templates(p_ws uuid) returns jsonb
language plpgsql stable security definer set search_path = ''
as $$
declare v_rows jsonb;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then raise exception 'permission denied' using errcode = '42501'; end if;
  select coalesce(jsonb_agg(jsonb_build_object('id', id, 'name', name, 'spec', spec)), '[]'::jsonb)
    into v_rows from public.column_template where workspace_id = p_ws;
  return coalesce(v_rows, '[]'::jsonb);
end $$;

-- ── 10. Business context ─────────────────────────────────────────────────────
create or replace function public.set_business_context(p_ws uuid, p_context jsonb) returns void
language plpgsql security definer set search_path = ''
as $$
begin
  if not app.has_workspace_role(p_ws, 'Admin') then raise exception 'permission denied' using errcode = '42501'; end if;
  update public.workspace_settings set business_context = coalesce(p_context, '{}'::jsonb), updated_at = now()
   where workspace_id = p_ws;
end $$;

-- ── Grants ───────────────────────────────────────────────────────────────────
revoke all on function public.report_funnel(uuid, uuid) from public, anon;
revoke all on function public.report_coverage(uuid, uuid) from public, anon;
revoke all on function public.report_cost(uuid, uuid, int) from public, anon;
revoke all on function public.report_sources(uuid, uuid) from public, anon;
revoke all on function public.report_calibration(uuid, uuid) from public, anon;
revoke all on function public.set_budget(uuid, uuid, text, numeric) from public, anon;
revoke all on function public.budget_alerts(uuid) from public, anon;
revoke all on function public.save_tag(uuid, text, text) from public, anon;
revoke all on function public.delete_tag(uuid) from public, anon;
revoke all on function public.tag_accounts(uuid, uuid, uuid[], boolean) from public, anon;
revoke all on function public.save_segment(uuid, uuid, text, jsonb) from public, anon;
revoke all on function public.segment_counts(uuid) from public, anon;
revoke all on function public.save_column_template(uuid, text, jsonb) from public, anon;
revoke all on function public.delete_column_template(uuid) from public, anon;
revoke all on function public.list_column_templates(uuid) from public, anon;
revoke all on function public.set_business_context(uuid, jsonb) from public, anon;
grant execute on function public.report_funnel(uuid, uuid) to authenticated;
grant execute on function public.report_coverage(uuid, uuid) to authenticated;
grant execute on function public.report_cost(uuid, uuid, int) to authenticated;
grant execute on function public.report_sources(uuid, uuid) to authenticated;
grant execute on function public.report_calibration(uuid, uuid) to authenticated;
grant execute on function public.set_budget(uuid, uuid, text, numeric) to authenticated;
grant execute on function public.budget_alerts(uuid) to authenticated;
grant execute on function public.save_tag(uuid, text, text) to authenticated;
grant execute on function public.delete_tag(uuid) to authenticated;
grant execute on function public.tag_accounts(uuid, uuid, uuid[], boolean) to authenticated;
grant execute on function public.save_segment(uuid, uuid, text, jsonb) to authenticated;
grant execute on function public.segment_counts(uuid) to authenticated;
grant execute on function public.save_column_template(uuid, text, jsonb) to authenticated;
grant execute on function public.delete_column_template(uuid) to authenticated;
grant execute on function public.list_column_templates(uuid) to authenticated;
grant execute on function public.set_business_context(uuid, jsonb) to authenticated;
revoke all on function app.check_budget_thresholds(uuid) from public, anon, authenticated;