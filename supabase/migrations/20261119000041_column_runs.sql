-- Migration 0041: column runs (P3).
--   * Cost first: app.column_cost_per_entity prices one cell (a formula is free; an extract column
--     costs one extract call per extracted record of the entity, 3000 input + 500 output tokens at
--     the extract model's prices). public.estimate_column_run shows count, cost and how many fit
--     the budget; public.request_column_run queues the runs, or refuses (55000) when they do not fit.
--   * The executor (signal_core.column_runtime) starts and finishes runs through app.start_run and
--     app.finish_run (predicate_run stays select-only for clients and the service role).
--   * A formula re-run ending no_data retracts the cell's older formula claim, so the cell shows
--     no_data instead of a stale derived value (app.clear_formula_claims).
--   * A new input claim queues the formula columns that read it (app.queue_auto_runs).
--   * usage_ledger rows carry the predicate_run_id of the run that spent them.

-- ── 1. Event catalogue (reviewed addition) ───────────────────────────────────
alter type public.event_type add value 'column.run_requested';

-- Kill switch for the run job.
insert into public.job_type_config (job_type) values ('column.run') on conflict do nothing;

-- ── 2. The model each AI capability uses ─────────────────────────────────────
-- SQL prices a run before any call is made, so it needs the extract model. The worker writes its
-- configured models here on start (AI_MODEL_EXTRACT); the seed is the configured default.
create table public.ai_capability_model (
  capability text primary key,
  model      text not null check (length(btrim(model)) > 0),
  updated_at timestamptz not null default now()
);
alter table public.ai_capability_model enable row level security;
create policy ai_capability_model_read on public.ai_capability_model for select to authenticated using (true);
grant select on public.ai_capability_model to authenticated;
grant select, insert, update on public.ai_capability_model to service_role;
insert into public.ai_capability_model (capability, model) values ('extract', 'claude-sonnet-5-5');

-- ── 3. Settle writes the run that spent the money ────────────────────────────
-- As 0012, plus p_actual.predicate_run_id on every settled line.
create or replace function app.settle_reservation(p_reservation uuid, p_actual jsonb) returns void
language plpgsql security definer set search_path = '' as $$
declare
  r public.usage_ledger%rowtype;
  v_status text := p_actual ->> 'status';
  l jsonb;
begin
  select * into r from public.usage_ledger where reservation_id = p_reservation and status = 'reserved';
  if not found then
    raise exception 'reservation not found' using errcode = 'P0002';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(r.workspace_id::text, 0));
  if exists (select 1 from public.usage_ledger where reservation_id = p_reservation and status = 'released') then
    raise exception 'reservation already released' using errcode = '55000';
  end if;
  if p_actual is not null and v_status not in ('ok', 'rejected') then
    raise exception 'the settled status must be ok or rejected' using errcode = '22023';
  end if;

  insert into public.usage_ledger (org_id, workspace_id, brief_id, job_id, capability, provider, model, cost_usd, status, reservation_id, approval_id)
  values (r.org_id, r.workspace_id, r.brief_id, r.job_id, r.capability, r.provider, r.model, 0, 'released', p_reservation, r.approval_id);

  if p_actual is not null then
    for l in select * from jsonb_array_elements(coalesce(p_actual -> 'lines', '[]')) loop
      insert into public.usage_ledger (org_id, workspace_id, brief_id, job_id, capability, provider, model, unit,
                                       quantity, cost_usd, latency_ms, status, reservation_id, approval_id,
                                       predicate_run_id)
      values (r.org_id, r.workspace_id, coalesce(r.brief_id, (p_actual ->> 'brief_id')::uuid),
              coalesce(p_actual ->> 'job_id', r.job_id), coalesce(p_actual ->> 'capability', r.capability),
              p_actual ->> 'provider', p_actual ->> 'model', (l ->> 'unit')::public.usage_unit,
              coalesce((l ->> 'quantity')::numeric, 0), coalesce((l ->> 'cost_usd')::numeric, 0),
              (p_actual ->> 'latency_ms')::int, v_status, p_reservation, r.approval_id,
              (p_actual ->> 'predicate_run_id')::uuid);
    end loop;
  end if;
end $$;

-- ── 4. Cost and budget ───────────────────────────────────────────────────────
-- USD per entity for one run of the column. formula and manual: 0. extract: one extract call per
-- extracted source record of the entity, 3000 input and 500 output tokens at the extract model's
-- prices; the records per entity are the workspace's average (over entities with at least one
-- extracted record; 1 when there is none yet), capped at 20. Other fill methods have no price yet.
create function app.column_cost_per_entity(p_predicate uuid) returns numeric
  language plpgsql stable security definer set search_path = ''
as $$
declare
  p        public.predicate%rowtype;
  v_model  text;
  v_in     numeric;
  v_out    numeric;
  v_recs   numeric;
begin
  select * into p from public.predicate where id = p_predicate;
  if not found then
    raise exception 'column not found' using errcode = 'P0002';
  end if;
  if p.fill_method in ('formula', 'manual') then
    return 0;
  end if;
  if p.fill_method <> 'extract' then
    raise exception 'column "%" is filled by %; it has no run price yet', p.key, p.fill_method using errcode = '22023';
  end if;
  select model into v_model from public.ai_capability_model where capability = 'extract';
  select usd_per_million into v_in from public.model_price where model = v_model and unit = 'ai_input_tokens';
  select usd_per_million into v_out from public.model_price where model = v_model and unit = 'ai_output_tokens';
  if v_in is null or v_out is null then
    raise exception 'no price for the extract model "%"', coalesce(v_model, '') using errcode = 'P0002';
  end if;
  select avg(x.n) into v_recs
    from (select m.entity_id, count(distinct sr.id) as n
            from public.source_record sr
            join public.mention m on m.source_record_id = sr.id
           where sr.workspace_id = p.workspace_id and sr.extraction_status = 'extracted'
             and m.entity_id is not null
           group by m.entity_id) x;
  v_recs := least(20, coalesce(v_recs, 1));
  return round(v_recs * (3000 * v_in + 500 * v_out) / 1000000, 6);
end $$;

-- The smallest cap − spent over the workspace's caps (and the Brief's, when one is given) for the
-- current day and month, never below 0. Spent is app.period_spend: settled rows and reservations
-- not yet released (the same sum the budget guard checks). Null when there is no cap.
create function app.remaining_budget_usd(p_ws uuid, p_brief uuid) returns numeric
  language sql stable security definer set search_path = ''
as $$
  select min(greatest(0, b.cap_usd - app.period_spend(p_ws, b.brief_id, b.period)))
    from public.budget b
   where b.workspace_id = p_ws and (b.brief_id is null or b.brief_id = p_brief)
$$;

-- Money for a message: two decimals, more only when they matter (0.12, 0.011).
create function app.usd_text(p numeric) returns text
  language sql immutable set search_path = ''
as $$
  select case when round(p, 2) = p then to_char(p, 'FM999999999990.00')
              else trim_scale(round(p, 6))::text end
$$;

-- ── 5. Which cells a request covers ──────────────────────────────────────────
-- The company entities with an account in the workspace that pass the filter (app.filter_where)
-- and the mode, minus any with a queued or running run for the column, in view order (name,
-- then id). Modes: empty (state empty or no_data), stale, errored, selected (the given ids).
create function app.column_run_targets(p_ws uuid, p_predicate uuid, p_mode text, p_brief uuid, p_filter jsonb,
                                       p_entity_ids uuid[])
returns table (entity_id uuid)
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_where text;
  v_mode  text;
  v_src   text;
begin
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  if not exists (select 1 from public.predicate p where p.id = p_predicate
                  and (p.workspace_id = p_ws or p.workspace_id is null)) then
    raise exception 'column not found in this workspace' using errcode = '22023';
  end if;
  v_where := app.filter_where(p_ws, p_brief, p_filter, 'filter');
  v_src := format('select p.id as pid, p.fill_method as fill from public.predicate p where p.id = %L::uuid', p_predicate);
  v_mode := case p_mode
    when 'empty'    then app.filter_run_sql(v_src, array['empty', 'no_data'])
    when 'stale'    then app.filter_run_sql(v_src, array['stale'])
    when 'errored'  then app.filter_run_sql(v_src, array['error'])
    when 'selected' then format('e.id = any(%L::uuid[])', coalesce(p_entity_ids, '{}'))
  end;
  if v_mode is null then
    raise exception 'mode must be empty, stale, errored or selected' using errcode = '22023';
  end if;
  if p_mode = 'selected' and coalesce(cardinality(p_entity_ids), 0) = 0 then
    raise exception 'selected needs the entity ids to run' using errcode = '22023';
  end if;
  return query execute format(
    'select e.id from public.account a join public.entity e on e.id = a.entity_id'
    ' where a.workspace_id = $1 and e.org_id = $2 and e.entity_type = ''company'' and e.status = ''active'''
    ' and %s and %s'
    ' and not exists (select 1 from public.predicate_run pr where pr.entity_id = e.id and pr.predicate_id = $3'
    ' and pr.status in (''queued'', ''running''))'
    ' order by lower(e.display_name), e.id', v_where, v_mode)
    using p_ws, v_org, p_predicate;
end $$;

-- ── 6. Estimate (Viewer+) ────────────────────────────────────────────────────
-- {count, per_entity_usd, cost_usd, remaining_usd, within_budget}. within_budget is the largest N
-- whose cost fits what is left, or count when there is no cap (or the column is free).
create function public.estimate_column_run(p_ws uuid, p_predicate uuid, p_mode text, p_brief uuid default null,
                                           p_filter jsonb default null, p_entity_ids uuid[] default null)
returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_count int;
  v_per   numeric;
  v_left  numeric;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select count(*)::int into v_count
    from app.column_run_targets(p_ws, p_predicate, p_mode, p_brief, p_filter, p_entity_ids);
  v_per  := app.column_cost_per_entity(p_predicate);
  v_left := app.remaining_budget_usd(p_ws, p_brief);
  return jsonb_build_object(
    'count', v_count,
    'per_entity_usd', v_per,
    'cost_usd', v_count * v_per,
    'remaining_usd', v_left,
    'within_budget', case when v_left is null or v_per = 0 then v_count
                          else least(v_count, floor(v_left / v_per)::int) end);
end $$;

-- ── 7. Request (Editor+) ─────────────────────────────────────────────────────
-- Queues one run per target cell (at most p_limit, in view order), stamped with the column's
-- version and the Brief's current version, and emits column.run_requested {run_ids}. A cell that
-- already has a queued or running run is skipped. Refuses more than 1000 runs (22023) and a cost
-- over what is left in the budget (55000, naming the N that fits).
create function public.request_column_run(p_ws uuid, p_predicate uuid, p_mode text, p_brief uuid default null,
                                          p_filter jsonb default null, p_entity_ids uuid[] default null,
                                          p_limit int default null)
returns jsonb
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org     uuid;
  p         public.predicate%rowtype;
  v_ids     uuid[];
  v_count   int;
  v_per     numeric;
  v_cost    numeric;
  v_left    numeric;
  v_n       int;
  v_bv      uuid;
  v_runs    uuid[];
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_limit is not null and p_limit < 1 then
    raise exception 'limit must be 1 or more' using errcode = '22023';
  end if;
  select * into p from public.predicate where id = p_predicate and (workspace_id = p_ws or workspace_id is null);
  if not found then
    raise exception 'column not found in this workspace' using errcode = '22023';
  end if;
  if p.fill_method not in ('extract', 'formula') then
    raise exception 'column "%" is filled by %; it cannot be run yet', p.key, p.fill_method using errcode = '22023';
  end if;

  select array_agg(t.entity_id) into v_ids
    from app.column_run_targets(p_ws, p_predicate, p_mode, p_brief, p_filter, p_entity_ids) t;
  v_ids := coalesce(v_ids[1:coalesce(p_limit, cardinality(v_ids))], '{}');
  v_count := cardinality(v_ids);
  if v_count > 1000 then
    raise exception 'a run request has at most 1000 cells (this one has %); set a limit or narrow the filter', v_count
      using errcode = '22023';
  end if;

  v_per  := app.column_cost_per_entity(p_predicate);
  v_cost := v_count * v_per;
  v_left := app.remaining_budget_usd(p_ws, p_brief);
  if v_left is not null and v_cost > v_left then
    v_n := least(v_count, floor(v_left / v_per)::int);
    raise exception 'this run needs $% but $% is left in the budget; run the first % within budget',
      app.usd_text(v_cost), app.usd_text(v_left), v_n using errcode = '55000';
  end if;

  if p_brief is not null then
    select current_version_id into v_bv from public.brief where id = p_brief and workspace_id = p_ws;
  end if;
  with ins as (
    insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, brief_version_id)
    select v_org, p_ws, t.id, p.id, p.version, v_bv from unnest(v_ids) t(id)
    on conflict (entity_id, predicate_id) where status in ('queued', 'running') do nothing
    returning id)
  select coalesce(array_agg(id), '{}') into v_runs from ins;

  if cardinality(v_runs) > 0 then
    perform app.emit('column.run_requested'::public.event_type, v_org, p_ws, 'predicate', p.id,
                     jsonb_build_object('run_ids', to_jsonb(v_runs)));
  end if;
  perform app.audit(v_org, p_ws, 'column.run_requested', 'predicate', p.id, null,
                    jsonb_build_object('mode', p_mode, 'brief_id', p_brief, 'queued', cardinality(v_runs),
                                       'cost_usd', cardinality(v_runs) * v_per));
  return jsonb_build_object('queued', cardinality(v_runs), 'cost_usd', cardinality(v_runs) * v_per);
end $$;

-- ── 8. Auto-run (service role) ───────────────────────────────────────────────
-- For each formula column with auto_run that reads the claim's column, queue one run on the claim's
-- entity unless one is already queued or running. Returns the new run ids.
create function app.queue_auto_runs(p_claim uuid) returns uuid[]
  language plpgsql security definer set search_path = ''
as $$
declare
  v_runs uuid[];
begin
  with ins as (
    insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version)
    select f.org_id, f.workspace_id, c.entity_id, f.id, f.version
      from public.claim c
      join public.predicate f on f.fill_method = 'formula' and f.auto_run and f.workspace_id is not null
                             and c.predicate_id = any(f.input_predicate_ids) and f.org_id = c.org_id
     where c.id = p_claim
     order by f.key collate "C"
    on conflict (entity_id, predicate_id) where status in ('queued', 'running') do nothing
    returning id)
  select coalesce(array_agg(id), '{}') into v_runs from ins;
  return v_runs;
end $$;

-- ── 9. Executor helpers (service role) ───────────────────────────────────────
-- Does the entity pass the filter (a run condition), in the workspace and Brief context?
create function app.entity_matches(p_ws uuid, p_brief uuid, p_entity uuid, p_filter jsonb) returns boolean
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_ok boolean;
begin
  execute format('select exists (select 1 from public.entity e left join public.account a'
                 ' on a.entity_id = e.id and a.workspace_id = $1 where e.id = $2 and %s)',
                 app.filter_where(p_ws, p_brief, p_filter, 'run_condition'))
    into v_ok using p_ws, p_entity;
  return v_ok;
end $$;

-- Starts a queued run: running, started_at and inputs_hash (app.run_inputs_hash). Returns what the
-- executor needs, with started true. A run that is not queued is left alone:
-- {"started": false, "status": <its status>} (status null when there is no such run).
create function app.start_run(p_run uuid) returns jsonb
  language plpgsql security definer set search_path = ''
as $$
declare
  r public.predicate_run%rowtype;
  p public.predicate%rowtype;
begin
  select * into r from public.predicate_run where id = p_run for update;
  if not found or r.status <> 'queued' then
    return jsonb_build_object('started', false, 'status', r.status);
  end if;
  update public.predicate_run
     set status = 'running', started_at = now(), inputs_hash = app.run_inputs_hash(r.entity_id, r.predicate_id)
   where id = p_run;
  select * into p from public.predicate where id = r.predicate_id;
  return jsonb_build_object(
    'started', true, 'status', 'running',
    'org_id', r.org_id, 'workspace_id', r.workspace_id, 'entity_id', r.entity_id, 'predicate_id', r.predicate_id,
    'brief_id', (select bv.brief_id from public.brief_version bv where bv.id = r.brief_version_id),
    'key', p.key, 'fill_method', p.fill_method, 'fill_config', p.fill_config, 'value_type', p.value_type,
    'allowed_values', p.allowed_values, 'run_condition', p.run_condition,
    'inputs', (select coalesce(jsonb_agg(x.key order by x.key collate "C"), '[]'::jsonb)
                 from public.predicate x where x.id = any(p.input_predicate_ids)),
    'name', (select e.display_name from public.entity e where e.id = r.entity_id),
    'domain', (select a.value_normalised from public.entity_alias a
                where a.entity_id = r.entity_id and a.alias_type = 'domain'
                order by a.confidence desc, a.created_at, a.id limit 1));
end $$;

-- The current value of each input column of a running formula run: the most recent active claim
-- (observed_at desc, then id). Inputs with no claim are left out.
create function app.run_input_values(p_run uuid)
returns table (key text, claim_id uuid, value jsonb)
  language sql stable security definer set search_path = ''
as $$
  select x.key, c.id, c.value
    from public.predicate_run r
    join public.predicate p on p.id = r.predicate_id
    join public.predicate x on x.id = any(p.input_predicate_ids)
    cross join lateral (select cl.id, cl.value from public.claim cl
                         where cl.entity_id = r.entity_id and cl.predicate_id = x.id and cl.status = 'active'
                         order by cl.observed_at desc, cl.id desc limit 1) c
   where r.id = p_run
   order by x.key collate "C"
$$;

-- A running formula run that found no value: its cell's active formula claims are retracted, so
-- the cell shows no_data rather than an older derived value. Returns how many were retracted.
create function app.clear_formula_claims(p_run uuid) returns int
  language plpgsql security definer set search_path = ''
as $$
declare
  r      public.predicate_run%rowtype;
  v_sr   uuid;
  v_n    int;
begin
  select * into r from public.predicate_run where id = p_run for update;
  if not found then
    raise exception 'run not found' using errcode = 'P0002';
  end if;
  if r.status <> 'running' then
    raise exception 'run % is %; only a running run clears its cell', p_run, r.status using errcode = '55000';
  end if;
  if (select fill_method from public.predicate where id = r.predicate_id) is distinct from 'formula' then
    raise exception 'run % is not a formula run', p_run using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(r.entity_id::text || ':' || r.predicate_id::text, 0));
  v_sr := app.derived_source_record(r.workspace_id, 'formula');
  update public.claim set status = 'retracted'
   where entity_id = r.entity_id and predicate_id = r.predicate_id and source_record_id = v_sr and status = 'active';
  get diagnostics v_n = row_count;
  update public.predicate_run set claim_id = null where id = p_run;
  return v_n;
end $$;

-- Finishes a running run: succeeded, no_data, condition_not_met or error, with finished_at and the
-- run's cost (its usage_ledger rows). A formula run ending no_data clears its cell first. A run
-- that is not running is left alone. Returns the run's status afterwards.
create function app.finish_run(p_run uuid, p_status text, p_claim uuid default null, p_inputs jsonb default null,
                               p_not_run_reason jsonb default null, p_error text default null) returns text
  language plpgsql security definer set search_path = ''
as $$
declare
  r public.predicate_run%rowtype;
begin
  if p_status is null or p_status not in ('succeeded', 'no_data', 'condition_not_met', 'error') then
    raise exception 'a run finishes succeeded, no_data, condition_not_met or error' using errcode = '22023';
  end if;
  select * into r from public.predicate_run where id = p_run for update;
  if not found then
    raise exception 'run not found' using errcode = 'P0002';
  end if;
  if r.status <> 'running' then
    return r.status;
  end if;
  if p_status = 'no_data' and (select fill_method from public.predicate where id = r.predicate_id) = 'formula' then
    perform app.clear_formula_claims(p_run);
  end if;
  update public.predicate_run
     set status = p_status,
         finished_at = now(),
         claim_id = case when p_status = 'succeeded' then coalesce(p_claim, claim_id) end,
         inputs = p_inputs,
         not_run_reason = p_not_run_reason,
         error = case when p_status = 'error' then left(coalesce(p_error, 'error'), 2000) end,
         cost_usd = (select coalesce(sum(l.cost_usd), 0) from public.usage_ledger l where l.predicate_run_id = p_run)
   where id = p_run;
  return p_status;
end $$;

-- ── 10. Grants ───────────────────────────────────────────────────────────────
revoke execute on function
  app.column_cost_per_entity(uuid), app.remaining_budget_usd(uuid, uuid), app.usd_text(numeric),
  app.column_run_targets(uuid, uuid, text, uuid, jsonb, uuid[]), app.queue_auto_runs(uuid),
  app.entity_matches(uuid, uuid, uuid, jsonb), app.start_run(uuid), app.run_input_values(uuid),
  app.clear_formula_claims(uuid), app.finish_run(uuid, text, uuid, jsonb, jsonb, text)
  from public, anon, authenticated;
grant execute on function
  app.column_cost_per_entity(uuid), app.remaining_budget_usd(uuid, uuid), app.queue_auto_runs(uuid),
  app.entity_matches(uuid, uuid, uuid, jsonb), app.start_run(uuid), app.run_input_values(uuid),
  app.clear_formula_claims(uuid), app.finish_run(uuid, text, uuid, jsonb, jsonb, text)
  to postgres, service_role;

revoke execute on function
  public.estimate_column_run(uuid, uuid, text, uuid, jsonb, uuid[]),
  public.request_column_run(uuid, uuid, text, uuid, jsonb, uuid[], int)
  from public, anon;
grant execute on function
  public.estimate_column_run(uuid, uuid, text, uuid, jsonb, uuid[]),
  public.request_column_run(uuid, uuid, text, uuid, jsonb, uuid[], int)
  to authenticated;
