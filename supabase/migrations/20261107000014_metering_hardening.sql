-- Phase 1a hardening (whole-branch review): dedupe pending approvals (R27), reclaim leaked
-- reservations and expire stale approvals (R28), tighten function grants, and make set_budget
-- stricter. 0012/0013 are already applied; everything here is create-or-replace or additive.

-- ── R27: one pending approval per (workspace, Brief, job type, args) ─────────
-- Body is 0012's reserve_budget; only the refusal branch changed: it reuses a matching pending
-- approval instead of inserting another, and audits only when it inserts.
create or replace function app.reserve_budget(
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
        -- R27: the same pending request (workspace, Brief, job type, args) is reused, not duplicated.
        v_approval := null;
        select x.id into v_approval from public.budget_approval x
         where x.workspace_id = p_ws and x.brief_id is not distinct from p_brief
           and x.job_type = p_job_type and x.job_args = coalesce(p_job_args, '{}')
           and x.status = 'pending'
         order by x.requested_at, x.id
         limit 1;
        if v_approval is null then
          insert into public.budget_approval (org_id, workspace_id, brief_id, job_type, job_args, estimate_usd, reason)
          values (v_org, p_ws, p_brief, p_job_type, coalesce(p_job_args, '{}'), p_estimate, v_reason)
          returning id into v_approval;
          perform app.audit(v_org, p_ws, 'budget.approval_requested', 'budget_approval', v_approval, null,
                            jsonb_build_object('job_type', p_job_type, 'estimate_usd', p_estimate, 'reason', v_reason,
                                               'cap_usd', v_cap.cap_usd, 'spent_usd', v_spent));
        end if;
        return jsonb_build_object('ok', false, 'approval_id', v_approval, 'period', v_cap.period,
                                  'cap', v_cap.cap_usd, 'spent', v_spent);
      end if;
    end loop;
  end if;

  insert into public.usage_ledger (org_id, workspace_id, brief_id, capability, cost_usd, status, reservation_id, approval_id)
  values (v_org, p_ws, p_brief, p_job_type, p_estimate, 'reserved', v_reservation, v_bypass);
  return jsonb_build_object('ok', true, 'reservation_id', v_reservation);
end $$;

revoke execute on function app.reserve_budget(uuid, uuid, numeric, text, jsonb, uuid) from public, anon, authenticated;
grant execute on function app.reserve_budget(uuid, uuid, numeric, text, jsonb, uuid) to service_role;

-- ── set_budget: a null period raises; removing a missing budget writes no audit row ──
create or replace function public.set_budget(p_ws uuid, p_brief uuid, p_period public.budget_period, p_cap numeric)
returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org uuid;
  v_before numeric;
begin
  if p_period is null then
    raise exception 'a budget period is required' using errcode = '22023';
  end if;
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
    if v_before is not null then   -- removing a budget that does not exist is a no-op, not an audit row
      perform app.audit(v_org, p_ws, 'budget.removed', 'workspace', p_ws,
                        jsonb_build_object('period', p_period, 'brief_id', p_brief, 'cap_usd', v_before), null);
    end if;
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

grant execute on function public.set_budget(uuid, uuid, public.budget_period, numeric) to authenticated;

-- ── R28: reclaim leaked reservations, expire old pending approvals ──────────
-- A crash between reserve and settle leaves a `reserved` row that counts as spend forever.
-- Writes the released twin (cost 0, same reservation_id) for each reservation older than
-- p_older_than, per workspace under the same advisory lock as reserve/settle.
create function app.release_stale_reservations(p_older_than interval default '30 minutes')
returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_ws uuid;
  v_n int;
  v_released int := 0;
  v_expired int;
begin
  for v_ws in
    select distinct l.workspace_id from public.usage_ledger l
     where l.status = 'reserved' and l.created_at < now() - p_older_than
       and not exists (select 1 from public.usage_ledger r
                        where r.reservation_id = l.reservation_id and r.status = 'released')
     order by 1
  loop
    perform pg_advisory_xact_lock(hashtextextended(v_ws::text, 0));
    -- Re-checked under the lock: a settle that committed meanwhile already wrote its twin.
    insert into public.usage_ledger (org_id, workspace_id, brief_id, job_id, capability, cost_usd, status, reservation_id, approval_id)
    select l.org_id, l.workspace_id, l.brief_id, l.job_id, l.capability, 0, 'released', l.reservation_id, l.approval_id
      from public.usage_ledger l
     where l.workspace_id = v_ws and l.status = 'reserved' and l.created_at < now() - p_older_than
       and not exists (select 1 from public.usage_ledger r
                        where r.reservation_id = l.reservation_id and r.status = 'released');
    get diagnostics v_n = row_count;
    v_released := v_released + v_n;
  end loop;

  update public.budget_approval set status = 'expired'
   where status = 'pending' and requested_at < now() - interval '7 days';
  get diagnostics v_expired = row_count;

  return jsonb_build_object('released', v_released, 'expired', v_expired);
end $$;

revoke execute on function app.release_stale_reservations(interval) from public, anon, authenticated;
grant execute on function app.release_stale_reservations(interval) to service_role;

insert into public.job_type_config (job_type) values ('budget.release_stale') on conflict do nothing;

-- ── M1: nothing is executable by anon (or PUBLIC) unless granted on purpose ──
revoke execute on function
  public.set_budget(uuid, uuid, public.budget_period, numeric),
  public.decide_budget_approval(uuid, boolean),
  public.ai_status()
from public, anon;
revoke execute on function
  app.audit(uuid, uuid, text, text, uuid, jsonb, jsonb),
  app.record_health(text, text, jsonb, text)
from public;

-- Schema-level `alter default privileges ... revoke` (0001) cannot remove the built-in PUBLIC
-- execute default, so every function created without an explicit revoke is callable by anon.
-- Sweep it once: anon and PUBLIC lose execute on every function in public and app. Explicit
-- grants to authenticated / service_role survive, but anything service_role or authenticated
-- reached only through PUBLIC is re-granted below. New functions are caught by the `pnpm db:check` anon-privilege check.
do $$
declare
  f record;
begin
  for f in
    select p.oid::regprocedure as sig
      from pg_proc p join pg_namespace n on n.oid = p.pronamespace
     where n.nspname in ('public', 'app') and p.prokind = 'f'
       and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
  loop
    execute format('revoke execute on function %s from public, anon', f.sig);
  end loop;
end $$;

-- Re-grants for functions that run as the invoker and were reachable only through PUBLIC:
--   app.is_derivation_writer(): called by the guard_derived / guard_account_stage triggers, which run as
--   the writing role. service_role (app.run_canary inserts into derived tables) needs it to pass the
--   guard; authenticated has no write grants, so granting it only keeps the guard's error clean.
grant execute on function app.is_derivation_writer() to service_role, authenticated;
