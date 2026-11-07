-- S6 metering: usage ledger, price table, budgets and approvals (Phase 1a).
-- Budget check comes before the call: a reservation over a cap does not go through, it opens a
-- budget_approval. Caps are USD, per workspace and per Brief, day and month, UTC calendar periods.
-- No budget row = no cap. Spend = sum of usage_ledger.cost_usd in the period.

-- Enums: must match packages/contracts/schema/enums.json (checked by `pnpm db:check`).
create type public.usage_unit as enum ('ai_input_tokens', 'ai_output_tokens', 'embed_tokens', 'fetch', 'enrichment_lookup', 'research_doc');  -- UsageUnit
create type public.budget_period as enum ('day', 'month');  -- BudgetPeriod
create type public.approval_status as enum ('pending', 'approved', 'rejected', 'expired');  -- ApprovalStatus

-- ── Prices ─────────────────────────────────────────────────────────────────
create table public.model_price (
  model          text not null,
  provider       text not null,
  unit           public.usage_unit not null,
  usd_per_million numeric(12,4) not null check (usd_per_million >= 0),
  updated_at     timestamptz not null default now(),
  primary key (model, unit)
);
alter table public.model_price enable row level security;
create policy model_price_read on public.model_price for select to authenticated using (true);
grant select on public.model_price to authenticated;

-- Prices as of 2026-11-07; sources: Anthropic model table cached 2026-10-06, docs.voyageai.com/docs/pricing.
-- USD per million units. Extract, judge, write and summarise all use claude-sonnet-5-5.
insert into public.model_price (model, provider, unit, usd_per_million) values
  ('claude-sonnet-5-5', 'anthropic', 'ai_input_tokens',  2.0000),
  ('claude-sonnet-5-5', 'anthropic', 'ai_output_tokens', 10.0000),
  ('claude-haiku-5-5',  'anthropic', 'ai_input_tokens',  0.1000),   -- prompts up to 100K tokens
  ('claude-haiku-5-5',  'anthropic', 'ai_output_tokens', 0.5000),   -- prompts up to 100K tokens
  ('voyage-4',          'voyage',    'embed_tokens',     0.0600);

-- ── Ledger (insert-only) ───────────────────────────────────────────────────
-- A `reserved` row holds the estimate; a `released` row (cost 0, same reservation_id) cancels it.
create table public.usage_ledger (
  id             bigint generated always as identity primary key,
  org_id         uuid not null references public.organisation (id) on delete restrict,
  workspace_id   uuid not null references public.workspace (id) on delete restrict,
  brief_id       uuid references public.brief (id) on delete restrict,
  job_id         text,
  capability     text,
  provider       text,
  model          text,
  unit           public.usage_unit,
  quantity       numeric not null default 0,
  cost_usd       numeric(14,6) not null default 0 check (cost_usd >= 0),
  latency_ms     int,
  status         text not null check (status in ('ok', 'rejected', 'reserved', 'released')),
  reservation_id uuid,
  approval_id    uuid,   -- the budget_approval this spend was let through under (FK added below)
  created_at     timestamptz not null default now(),
  constraint ledger_unit_for_spend check (status in ('reserved', 'released') or unit is not null)
);
create index usage_ledger_ws_time_idx    on public.usage_ledger (workspace_id, created_at);
create index usage_ledger_brief_time_idx on public.usage_ledger (brief_id, created_at);
create index usage_ledger_reservation_idx on public.usage_ledger (reservation_id) where reservation_id is not null;

create trigger usage_ledger_append_only
  before update or delete on public.usage_ledger
  for each row execute function app.forbid_mutation();
create trigger usage_ledger_no_truncate
  before truncate on public.usage_ledger
  for each statement execute function app.forbid_mutation();
revoke update, delete, truncate on public.usage_ledger from anon, authenticated, service_role;

alter table public.usage_ledger enable row level security;
create policy usage_ledger_read on public.usage_ledger for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.usage_ledger to authenticated;
grant select, insert on public.usage_ledger to service_role;

-- ── Budgets and approvals ──────────────────────────────────────────────────
create table public.budget (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  brief_id     uuid references public.brief (id) on delete cascade,   -- null = workspace-wide cap
  period       public.budget_period not null,
  cap_usd      numeric(12,2) not null check (cap_usd > 0),
  updated_by   uuid references public.member (id),
  updated_at   timestamptz not null default now(),
  constraint budget_one_per_scope unique nulls not distinct (workspace_id, brief_id, period)
);
alter table public.budget enable row level security;
create policy budget_read on public.budget for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.budget to authenticated;

create table public.budget_approval (
  id            uuid primary key default gen_random_uuid(),
  org_id        uuid not null references public.organisation (id) on delete restrict,
  workspace_id  uuid not null references public.workspace (id) on delete cascade,
  brief_id      uuid references public.brief (id) on delete cascade,
  job_type      text not null,
  job_args      jsonb not null default '{}',
  estimate_usd  numeric(12,4) not null check (estimate_usd >= 0),
  reason        text not null,
  status        public.approval_status not null default 'pending',
  requested_at  timestamptz not null default now(),
  decided_by    uuid references public.member (id),
  decided_at    timestamptz,
  resumed_at    timestamptz
);
create index budget_approval_ws_idx on public.budget_approval (workspace_id, status);
alter table public.usage_ledger add constraint usage_ledger_approval_fk foreign key (approval_id) references public.budget_approval (id) on delete restrict;
create index usage_ledger_approval_idx on public.usage_ledger (approval_id) where approval_id is not null;
alter table public.budget_approval enable row level security;
create policy budget_approval_read on public.budget_approval for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Editor'));
grant select on public.budget_approval to authenticated;

-- Owner/Admin approve any amount; Editor only up to this limit.
alter table public.workspace_settings
  add column editor_approval_limit_usd numeric(10,2) not null default 25 check (editor_approval_limit_usd >= 0);

-- ── Seeds ──────────────────────────────────────────────────────────────────
insert into public.job_type_config (job_type) values ('system.ai_status'), ('budget.resume')
  on conflict do nothing;

-- ── Spend and reservations (service role only) ─────────────────────────────
-- Start of the current UTC calendar period.
create function app.period_start(p_period public.budget_period) returns timestamptz
language sql stable set search_path = '' as $$
  select date_trunc(p_period::text, now() at time zone 'utc') at time zone 'utc'
$$;

-- p_brief null = the whole workspace. A reserved row counts only until its released twin exists.
create function app.period_spend(p_ws uuid, p_brief uuid, p_period public.budget_period) returns numeric
language sql stable security definer set search_path = '' as $$
  select coalesce(sum(l.cost_usd), 0)
  from public.usage_ledger l
  where l.workspace_id = p_ws
    and (p_brief is null or l.brief_id = p_brief)
    and l.created_at >= app.period_start(p_period)
    and (l.status in ('ok', 'rejected')
         or (l.status = 'reserved' and not exists (
               select 1 from public.usage_ledger r
               where r.reservation_id = l.reservation_id and r.status = 'released')))
$$;

create function app.reserve_budget(
  p_ws uuid, p_brief uuid, p_estimate numeric, p_job_type text, p_job_args jsonb,
  p_approval uuid default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_org uuid;
  v_cap record;
  v_spent numeric;
  v_reason text;
  v_approval uuid;
  v_reservation uuid := gen_random_uuid();
  v_bypass uuid;   -- the approval this reservation is let through under, if any
  a public.budget_approval%rowtype;
begin
  if p_estimate is null or p_estimate < 0 then
    raise exception 'the estimate must be zero or more' using errcode = '22023';
  end if;
  -- Serialise reservations per workspace (the workspace cap spans all Briefs) before reading spend.
  perform pg_advisory_xact_lock(hashtextextended(p_ws::text, 0));

  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  if p_brief is not null and not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception 'that Brief is not in this workspace' using errcode = '22023';
  end if;

  -- An approval pre-approves spend only for the same workspace, Brief and job type, within 24 hours of
  -- the decision, and only up to its estimate counting what is already charged to it. Otherwise the
  -- normal caps apply (a non-matching approval never errors).
  if p_approval is not null then
    select * into a from public.budget_approval x
     where x.id = p_approval and x.workspace_id = p_ws and x.status = 'approved'
       and x.brief_id is not distinct from p_brief and x.job_type = p_job_type
       and x.decided_at >= now() - interval '24 hours';
    if found and p_estimate + coalesce((
         select sum(l.cost_usd) from public.usage_ledger l
          where l.approval_id = p_approval
            and (l.status in ('ok', 'rejected')
                 or (l.status = 'reserved' and not exists (
                       select 1 from public.usage_ledger r
                        where r.reservation_id = l.reservation_id and r.status = 'released')))), 0) <= a.estimate_usd then
      v_bypass := p_approval;
    end if;
  end if;

  if v_bypass is null then
    -- Workspace caps first (always), then Brief caps (only when a Brief is given); day before month.
    for v_cap in
      select b.brief_id, b.period, b.cap_usd
      from public.budget b
      where b.workspace_id = p_ws and (b.brief_id is null or b.brief_id = p_brief)
      order by (b.brief_id is not null), b.period
    loop
      v_spent := app.period_spend(p_ws, v_cap.brief_id, v_cap.period);
      if v_spent + p_estimate > v_cap.cap_usd then
        v_reason := case when v_cap.brief_id is null then 'workspace' else 'Brief' end || ' ' || v_cap.period || ' cap';
        insert into public.budget_approval (org_id, workspace_id, brief_id, job_type, job_args, estimate_usd, reason)
        values (v_org, p_ws, p_brief, p_job_type, coalesce(p_job_args, '{}'), p_estimate, v_reason)
        returning id into v_approval;
        perform app.audit(v_org, p_ws, 'budget.approval_requested', 'budget_approval', v_approval, null,
                          jsonb_build_object('job_type', p_job_type, 'estimate_usd', p_estimate, 'reason', v_reason,
                                             'cap_usd', v_cap.cap_usd, 'spent_usd', v_spent));
        return jsonb_build_object('ok', false, 'approval_id', v_approval, 'period', v_cap.period,
                                  'cap', v_cap.cap_usd, 'spent', v_spent);
      end if;
    end loop;
  end if;

  insert into public.usage_ledger (org_id, workspace_id, brief_id, capability, cost_usd, status, reservation_id, approval_id)
  values (v_org, p_ws, p_brief, p_job_type, p_estimate, 'reserved', v_reservation, v_bypass);
  return jsonb_build_object('ok', true, 'reservation_id', v_reservation);
end $$;

-- p_actual: {"capability","provider","model","latency_ms","status":"ok"|"rejected","brief_id","job_id",
--            "lines":[{"unit","quantity","cost_usd"}]}. null = release only.
create function app.settle_reservation(p_reservation uuid, p_actual jsonb) returns void
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
                                       quantity, cost_usd, latency_ms, status, reservation_id, approval_id)
      values (r.org_id, r.workspace_id, coalesce(r.brief_id, (p_actual ->> 'brief_id')::uuid),
              coalesce(p_actual ->> 'job_id', r.job_id), coalesce(p_actual ->> 'capability', r.capability),
              p_actual ->> 'provider', p_actual ->> 'model', (l ->> 'unit')::public.usage_unit,
              coalesce((l ->> 'quantity')::numeric, 0), coalesce((l ->> 'cost_usd')::numeric, 0),
              (p_actual ->> 'latency_ms')::int, v_status, p_reservation, r.approval_id);
    end loop;
  end if;
end $$;

revoke execute on function app.period_start(public.budget_period),
  app.period_spend(uuid, uuid, public.budget_period),
  app.reserve_budget(uuid, uuid, numeric, text, jsonb, uuid),
  app.settle_reservation(uuid, jsonb) from public, anon, authenticated;
grant execute on function app.period_start(public.budget_period),
  app.period_spend(uuid, uuid, public.budget_period),
  app.reserve_budget(uuid, uuid, numeric, text, jsonb, uuid),
  app.settle_reservation(uuid, jsonb) to service_role;

-- ── User-facing writes (audited) ───────────────────────────────────────────
-- A null cap removes the budget. Owner or Admin only.
create function public.set_budget(p_ws uuid, p_brief uuid, p_period public.budget_period, p_cap numeric)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org uuid;
  v_before numeric;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  perform app.require_org_role(v_org, 'Admin', 'set budgets');
  if p_brief is not null and not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception 'that Brief is not in this workspace' using errcode = '22023';
  end if;

  select cap_usd into v_before from public.budget
   where workspace_id = p_ws and brief_id is not distinct from p_brief and period = p_period;

  if p_cap is null then
    delete from public.budget where workspace_id = p_ws and brief_id is not distinct from p_brief and period = p_period;
    perform app.audit(v_org, p_ws, 'budget.removed', 'workspace', p_ws,
                      jsonb_build_object('period', p_period, 'brief_id', p_brief, 'cap_usd', v_before), null);
  else
    insert into public.budget (org_id, workspace_id, brief_id, period, cap_usd, updated_by)
    values (v_org, p_ws, p_brief, p_period, p_cap, app.current_member_id(v_org))
    on conflict on constraint budget_one_per_scope
      do update set cap_usd = excluded.cap_usd, updated_by = excluded.updated_by, updated_at = now();
    perform app.audit(v_org, p_ws, 'budget.set', 'workspace', p_ws,
                      jsonb_build_object('period', p_period, 'brief_id', p_brief, 'cap_usd', v_before),
                      jsonb_build_object('period', p_period, 'brief_id', p_brief, 'cap_usd', p_cap));
  end if;
end $$;

-- Owner/Admin decide any amount; an Editor only up to the workspace's editor approval limit.
-- Operators and Viewers cannot decide. Rejecting is never limited by amount.
create function public.decide_budget_approval(p_id uuid, p_approve boolean) returns void
language plpgsql security definer set search_path = '' as $$
declare
  a public.budget_approval%rowtype;
  v_limit numeric;
begin
  if (select auth.uid()) is null then
    raise exception 'sign in required' using errcode = '28000';
  end if;
  select * into a from public.budget_approval where id = p_id for update;
  if not found then
    raise exception 'approval not found' using errcode = 'P0002';
  end if;
  if not app.has_workspace_role(a.workspace_id, 'Editor') then
    raise exception 'only an Editor, Admin or Owner can decide budget approvals' using errcode = '42501';
  end if;
  if a.status <> 'pending' then
    raise exception 'this approval has already been decided' using errcode = '55000';
  end if;
  if p_approve and not app.has_org_role(a.org_id, 'Admin') then
    select editor_approval_limit_usd into v_limit from public.workspace_settings where workspace_id = a.workspace_id;
    if a.estimate_usd > coalesce(v_limit, 25) then
      raise exception 'this is above your approval limit of % USD: ask an Admin or Owner', coalesce(v_limit, 25)
        using errcode = '42501';
    end if;
  end if;

  update public.budget_approval
     set status = case when p_approve then 'approved'::public.approval_status else 'rejected'::public.approval_status end,
         decided_by = app.current_member_id(a.org_id), decided_at = now()
   where id = p_id;
  perform app.audit(a.org_id, a.workspace_id,
                    case when p_approve then 'budget.approval_approved' else 'budget.approval_rejected' end,
                    'budget_approval', p_id,
                    jsonb_build_object('status', a.status), jsonb_build_object('status', case when p_approve then 'approved' else 'rejected' end,
                                                                               'estimate_usd', a.estimate_usd));
end $$;

-- The narrow window the Settings screen needs: platform-wide health rows are hidden from clients.
create function public.ai_status() returns jsonb
language sql stable security definer set search_path = '' as $$
  select coalesce(
    (select jsonb_build_object(
              'chat', coalesce((h.detail ->> 'chat')::boolean, false),
              'embed', coalesce((h.detail ->> 'embed')::boolean, false),
              'checked_at', h.checked_at)
       from public.health_check h
      where h.component = 'ai_gateway'
      order by h.checked_at desc, h.id desc
      limit 1),
    jsonb_build_object('chat', false, 'embed', false, 'checked_at', null::timestamptz))
$$;

grant execute on function
  public.set_budget(uuid, uuid, public.budget_period, numeric),
  public.decide_budget_approval(uuid, boolean),
  public.ai_status()
to authenticated;
