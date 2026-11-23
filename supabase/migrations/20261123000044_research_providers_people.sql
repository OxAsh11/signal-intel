-- Migration 0044: provider attempts and cache, people in the operating table (P6).
--   * predicate_run_attempt is the receipt for every provider call in a column run: which vendor,
--     in what order, what it answered and what it cost. app.record_attempt is service-role only;
--     app.finish_run adds attempts to a run's cost alongside settled ledger lines.
--   * provider_cache keeps one PDL company payload per workspace/entity for 30 days, so a second
--     company column on the same account costs nothing.
--   * entity_flags marks a person found in another office; contact_point.masked_hint carries the
--     masked form the table may show (the value itself is Fernet ciphertext, decrypted only in the
--     worker). public.person_email returns the contact row and its mask, never a decrypted value:
--     the full reveal is a worker-backed server action, added with the People page in Task 5.
--   * A person with no works_at link to a company with an account in the workspace queues an
--     unlinked_person Review item. Editor+ can link it; Dismiss skips it.
--   * view_entities and account_table take p_entity_type 'person'. The filter/sort compiler still
--     sees e = the row's entity and a = the company's account, so the same filter AST works in both
--     modes; in person mode e is the person and the company account comes through works_at.
--   * provider_order, provider_status and the six provider prices back Settings -> Providers.
--     Estimates read model_price, never a constant in code.
--   * Re-appliable: create or replace, add column if not exists, drop constraint if exists.

-- ── 1. Alias types for people and sites ───────────────────────────────────────
-- Matching priority in packages/contracts/schema/enums.json is definition order; these are
-- appended, so the JSON list must append them in the same order.
alter type public.alias_type add value if not exists 'email';
alter type public.alias_type add value if not exists 'phone';
alter type public.alias_type add value if not exists 'site';

-- A request to find people is announced in the outbox like a column run; the worker defers one
-- `people.find` job under a people:<account> lock.
alter type public.event_type add value if not exists 'people.find_requested';
insert into public.job_type_config (job_type, enabled) values ('people.find', true)
  on conflict do nothing;

-- ── 2. One row per provider attempt in a run ─────────────────────────────────
create table if not exists public.predicate_run_attempt (
  id             uuid primary key default gen_random_uuid(),
  org_id         uuid not null references public.organisation (id) on delete restrict,
  workspace_id   uuid not null references public.workspace (id) on delete restrict,
  run_id         uuid not null references public.predicate_run (id) on delete cascade,
  ordinal        int not null check (ordinal >= 1),
  provider       text not null check (length(btrim(provider)) > 0),
  status         text not null check (status in ('found', 'not_found', 'error')),
  cost_usd       numeric(14,6) not null default 0 check (cost_usd >= 0),
  confidence     smallint check (confidence between 0 and 100),
  raw_ref        text,
  billable_units int not null default 0 check (billable_units >= 0),
  error          text,
  error_kind     text check (error_kind in ('vendor', 'input')),
  created_at     timestamptz not null default now(),
  constraint predicate_run_attempt_ordinal unique (run_id, ordinal)
);
create index if not exists predicate_run_attempt_ws_idx
  on public.predicate_run_attempt (workspace_id, created_at desc);
create index if not exists predicate_run_attempt_provider_idx
  on public.predicate_run_attempt (provider, created_at desc)
  where status = 'error';

alter table public.predicate_run_attempt enable row level security;
drop policy if exists predicate_run_attempt_read on public.predicate_run_attempt;
create policy predicate_run_attempt_read on public.predicate_run_attempt
  for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.predicate_run_attempt to authenticated;
grant select on public.predicate_run_attempt to service_role;

-- {id: uuid} — one attempt of one provider in a run. An omitted ordinal is the next one.
-- p_error_kind 'input' means the row lacked a name/domain: no call was made and no money was spent.
create or replace function app.record_attempt(
  p_run uuid,
  p_provider text,
  p_status text,
  p_cost_usd numeric default 0,
  p_error text default null,
  p_ordinal int default null,
  p_confidence int default null,
  p_raw_ref text default null,
  p_units int default 0,
  p_error_kind text default null
) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_run public.predicate_run%rowtype;
  v_ord int;
  v_id uuid;
begin
  select * into v_run from public.predicate_run where id = p_run;
  if not found then
    raise exception 'run not found' using errcode = 'P0002';
  end if;
  if p_status is null or p_status not in ('found', 'not_found', 'error') then
    raise exception 'an attempt is found, not_found or error' using errcode = '22023';
  end if;
  if p_error_kind is not null and p_error_kind not in ('vendor', 'input') then
    raise exception 'unknown error kind' using errcode = '22023';
  end if;
  if p_confidence is not null and p_confidence not between 0 and 100 then
    raise exception 'confidence must be 0 to 100' using errcode = '22023';
  end if;
  if p_ordinal is null then
    select coalesce(max(a.ordinal), 0) + 1 into v_ord
      from public.predicate_run_attempt a where a.run_id = p_run;
  else
    v_ord := p_ordinal;
  end if;
  insert into public.predicate_run_attempt
    (org_id, workspace_id, run_id, ordinal, provider, status, cost_usd, confidence, raw_ref,
     billable_units, error, error_kind)
  values
    (v_run.org_id, v_run.workspace_id, p_run, v_ord, p_provider, p_status, coalesce(p_cost_usd, 0),
     p_confidence::smallint, p_raw_ref, coalesce(p_units, 0), p_error, p_error_kind)
  returning id into v_id;
  return v_id;
end $$;

revoke all on function app.record_attempt(uuid, text, text, numeric, text, int, int, text, int, text)
  from public, anon, authenticated;
grant execute on function app.record_attempt(uuid, text, text, numeric, text, int, int, text, int, text)
  to service_role;

-- As 0041, plus the attempts. Provider calls settle their reservation without
-- p_actual.predicate_run_id: the ledger still counts the spend for the workspace budget, while the
-- cell's cost comes from its attempts. No call is counted twice.
create or replace function app.finish_run(p_run uuid, p_status text, p_claim uuid default null, p_inputs jsonb default null,
                                          p_not_run_reason jsonb default null, p_error text default null) returns text
  language plpgsql security definer set search_path = ''
as $$
declare
  r public.predicate_run%rowtype;
  p public.predicate%rowtype;
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
  if p_status = 'error' and (select fill_method from public.predicate where id = r.predicate_id) = 'formula' then
    perform pg_advisory_xact_lock(hashtextextended(r.entity_id::text || ':' || r.predicate_id::text, 0));
    with cell as (
      select c.id, first_value(c.id) over (order by c.created_at desc, c.id desc) as keep
        from public.claim c
       where c.entity_id = r.entity_id and c.predicate_id = r.predicate_id and c.status = 'active'
         and c.source_record_id = app.derived_source_record(r.workspace_id, 'formula'))
    update public.claim c set status = 'superseded', superseded_by = cell.keep
      from cell where c.id = cell.id and cell.id <> cell.keep;
  end if;
  update public.predicate_run
     set status = p_status,
         finished_at = now(),
         claim_id = case when p_status = 'succeeded' then coalesce(p_claim, claim_id) end,
         inputs = p_inputs,
         not_run_reason = p_not_run_reason,
         error = case when p_status = 'error' then left(coalesce(p_error, 'error'), 2000) end,
         cost_usd = (select coalesce(sum(l.cost_usd), 0) from public.usage_ledger l where l.predicate_run_id = p_run)
                  + (select coalesce(sum(a.cost_usd), 0) from public.predicate_run_attempt a where a.run_id = p_run)
   where id = p_run;

  select * into p from public.predicate where id = r.predicate_id;
  if p.fill_method = 'formula' and p.auto_run and r.inputs_hash is not null
     and r.inputs_hash is distinct from app.run_inputs_hash(r.entity_id, r.predicate_id)
     and exists (select 1 from public.account a where a.entity_id = r.entity_id and a.workspace_id = r.workspace_id) then
    perform app.queue_cell_runs(r.predicate_id, r.workspace_id, array[r.entity_id], r.brief_version_id,
                                'predicate_run', p_run);
  end if;
  return p_status;
end $$;

-- ── 3. Provider payload cache ────────────────────────────────────────────────
-- Company facts only. No person data is ever cached here.
create table if not exists public.provider_cache (
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  provider     text not null,
  entity_id    uuid not null references public.entity (id) on delete cascade,
  payload_ref  text,
  payload      jsonb not null,
  fetched_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  primary key (workspace_id, provider, entity_id)
);
alter table public.provider_cache enable row level security;
drop policy if exists provider_cache_read on public.provider_cache;
create policy provider_cache_read on public.provider_cache
  for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.provider_cache to authenticated;
grant select on public.provider_cache to service_role;

-- jsonb | null — a fresh payload, or null when there is no entry or it has expired.
create or replace function app.cached_provider_payload(p_ws uuid, p_provider text, p_entity uuid) returns jsonb
  language sql stable security definer set search_path = ''
as $$
  select payload from public.provider_cache
   where workspace_id = p_ws and provider = p_provider and entity_id = p_entity and expires_at > now()
$$;

-- void — one company payload per workspace/provider/entity, valid for p_ttl_days (default 30).
create or replace function app.store_provider_payload(
  p_ws uuid, p_provider text, p_entity uuid, p_payload jsonb,
  p_ref text default null, p_ttl_days int default 30
) returns void
  language plpgsql security definer set search_path = ''
as $$
begin
  if p_ttl_days is null or p_ttl_days < 1 then
    raise exception 'a cache TTL must be at least one day' using errcode = '22023';
  end if;
  insert into public.provider_cache (workspace_id, provider, entity_id, payload_ref, payload, fetched_at, expires_at)
  values (p_ws, p_provider, p_entity, p_ref, p_payload, now(), now() + make_interval(days => p_ttl_days))
  on conflict (workspace_id, provider, entity_id) do update
     set payload_ref = excluded.payload_ref, payload = excluded.payload,
         fetched_at = now(), expires_at = now() + make_interval(days => p_ttl_days);
end $$;

revoke all on function app.cached_provider_payload(uuid, text, uuid) from public, anon, authenticated;
revoke all on function app.store_provider_payload(uuid, text, uuid, jsonb, text, int) from public, anon, authenticated;
grant execute on function app.cached_provider_payload(uuid, text, uuid) to service_role;
grant execute on function app.store_provider_payload(uuid, text, uuid, jsonb, text, int) to service_role;

-- ── 4. Person flags ──────────────────────────────────────────────────────────
create table if not exists public.entity_flag (
  id           uuid primary key default gen_random_uuid(),
  entity_id    uuid not null references public.entity (id) on delete cascade,
  workspace_id uuid not null references public.workspace (id) on delete cascade,
  kind         text not null check (kind in ('location_mismatch')),
  reason       text not null check (length(btrim(reason)) between 1 and 500),
  created_at   timestamptz not null default now(),
  constraint entity_flag_one unique (entity_id, workspace_id, kind)
);
alter table public.entity_flag enable row level security;
drop policy if exists entity_flag_read on public.entity_flag;
create policy entity_flag_read on public.entity_flag
  for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.entity_flag to authenticated;
grant select on public.entity_flag to service_role;

create or replace function app.set_entity_flag(
  p_entity uuid, p_ws uuid, p_kind text, p_reason text
) returns void
  language plpgsql security definer set search_path = ''
as $$
begin
  if p_kind is distinct from 'location_mismatch' then
    raise exception 'unknown entity flag' using errcode = '22023';
  end if;
  if not exists (select 1 from public.entity where id = p_entity and entity_type = 'person') then
    raise exception 'flags name a person entity' using errcode = '22023';
  end if;
  insert into public.entity_flag (entity_id, workspace_id, kind, reason)
  values (p_entity, p_ws, p_kind, left(btrim(p_reason), 500))
  on conflict (entity_id, workspace_id, kind) do update set reason = excluded.reason;
end $$;
revoke all on function app.set_entity_flag(uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function app.set_entity_flag(uuid, uuid, text, text) to service_role;

-- ── 5. Masked contact hint and the panel RPC ────────────────────────────────
-- The encrypted value never leaves the worker. Writers pass the masked form (j•••@acme.com) with
-- the value so the operating table can show it without a decryption path in SQL or the browser.
alter table public.contact_point add column if not exists masked_hint text;

-- {contact_point_id, masked, status, verified_at, provider, reveal: 'worker'} for Editor+.
-- There is deliberately no decrypted value here: Fernet keys live only in the worker environment,
-- so the panel's full reveal is a worker-backed server action (Task 5). Viewer gets 42501.
create or replace function public.person_email(p_ws uuid, p_person uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org uuid;
  v_out jsonb;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'insufficient role' using errcode = '42501';
  end if;
  if not exists (select 1 from public.entity
                  where id = p_person and entity_type = 'person' and org_id = v_org) then
    raise exception 'not a person of this workspace' using errcode = '22023';
  end if;
  select jsonb_build_object(
           'contact_point_id', cp.id,
           'masked', coalesce(cp.masked_hint, left(coalesce(cp.value_hash, ''), 1) || '•••'),
           'status', cp.status,
           'verified_at', cp.verified_at,
           'provider', cp.provider,
           'reveal', 'worker')
    into v_out
    from public.contact_point cp
   where cp.workspace_id = p_ws and cp.entity_id = p_person and cp.type = 'email'
   order by (cp.status = 'valid') desc, cp.verified_at desc nulls last, cp.created_at desc
   limit 1;
  return v_out;
end $$;

-- ── 6. Unlinked people go to Review ─────────────────────────────────────────
alter table public.review_item add column if not exists person_entity_id uuid references public.entity (id);
create index if not exists review_item_person_idx
  on public.review_item (person_entity_id) where person_entity_id is not null;

alter table public.review_item drop constraint if exists review_item_type_check;
alter table public.review_item add constraint review_item_type_check
  check (type in ('identity', 'claim_wrong', 'unlinked_person'));
alter table public.review_item drop constraint if exists review_item_type_fields;
alter table public.review_item add constraint review_item_type_fields check (
  (type = 'identity' and mention_id is not null)
  or (type = 'claim_wrong' and claim_id is not null and reason is not null and length(reason) between 1 and 1000)
  or (type = 'unlinked_person' and person_entity_id is not null)
);
alter table public.review_item drop constraint if exists review_item_status_check;
alter table public.review_item add constraint review_item_status_check
  check (status in ('pending', 'merged', 'skipped', 'created_new', 'restored', 'linked'));

-- uuid | null — null when the person is already linked or a review item is already pending.
-- p_ws defaults to the person's contact-point workspace, then the org's first workspace.
create or replace function app.queue_unlinked_person(p_person uuid, p_ws uuid default null) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_e   public.entity%rowtype;
  v_ws  uuid := p_ws;
  v_id  uuid;
begin
  select * into v_e from public.entity where id = p_person;
  if not found or v_e.entity_type <> 'person' then
    raise exception 'only a person can be queued as unlinked' using errcode = '22023';
  end if;
  if v_ws is null then
    select workspace_id into v_ws from public.contact_point
     where entity_id = p_person order by created_at desc limit 1;
  end if;
  if v_ws is null then
    select id into v_ws from public.workspace where org_id = v_e.org_id order by created_at, id limit 1;
  end if;
  if v_ws is null or (select org_id from public.workspace where id = v_ws) <> v_e.org_id then
    raise exception 'no workspace of this organisation for the person' using errcode = '23514';
  end if;
  if exists (select 1 from public.relationship r
               join public.account a on a.entity_id = r.to_entity_id and a.workspace_id = v_ws
              where r.from_entity_id = p_person and r.relation = 'works_at' and r.valid_to is null) then
    return null;
  end if;
  select id into v_id from public.review_item
   where type = 'unlinked_person' and person_entity_id = p_person and status = 'pending' limit 1;
  if found then
    return v_id;
  end if;
  insert into public.review_item (org_id, workspace_id, type, person_entity_id, reason)
  values (v_e.org_id, v_ws, 'unlinked_person', p_person, 'Every person must be linked to an account')
  returning id into v_id;
  perform app.audit(v_e.org_id, v_ws, 'review.unlinked_person_queued', 'entity', p_person, null,
                    jsonb_build_object('review_item_id', v_id));
  return v_id;
end $$;
revoke all on function app.queue_unlinked_person(uuid, uuid) from public, anon, authenticated;
grant execute on function app.queue_unlinked_person(uuid, uuid) to service_role;

-- One internal link, used by both the direct RPC and Review's Link action.
create or replace function app.link_person_internal(
  p_org uuid, p_ws uuid, p_person uuid, p_account uuid, p_member uuid
) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_acc public.account%rowtype;
begin
  select * into v_acc from public.account where id = p_account and workspace_id = p_ws;
  if not found then
    raise exception 'account is not in this workspace' using errcode = '22023';
  end if;
  if not exists (select 1 from public.relationship r
                   where r.from_entity_id = p_person and r.to_entity_id = v_acc.entity_id
                     and r.relation = 'works_at' and r.valid_to is null) then
    insert into public.relationship
      (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes, scope)
    values
      (p_org, p_person, 'works_at', v_acc.entity_id, app.derived_source_record(p_ws, 'manual'), 100,
       jsonb_build_object('linked_by', 'manual'), 'tenant');
  end if;
  update public.review_item
     set status = 'linked', resolved_by = p_member, resolved_at = now()
   where type = 'unlinked_person' and person_entity_id = p_person
     and workspace_id = p_ws and status = 'pending';
  perform app.audit(p_org, p_ws, 'person.linked_to_account', 'entity', p_person, null,
                    jsonb_build_object('account_id', p_account, 'member_id', p_member));
end $$;
revoke all on function app.link_person_internal(uuid, uuid, uuid, uuid, uuid) from public, anon, authenticated;
grant execute on function app.link_person_internal(uuid, uuid, uuid, uuid, uuid) to service_role;

-- Editor+ links a person to an account of the workspace; the Review item, if any, resolves too.
create or replace function public.link_person_to_account(p_ws uuid, p_person uuid, p_account uuid) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org uuid;
  v_member uuid;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'insufficient role' using errcode = '42501';
  end if;
  if not exists (select 1 from public.entity
                  where id = p_person and entity_type = 'person' and org_id = v_org) then
    raise exception 'not a person of this workspace' using errcode = '22023';
  end if;
  v_member := app.current_member_id(v_org);
  perform app.link_person_internal(v_org, p_ws, p_person, p_account, v_member);
end $$;

-- As 0043, plus Link/Dismiss for an unlinked_person item. p_account carries the account for Link.
-- The implementation has its own name so the existing 2- and 3-argument entry points keep working;
-- changing an existing function's arity would create an overload and leave old callers on the old
-- body. Both entry points delegate here below.
create or replace function app.resolve_review_item_v2(
  p_id uuid,
  p_action text,
  p_new_entity_name text,
  p_account uuid
) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_item        public.review_item;
  v_new_entity  uuid;
  v_entity_type public.entity_type;
  v_prov_entity uuid;
  v_member_id   uuid;
  v_claim       public.claim;
begin
  if p_action is null or p_action not in ('merge', 'skip', 'create_new', 'restore', 'link') then
    raise exception 'invalid action: %', p_action using errcode = '22023';
  end if;

  select * into v_item from public.review_item where id = p_id;
  if not found then
    raise exception 'review_item not found' using errcode = 'P0002';
  end if;
  if v_item.status <> 'pending' then
    raise exception 'review_item already resolved' using errcode = '23514';
  end if;
  if not app.has_workspace_role(v_item.workspace_id, 'Editor') then
    raise exception 'insufficient role' using errcode = '42501';
  end if;
  v_member_id := app.current_member_id(v_item.org_id);

  -- An unlinked person: Link (with p_account) or Dismiss (skip).
  if v_item.type = 'unlinked_person' then
    if p_action = 'link' then
      if p_account is null then
        raise exception 'link needs an account' using errcode = '22023';
      end if;
      perform app.link_person_internal(v_item.org_id, v_item.workspace_id,
                                       v_item.person_entity_id, p_account, v_member_id);
    elsif p_action = 'skip' then
      update public.review_item
         set status = 'skipped', resolved_by = v_member_id, resolved_at = now()
       where id = p_id;
      perform app.audit(v_item.org_id, v_item.workspace_id, 'person.unlinked_dismissed',
                        'entity', v_item.person_entity_id, null,
                        jsonb_build_object('review_item_id', p_id, 'member_id', v_member_id));
    else
      raise exception 'an unlinked person is linked (link) or dismissed (skip), not %', p_action
        using errcode = '22023';
    end if;
    return;
  end if;

  -- A claim marked wrong.
  if v_item.type = 'claim_wrong' then
    if p_action not in ('skip', 'restore') then
      raise exception 'a wrong-claim item is dismissed (skip) or restored, not %', p_action using errcode = '22023';
    end if;
    if p_action = 'restore' then
      select * into v_claim from public.claim where id = v_item.claim_id for update;
      if v_claim.status = 'retracted' then
        update public.claim set status = 'active' where id = v_claim.id;
        perform app.emit('claim.created'::public.event_type, v_item.org_id, null, 'claim', v_claim.id,
                         jsonb_build_object('entity_id', v_claim.entity_id, 'predicate_id', v_claim.predicate_id,
                                            'source_record_id', v_claim.source_record_id));
        if exists (select 1 from public.source_record sr
                    where sr.id = v_claim.source_record_id and sr.connector_id = 'derived:manual') then
          update public.claim set status = 'superseded', superseded_by = v_claim.id
           where entity_id = v_claim.entity_id and predicate_id = v_claim.predicate_id
             and source_record_id = v_claim.source_record_id and status = 'active' and id <> v_claim.id;
        end if;
      end if;
    end if;
    update public.review_item
       set status = case p_action when 'restore' then 'restored' else 'skipped' end,
           resolved_by = v_member_id, resolved_at = now()
     where id = p_id;
    perform app.audit(v_item.org_id, v_item.workspace_id,
                      case p_action when 'restore' then 'claim.restored' else 'claim.wrong_dismissed' end,
                      'claim', v_item.claim_id, null, jsonb_build_object('review_item_id', p_id));
    return;
  end if;

  -- An identity decision (as 0034).
  if p_action in ('restore', 'link') then
    raise exception 'an identity item is merged, skipped or created new, not %', p_action using errcode = '22023';
  end if;
  if p_action = 'merge' then
    if v_item.candidate_entity is null then
      raise exception 'review_item has no candidate entity for merge' using errcode = '22023';
    end if;
    if (select org_id from public.entity where id = v_item.candidate_entity) <> v_item.org_id then
      raise exception 'candidate entity belongs to a different organisation' using errcode = '23514';
    end if;
  end if;

  select entity_id into v_prov_entity from public.mention where id = v_item.mention_id;

  if p_action = 'merge' then
    if v_prov_entity is not null and v_prov_entity <> v_item.candidate_entity then
      perform app.merge_entities(v_item.candidate_entity, v_prov_entity, v_member_id);
    end if;
    perform app.resolve_mention(v_item.mention_id, v_item.candidate_entity, 'manual_merge', 100::smallint);
    perform app.create_account(v_item.org_id, v_item.workspace_id, v_item.candidate_entity);

  elsif p_action = 'create_new' then
    select entity_type into v_entity_type from public.mention where id = v_item.mention_id;
    v_new_entity := app.create_entity(v_item.org_id, v_entity_type,
      coalesce(p_new_entity_name, (select raw_name from public.mention where id = v_item.mention_id)));
    if v_prov_entity is not null and v_prov_entity <> v_new_entity then
      perform app.merge_entities(v_new_entity, v_prov_entity, v_member_id);
    end if;
    perform app.resolve_mention(v_item.mention_id, v_new_entity, 'manual_create', 100::smallint);
    perform app.create_account(v_item.org_id, v_item.workspace_id, v_new_entity);
  end if;

  update public.review_item
  set status = case p_action
                 when 'merge'      then 'merged'
                 when 'skip'       then 'skipped'
                 when 'create_new' then 'created_new'
               end,
      resolved_by = v_member_id,
      resolved_at = now()
  where id = p_id;

  if p_action <> 'skip' and v_member_id is not null then
    insert into public.feedback (org_id, workspace_id, member_id, target_type, target_id, action, reason)
    values (v_item.org_id, v_item.workspace_id, v_member_id, 'identity_match', p_id,
            case p_action when 'merge' then 'confirm' else 'reject' end,
            case p_action when 'merge' then 'same company' else 'different company' end);
  end if;
end;
$$;

-- Existing entry points keep their signatures and grants; a new four-argument form carries the
-- account for Link. An unlinked-person item needs p_account, so only that form accepts 'link'.
create or replace function app.resolve_review_item(
  p_id uuid, p_action text, p_new_entity_name text default null
) returns void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT app.resolve_review_item_v2(p_id, p_action, p_new_entity_name, null);
$$;
create or replace function app.resolve_review_item(
  p_id uuid, p_action text, p_new_entity_name text, p_account uuid
) returns void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT app.resolve_review_item_v2(p_id, p_action, p_new_entity_name, p_account);
$$;

create or replace function public.resolve_review_item(
  p_id uuid, p_action text, p_new_entity_name text default null
) returns void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT app.resolve_review_item(p_id, p_action, p_new_entity_name);
$$;
create or replace function public.resolve_review_item(
  p_id uuid, p_action text, p_new_entity_name text, p_account uuid
) returns void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT app.resolve_review_item(p_id, p_action, p_new_entity_name, p_account);
$$;

-- ── 6b. Find people: receipt, estimate and request ───────────────────────────
-- A person found by PDL is matched by profile URL or email digest before a new entity is made.
-- The email value stays encrypted (and the digest opaque); only the web panel's worker-backed
-- reveal can decrypt it. Every found person gets a works_at link, a source receipt and the aliases
-- the People page needs. The worker flags a location mismatch separately.
create or replace function app.upsert_found_person(
  p_org uuid, p_ws uuid, p_account uuid, p_name text, p_title text, p_persona text,
  p_profile_url text, p_email_encrypted text, p_email_hash text, p_email_masked text,
  p_location jsonb, p_source uuid
) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_company uuid;
  v_person  uuid;
  v_cp      uuid;
  v_country text := nullif(btrim(coalesce(p_location ->> 'country', '')), '');
  v_city    text := nullif(btrim(coalesce(p_location ->> 'locality', '')), '');
begin
  select a.entity_id into v_company from public.account a
   where a.id = p_account and a.org_id = p_org and a.workspace_id = p_ws;
  if v_company is null then
    raise exception 'account % is not in this workspace', p_account using errcode = '42501';
  end if;

  if p_email_hash is not null then
    select cp.entity_id into v_person from public.contact_point cp
     where cp.org_id = p_org and cp.type = 'email' and cp.value_hash = p_email_hash limit 1;
  end if;
  if v_person is null and p_profile_url is not null then
    select ea.entity_id into v_person from public.entity_alias ea
     where ea.org_id = p_org and ea.alias_type = 'profile_url'
       and ea.value_normalised = p_profile_url limit 1;
  end if;
  if v_person is null then
    v_person := app.create_entity(p_org, 'person'::public.entity_type,
                                  coalesce(nullif(btrim(p_name), ''), 'Unknown contact'));
  end if;

  if not exists (select 1 from public.relationship r
                  where r.from_entity_id = v_person and r.to_entity_id = v_company
                    and r.relation = 'works_at' and r.valid_to is null) then
    insert into public.relationship
      (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes, scope)
    values
      (p_org, v_person, 'works_at', v_company, p_source, 80,
       jsonb_strip_nulls(jsonb_build_object('title', p_title, 'persona', p_persona,
                                            'discovered_by', 'pdl_person')), 'tenant');
  end if;

  if p_profile_url is not null then
    perform app.create_entity_alias(p_org, v_person, 'profile_url'::public.alias_type,
                                    p_profile_url, 80::smallint, p_source);
  end if;
  if p_email_hash is not null then
    perform app.create_entity_alias(p_org, v_person, 'email'::public.alias_type,
                                    p_email_hash, 80::smallint, p_source);
    select cp.id into v_cp from public.contact_point cp
     where cp.org_id = p_org and cp.type = 'email' and cp.value_hash = p_email_hash limit 1;
    if v_cp is null then
      insert into public.contact_point
        (org_id, workspace_id, entity_id, account_id, type, value_encrypted, value_hash,
         masked_hint, status, provider)
      values
        (p_org, p_ws, v_person, p_account, 'email', p_email_encrypted, p_email_hash,
         p_email_masked, 'unknown', 'pdl_person');
    end if;
  end if;
  if v_country is not null then
    perform app.create_entity_alias(p_org, v_person, 'name_country'::public.alias_type,
                                    coalesce(nullif(btrim(p_name), ''), 'Unknown contact') || ', ' || v_country,
                                    70::smallint, p_source);
  end if;
  if v_city is not null then
    perform app.create_entity_alias(p_org, v_person, 'site'::public.alias_type,
                                    v_city || coalesce(', ' || v_country, ''), 70::smallint, p_source);
  end if;
  return v_person;
end $$;
revoke all on function app.upsert_found_person(uuid, uuid, uuid, text, text, text, text, text, text, text, jsonb, uuid)
  from public, anon, authenticated;
grant execute on function app.upsert_found_person(uuid, uuid, uuid, text, text, text, text, text, text, text, jsonb, uuid)
  to service_role;

-- {count, per_person_usd, cost_usd, remaining_usd, within_budget, max_per_request}. The price is
-- read from model_price, never hard-coded here; the limit is 1–10 and defaults to 5 people.
create or replace function public.estimate_find_people(p_ws uuid, p_account uuid, p_limit int default 5)
returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_per  numeric;
  v_left numeric;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > 10 then
    raise exception 'find people takes a limit of 1 to 10' using errcode = '22023';
  end if;
  if not exists (select 1 from public.account where id = p_account and workspace_id = p_ws) then
    raise exception 'account not found in this workspace' using errcode = '22023';
  end if;
  select usd_per_million / 1000000 into v_per from public.model_price
   where model = 'pdl_person' and unit = 'enrichment_lookup';
  v_per := coalesce(v_per, 0);
  v_left := app.remaining_budget_usd(p_ws, null);
  return jsonb_build_object(
    'count', p_limit,
    'per_person_usd', v_per,
    'cost_usd', p_limit * v_per,
    'remaining_usd', v_left,
    'within_budget', least(10, case when v_left is null or v_per = 0 then p_limit
                                    else least(p_limit, floor(v_left / v_per)::int) end),
    'max_per_request', 10);
end $$;

-- Editor+: checks the same budget the worker will reserve, then announces one locked job on the
-- outbox. The P3 55000 message names the largest N that fits, never more than the asked limit.
create or replace function public.request_find_people(p_ws uuid, p_account uuid, p_limit int default 5,
                                                     p_brief uuid default null)
returns jsonb
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_bv    uuid;
  v_per   numeric;
  v_cost  numeric;
  v_left  numeric;
  v_n     int;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_limit is null or p_limit < 1 or p_limit > 10 then
    raise exception 'find people takes a limit of 1 to 10' using errcode = '22023';
  end if;
  if not exists (select 1 from public.account where id = p_account and workspace_id = p_ws) then
    raise exception 'account not found in this workspace' using errcode = '22023';
  end if;

  select usd_per_million / 1000000 into v_per from public.model_price
   where model = 'pdl_person' and unit = 'enrichment_lookup';
  v_per := coalesce(v_per, 0);
  v_cost := p_limit * v_per;
  v_left := app.remaining_budget_usd(p_ws, null);
  if v_left is not null and v_cost > v_left then
    v_n := least(p_limit, floor(v_left / v_per)::int);
    raise exception 'this run needs $% but $% is left in the budget; find the first % within budget',
      app.usd_text(v_cost), app.usd_text(v_left), v_n using errcode = '55000';
  end if;

  v_bv := app.home_lens(p_ws, p_brief);
  perform app.emit('people.find_requested'::public.event_type, v_org, p_ws, 'account', p_account,
                   jsonb_build_object('account_id', p_account, 'workspace_id', p_ws,
                                      'org_id', v_org, 'limit', p_limit, 'brief_id', v_bv));
  perform app.audit(v_org, p_ws, 'people.find_requested', 'account', p_account, null,
                    jsonb_build_object('limit', p_limit, 'cost_usd', v_cost, 'brief_id', v_bv));
  return jsonb_build_object('queued', 1, 'cost_usd', v_cost, 'brief_id', v_bv);
end $$;

-- ── 7. Provider order and status ────────────────────────────────────────────
alter table public.workspace_settings
  add column if not exists provider_order jsonb not null default '["leadmagic","findymail","prospeo"]'::jsonb;

-- Admin only. The list is a jsonb array of known keys, without duplicates.
create or replace function public.set_provider_order(p_ws uuid, p_order jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org uuid;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Admin') then
    raise exception 'insufficient role' using errcode = '42501';
  end if;
  if jsonb_typeof(p_order) <> 'array' or jsonb_array_length(p_order) < 1 then
    raise exception 'provider order must be a non-empty list' using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_array_elements_text(p_order) k
              where k.value not in ('leadmagic', 'findymail', 'prospeo')) then
    raise exception 'unknown provider in the order' using errcode = '22023';
  end if;
  if (select count(*) from jsonb_array_elements_text(p_order)) <>
     (select count(distinct value) from jsonb_array_elements_text(p_order)) then
    raise exception 'a provider may appear once' using errcode = '22023';
  end if;
  update public.workspace_settings set provider_order = p_order, updated_at = now()
   where workspace_id = p_ws;
  perform app.audit(v_org, p_ws, 'provider.order_set', 'workspace', p_ws, null,
                    jsonb_build_object('provider_order', p_order));
end $$;

-- [{key, kind, price_usd, month_spend_usd, attempts, last_error, last_error_at}]
-- Key configured/last-error state is merged in by the web from the worker's /providers/status.
create or replace function public.provider_status(p_ws uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org uuid;
  v_out jsonb;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select coalesce(jsonb_agg(jsonb_build_object(
           'key', s.key, 'kind', s.kind, 'price_usd', s.price_usd,
           'month_spend_usd', coalesce(a.spend, 0), 'attempts', coalesce(a.n, 0),
           'last_error', a.last_error, 'last_error_at', a.last_error_at)
         order by s.ord), '[]'::jsonb)
    into v_out
    from (values
      ('leadmagic', 'email_finder', 0.012::numeric, 1),
      ('findymail', 'email_finder', 0.015::numeric, 2),
      ('findymail_verify', 'email_verifier', 0.004::numeric, 3),
      ('prospeo', 'email_finder', 0.010::numeric, 4),
      ('pdl_company', 'company', 0.10::numeric, 5),
      ('pdl_person', 'person_search', 0.28::numeric, 6)
    ) s(key, kind, price_usd, ord)
    left join lateral (
      select sum(t.cost_usd) as spend, count(*) as n,
             (array_agg(t.error order by t.created_at desc, t.id desc)
                filter (where t.error is not null))[1] as last_error,
             max(t.created_at) filter (where t.error is not null) as last_error_at
        from public.predicate_run_attempt t
       where t.workspace_id = p_ws and t.provider = s.key
         and t.created_at >= date_trunc('month', now())
    ) a on true;
  return v_out;
end $$;

-- ── 8. Provider prices (editable in Settings; estimates read this table) ────
insert into public.model_price (model, provider, unit, usd_per_million) values
  ('leadmagic',        'leadmagic', 'enrichment_lookup',  12000.0000),
  ('findymail',        'findymail', 'enrichment_lookup',  15000.0000),
  ('findymail_verify', 'findymail', 'enrichment_lookup',   4000.0000),
  ('prospeo',          'prospeo',   'enrichment_lookup',  10000.0000),
  ('pdl_company',      'pdl',       'enrichment_lookup', 100000.0000),
  ('pdl_person',       'pdl',       'enrichment_lookup', 280000.0000)
on conflict (model, unit) do nothing;

-- ── 9. People in the view compiler ──────────────────────────────────────────
-- e is always the row's entity and a its company account, so the filter AST and sort compiler are
-- unchanged. Person mode joins through works_at and returns one row per person (the newest link).
create or replace function public.view_entities(
  p_ws uuid, p_brief uuid, p_filter jsonb, p_sort jsonb, p_limit int, p_offset int,
  p_entity_type public.entity_type default 'company'
) returns table (entity_id uuid, total bigint)
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_limit  int := coalesce(p_limit, 100);
  v_offset int := coalesce(p_offset, 0);
  v_where  text;
  v_order  text;
  v_org    uuid;
begin
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_entity_type not in ('company', 'person') then
    raise exception 'only company and person views are available' using errcode = '22023';
  end if;
  if v_limit > 500 then
    raise exception 'a page has at most 500 rows' using errcode = '22023';
  elsif v_limit < 1 then
    raise exception 'a page has at least 1 row' using errcode = '22023';
  elsif v_offset < 0 then
    raise exception 'offset must be 0 or more' using errcode = '22023';
  end if;
  v_where := app.filter_where(p_ws, p_brief, p_filter, 'filter');
  v_order := app.view_order(p_ws, p_brief, p_sort);
  if p_entity_type = 'person' then
    return query execute format(
      'select e.id, count(*) over () from public.entity e'
      ' join lateral (select r.to_entity_id from public.relationship r'
      '               where r.from_entity_id = e.id and r.relation = ''works_at'' and r.valid_to is null'
      '               order by r.created_at desc, r.id desc limit 1) w on true'
      ' join public.entity ce on ce.id = w.to_entity_id and ce.entity_type = ''company'' and ce.status = ''active'''
      ' join public.account a on a.workspace_id = $1 and a.entity_id = ce.id'
      ' where e.org_id = $4 and e.entity_type = ''person'' and e.status = ''active'' and %s'
      ' order by %s limit $2 offset $3', v_where, v_order)
      using p_ws, v_limit, v_offset, v_org;
  else
    return query execute format(
      'select e.id, count(*) over () from public.account a join public.entity e on e.id = a.entity_id'
      ' where a.workspace_id = $1 and e.org_id = $4 and e.entity_type = ''company'' and e.status = ''active'' and %s'
      ' order by %s limit $2 offset $3', v_where, v_order)
      using p_ws, v_limit, v_offset, v_org;
  end if;
end $$;

-- ── 10. The operating table in person mode ──────────────────────────────────
-- Drop the 8-argument form so callers with seven named arguments resolve to this one, not an
-- overload. The new p_entity_type has a default, so existing callers need no change.
drop function if exists public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int);
create or replace function public.account_table(
  p_ws uuid, p_brief uuid, p_filter jsonb, p_sort jsonb, p_columns uuid[],
  p_limit int default 50, p_offset int default 0,
  p_entity_type public.entity_type default 'company'
) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief   uuid := app.home_lens(p_ws, p_brief);
  v_limit   int := coalesce(p_limit, 50);
  v_offset  int := coalesce(p_offset, 0);
  v_vers    uuid[];
  v_cols    uuid[];
  v_ids     uuid[];
  v_total   bigint;
  v_cells   jsonb := '{}'::jsonb;
  v_rows    jsonb;
  v_columns jsonb;
begin
  if v_limit > 200 then
    raise exception 'a page has at most 200 rows' using errcode = '22023';
  elsif v_limit < 1 then
    raise exception 'a page has at least 1 row' using errcode = '22023';
  elsif v_offset < 0 then
    raise exception 'offset must be 0 or more' using errcode = '22023';
  end if;
  if p_entity_type not in ('company', 'person') then
    raise exception 'only company and person tables are available' using errcode = '22023';
  end if;
  if coalesce(cardinality(p_columns), 0) > 50 then
    raise exception 'a page shows at most 50 columns' using errcode = '22023';
  end if;

  v_cols := case
    when p_columns is null then case when v_brief is null then '{}'::uuid[] else app.table_brief_columns(p_ws, v_brief) end
    else (select coalesce(array_agg(p.id order by u.o), '{}')
            from (select t.id, min(t.o) as o from unnest(p_columns) with ordinality t(id, o) group by t.id) u
            join public.predicate p on p.id = u.id and (p.workspace_id = p_ws or p.workspace_id is null)) end;

  select coalesce(array_agg(ve.entity_id order by ve.n), '{}'), max(ve.total)
    into v_ids, v_total
    from public.view_entities(p_ws, v_brief, p_filter, p_sort, v_limit, v_offset, p_entity_type)
         with ordinality ve(entity_id, total, n);
  if v_total is null and v_offset > 0 then
    select max(v.total) into v_total
      from public.view_entities(p_ws, v_brief, p_filter, p_sort, 1, 0, p_entity_type) v;
  end if;

  if cardinality(v_ids) > 0 and cardinality(v_cols) > 0 then
    select coalesce(jsonb_object_agg(x.entity_id::text, x.cells), '{}'::jsonb) into v_cells
      from (select c.entity_id,
                   jsonb_object_agg(c.predicate_id::text,
                                    jsonb_build_object('state', c.state, 'value', c.value, 'confidence', c.confidence,
                                                       'claim_id', c.claim_id, 'run_id', c.run_id, 'error', c.error)) as cells
              from public.cells(p_ws, v_ids, v_cols) c
             group by c.entity_id) x;
  end if;

  v_vers := app.home_lens_versions(p_ws, v_brief);
  select coalesce(jsonb_agg((
           jsonb_build_object(
             'account_id', a.id, 'entity_id', e.id, 'name', e.display_name,
             'country', (select btrim(m.raw_country) from public.mention m
                           where m.entity_id = e.id and btrim(coalesce(m.raw_country, '')) <> ''
                           order by m.created_at desc, m.id desc limit 1),
             'domain', (select al.value_normalised from public.entity_alias al
                          where al.entity_id = e.id and al.alias_type = 'domain'
                          order by al.confidence desc, al.created_at, al.id limit 1),
             'verdict', vd.value, 'band', sc.band, 'priority', sc.priority, 'stage', a.stage,
             'owner', case when om.id is not null
                            then jsonb_build_object('member_id', om.id, 'name', coalesce(nullif(btrim(om.display_name), ''), om.email)) end,
             'last_signal', (select max(c.observed_at) from public.claim c
                               join public.source_record sr on sr.id = c.source_record_id
                              where c.entity_id = e.id and c.status <> 'retracted' and sr.connector_id <> 'derived:formula'),
             'cells', coalesce(v_cells -> e.id::text, '{}'::jsonb))
           -- Company rows keep the exact P5 shape; the six People fields are person-mode only.
           || case when p_entity_type = 'person' then jsonb_build_object(
                'account_name', a.account_name,
                'account_entity_id', a.account_entity_id,
                'title', a.title,
                'work_email', a.masked_email,
                'linkedin', (select al.value_normalised from public.entity_alias al
                               where al.entity_id = e.id and al.alias_type = 'profile_url'
                               order by al.confidence desc, al.created_at, al.id limit 1),
                'location_mismatch', coalesce(a.location_mismatch, false))
              else '{}'::jsonb end)
         order by u.n), '[]'::jsonb)
    into v_rows
    from unnest(v_ids) with ordinality u(id, n)
    join public.entity e on e.id = u.id
    left join lateral (
      select acc.id, acc.stage, acc.owner_member_id, ce.display_name as account_name, ce.id as account_entity_id,
             r.attributes ->> 'title' as title,
             (select cp.masked_hint from public.contact_point cp
               where cp.entity_id = e.id and cp.workspace_id = p_ws and cp.type = 'email'
               order by (cp.status = 'valid') desc, cp.verified_at desc nulls last, cp.created_at desc limit 1) as masked_email,
             exists (select 1 from public.entity_flag ef
                      where ef.entity_id = e.id and ef.workspace_id = p_ws
                        and ef.kind = 'location_mismatch') as location_mismatch
        from public.account acc
        join public.entity ce on ce.id = acc.entity_id and ce.entity_type = 'company' and ce.status = 'active'
        left join public.relationship r on r.to_entity_id = ce.id and r.from_entity_id = e.id
                                        and r.relation = 'works_at' and r.valid_to is null
       where acc.workspace_id = p_ws
         and (case when p_entity_type = 'person'
                   then r.id is not null
                   else ce.id = e.id end)
       order by case when p_entity_type = 'person' then 1 else 0 end, acc.created_at, acc.id
       limit 1
    ) a on true
    left join public.member om on om.id = a.owner_member_id
    left join lateral (select v.value from public.verdict v
                         where v.entity_id = a.account_entity_id and v.brief_version_id = any(v_vers)
                         order by array_position(enum_range(null::public.verdict_value), v.value), v.computed_at desc
                         limit 1) vd on true
    left join lateral (select s.band, s.priority from public.score s
                         where s.account_id = a.id and s.brief_version_id = any(v_vers)
                         order by s.priority desc nulls last, s.computed_at desc
                         limit 1) sc on true;

  select coalesce(jsonb_agg(app.table_column_json(u.id) order by u.o), '[]'::jsonb) into v_columns
    from unnest(v_cols) with ordinality u(id, o);

  return jsonb_build_object('total', coalesce(v_total, 0), 'rows', v_rows, 'columns', v_columns);
end $$;

-- ── 11. Grants for the client RPCs ──────────────────────────────────────────
revoke all on function app.resolve_review_item_v2(uuid, text, text, uuid) from public, anon, authenticated;
revoke all on function app.resolve_review_item(uuid, text, text, uuid) from public, anon, authenticated;
revoke all on function public.resolve_review_item(uuid, text, text, uuid) from public, anon, authenticated;
grant execute on function app.resolve_review_item(uuid, text, text) to authenticated;
grant execute on function public.resolve_review_item(uuid, text, text) to authenticated;
grant execute on function app.resolve_review_item(uuid, text, text, uuid) to authenticated;
grant execute on function public.resolve_review_item(uuid, text, text, uuid) to authenticated;

revoke all on function public.person_email(uuid, uuid) from public, anon;
revoke all on function public.link_person_to_account(uuid, uuid, uuid) from public, anon;
revoke all on function public.set_provider_order(uuid, jsonb) from public, anon;
revoke all on function public.provider_status(uuid) from public, anon;
revoke all on function public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int, public.entity_type)
  from public, anon;
grant execute on function public.person_email(uuid, uuid) to authenticated;
grant execute on function public.link_person_to_account(uuid, uuid, uuid) to authenticated;
grant execute on function public.set_provider_order(uuid, jsonb) to authenticated;
grant execute on function public.provider_status(uuid) to authenticated;
grant execute on function public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int, public.entity_type)
  to authenticated;

revoke all on function public.estimate_find_people(uuid, uuid, int) from public, anon;
revoke all on function public.request_find_people(uuid, uuid, int, uuid) from public, anon;
grant execute on function public.estimate_find_people(uuid, uuid, int) to authenticated;
grant execute on function public.request_find_people(uuid, uuid, int, uuid) to authenticated;

-- ── 12. Research and provider column specs (Task 6) ──────────────────────────
-- P3 reserved 'research' and 'provider' as fill methods but its validator only accepted
-- extract/formula, and its publish trigger only saved a fill_config for formula. A Brief may now
-- carry either config; every other check (keys, types, rules, cycles) is kept exactly as in 0039.
create or replace function app.validate_brief_columns(p_cfg jsonb) returns void
  language plpgsql immutable set search_path = ''
as $$
declare
  v_preds  jsonb := coalesce(nullif(p_cfg -> 'predicates', 'null'::jsonb), '[]'::jsonb);
  v_rules  jsonb := coalesce(nullif(p_cfg -> 'evidence_rules', 'null'::jsonb), '[]'::jsonb);
  v_types  jsonb := '{}'::jsonb;
  v_enums  jsonb := '{}'::jsonb;
  v_fills  jsonb := '{}'::jsonb;
  v_inputs jsonb := '{}'::jsonb;
  s        jsonb;
  r        jsonb;
  v_key    text;
  v_type   text;
  v_fill   text;
  v_op     text;
  v_val    jsonb;
  v_cfg    jsonb;
  v_n      jsonb;
  v_in     text;
  v_cycle  text[];
  v_legacy text[] := '{}';
begin
  if jsonb_typeof(v_preds) <> 'array' then
    raise exception 'predicates must be a list of columns' using errcode = '22023';
  end if;
  if jsonb_typeof(v_rules) <> 'array' then
    raise exception 'evidence_rules must be a list of rules' using errcode = '22023';
  end if;
  if jsonb_array_length(v_preds) > 12 then
    raise exception 'a Brief has at most 12 columns (this one has %)', jsonb_array_length(v_preds)
      using errcode = '22023';
  end if;
  if jsonb_array_length(v_rules) > 10 then
    raise exception 'a Brief has at most 10 evidence rules (this one has %)', jsonb_array_length(v_rules)
      using errcode = '22023';
  end if;

  for s in select value from jsonb_array_elements(v_preds) loop
    if jsonb_typeof(s) = 'string' then
      v_legacy := v_legacy || (s #>> '{}');
      continue;
    end if;
    if jsonb_typeof(s) <> 'object' then
      raise exception 'each column must be an object or a key' using errcode = '22023';
    end if;
    v_key := case when jsonb_typeof(s -> 'key') = 'string' then s ->> 'key' end;
    if v_key is null or v_key !~ '^[a-z][a-z0-9_]{1,39}$' then
      raise exception 'column "%": key must be 2–40 lowercase letters, digits or underscores, starting with a letter',
        coalesce(s ->> 'key', '') using errcode = '22023';
    end if;
    if v_types ? v_key then
      raise exception 'column "%": key is used twice', v_key using errcode = '22023';
    end if;
    if jsonb_typeof(s -> 'label') is distinct from 'string'
       or length(btrim(s ->> 'label')) = 0 or length(s ->> 'label') > 80 then
      raise exception 'column "%": label needs 1–80 characters', v_key using errcode = '22023';
    end if;
    v_type := case when jsonb_typeof(s -> 'value_type') = 'string' then s ->> 'value_type' end;
    if v_type is null or v_type not in ('boolean', 'number', 'enum', 'text', 'date') then
      raise exception 'column "%": value_type must be boolean, number, enum, text or date', v_key
        using errcode = '22023';
    end if;
    v_val := s -> 'allowed_values';
    if v_type = 'enum' then
      if jsonb_typeof(v_val) is distinct from 'array'
         or jsonb_array_length(v_val) not between 2 and 12
         or exists (select 1 from jsonb_array_elements(v_val) e
                     where jsonb_typeof(e) <> 'string' or btrim(e #>> '{}') = '')
         or (select count(distinct e) from jsonb_array_elements(v_val) e) <> jsonb_array_length(v_val) then
        raise exception 'column "%": enum needs 2–12 allowed values', v_key using errcode = '22023';
      end if;
      v_enums := v_enums || jsonb_build_object(v_key, v_val);
    elsif v_val is not null and v_val <> 'null'::jsonb then
      raise exception 'column "%": only an enum has allowed values', v_key using errcode = '22023';
    end if;
    v_val := s -> 'extraction_hint';
    if v_val is not null and v_val <> 'null'::jsonb
       and (jsonb_typeof(v_val) <> 'string' or length(v_val #>> '{}') > 300) then
      raise exception 'column "%": extraction_hint must be text of at most 300 characters', v_key
        using errcode = '22023';
    end if;
    v_n := s -> 'decay_half_life_days';
    if v_n is not null and v_n <> 'null'::jsonb
       and (jsonb_typeof(v_n) <> 'number' or v_n::numeric <> trunc(v_n::numeric)
            or v_n::numeric not between 1 and 3650) then
      raise exception 'column "%": decay_half_life_days must be empty or 1–3650 days', v_key
        using errcode = '22023';
    end if;
    if jsonb_typeof(s -> 'role') is distinct from 'string'
       or s ->> 'role' not in ('signal', 'fit.vertical', 'fit.geography', 'fit.size',
                               'fit.commercial_role', 'scale', 'info') then
      raise exception 'column "%": role must be signal, fit.vertical, fit.geography, fit.size, fit.commercial_role, scale or info',
        v_key using errcode = '22023';
    end if;

    -- P3's fill check, widened for P6's two paid fill methods.
    v_val := s -> 'fill_method';
    if v_val is null or v_val = 'null'::jsonb then
      v_fill := 'extract';
    elsif jsonb_typeof(v_val) = 'string' and v_val #>> '{}' in ('extract', 'formula', 'research', 'provider') then
      v_fill := v_val #>> '{}';
    else
      raise exception 'column "%": fill_method must be extract, formula, research or provider', v_key
        using errcode = '22023';
    end if;

    v_cfg := s -> 'fill_config';
    if v_fill in ('research', 'provider') then
      if jsonb_typeof(v_cfg) is distinct from 'object' then
        raise exception 'column "%": a % column needs a fill_config object', v_key, v_fill using errcode = '22023';
      end if;
    elsif v_cfg is not null and v_cfg <> 'null'::jsonb and v_cfg <> '{}'::jsonb then
      raise exception 'column "%": fill_config is only for research and provider columns', v_key using errcode = '22023';
    end if;
    if v_fill = 'research' then
      if jsonb_typeof(v_cfg -> 'question') <> 'string'
         or length(btrim(v_cfg ->> 'question')) not between 10 and 500 then
        raise exception 'column "%": research needs a question of 10–500 characters', v_key using errcode = '22023';
      end if;
      v_val := v_cfg -> 'inputs';
      if jsonb_typeof(v_val) is distinct from 'array' or jsonb_array_length(v_val) > 10
         or exists (select 1 from jsonb_array_elements(v_val) e where jsonb_typeof(e) <> 'string')
         or (select count(distinct e) from jsonb_array_elements(v_val) e) <> jsonb_array_length(v_val) then
        raise exception 'column "%": research inputs must be at most 10 distinct column keys', v_key
          using errcode = '22023';
      end if;
      v_val := v_cfg -> 'web';
      if v_val is not null and v_val <> 'null'::jsonb and jsonb_typeof(v_val) <> 'boolean' then
        raise exception 'column "%": research web must be true or false', v_key using errcode = '22023';
      end if;
      v_n := v_cfg -> 'max_pages';
      if v_n is not null and v_n <> 'null'::jsonb
         and (jsonb_typeof(v_n) <> 'number' or v_n::numeric <> trunc(v_n::numeric)
              or v_n::numeric not between 1 and 5) then
        raise exception 'column "%": research max_pages must be 1–5', v_key using errcode = '22023';
      end if;
    end if;
    if v_fill = 'provider' then
      if v_cfg ->> 'provider' not in ('leadmagic', 'findymail', 'prospeo', 'pdl_company', 'email_waterfall') then
        raise exception 'column "%": unknown provider "%"', v_key, coalesce(v_cfg ->> 'provider', '')
          using errcode = '22023';
      end if;
      v_val := v_cfg -> 'field';
      if v_val is not null and v_val <> 'null'::jsonb
         and (jsonb_typeof(v_val) <> 'string' or (v_cfg ->> 'provider') <> 'pdl_company'
              or (v_val #>> '{}') !~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,4}$') then
        raise exception 'column "%": only pdl_company has a dotted company field', v_key using errcode = '22023';
      end if;
    end if;

    -- SQL never parses a formula: the editor and the worker check its parse and inputs.
    v_val := s -> 'formula';
    if v_fill = 'formula' then
      if jsonb_typeof(v_val) is distinct from 'string'
         or length(btrim(v_val #>> '{}')) = 0 or length(v_val #>> '{}') > 500 then
        raise exception 'column "%": a formula column needs a formula of 1–500 characters', v_key
          using errcode = '22023';
      end if;
    elsif v_val is not null and v_val <> 'null'::jsonb then
      raise exception 'column "%": only a formula column has a formula', v_key using errcode = '22023';
    end if;
    v_val := s -> 'inputs';
    if v_fill = 'formula' then
      if jsonb_typeof(v_val) is distinct from 'array'
         or jsonb_array_length(v_val) not between 1 and 10
         or exists (select 1 from jsonb_array_elements(v_val) e where jsonb_typeof(e) <> 'string')
         or (select count(distinct e) from jsonb_array_elements(v_val) e) <> jsonb_array_length(v_val) then
        raise exception 'column "%": a formula reads 1–10 other columns', v_key using errcode = '22023';
      end if;
      v_inputs := v_inputs || jsonb_build_object(v_key, v_val);
    elsif v_fill = 'research' then
      if jsonb_typeof(v_val) is distinct from 'array'
         or jsonb_array_length(v_val) > 10
         or exists (select 1 from jsonb_array_elements(v_val) e where jsonb_typeof(e) <> 'string')
         or (select count(distinct e) from jsonb_array_elements(v_val) e) <> jsonb_array_length(v_val) then
        raise exception 'column "%": research reads 0–10 other columns', v_key using errcode = '22023';
      end if;
      v_inputs := v_inputs || jsonb_build_object(v_key, v_val);
    elsif v_val is not null and v_val <> 'null'::jsonb and v_val <> '[]'::jsonb then
      raise exception 'column "%": only a formula or research column has inputs', v_key using errcode = '22023';
    end if;

    v_types := v_types || jsonb_build_object(v_key, v_type);
    v_fills := v_fills || jsonb_build_object(v_key, v_fill);
  end loop;

  -- Inputs exist; a formula may only read extract/formula columns (research claims are evidence,
  -- but a formula can read their values too once they run — P3's rule is kept as written).
  for v_key in select k from jsonb_object_keys(v_inputs) k order by k collate "C" loop
    for v_in in select jsonb_array_elements_text(v_inputs -> v_key) loop
      if v_in = v_key then
        if v_fills ->> v_key = 'formula' then
          raise exception 'column "%": a formula cannot read its own column', v_key using errcode = '22023';
        end if;
        raise exception 'column "%": a column cannot read its own column', v_key using errcode = '22023';
      end if;
      if not (v_fills ? v_in) and v_in = any(v_legacy) then
        if v_fills ->> v_key = 'formula' then
          raise exception 'column "%": formula input "%" has no column spec; give it one first', v_key, v_in
            using errcode = '22023';
        end if;
        raise exception 'column "%": input "%" has no column spec; give it one first', v_key, v_in
          using errcode = '22023';
      end if;
      if not (v_fills ? v_in) then
        raise exception 'column "%": input "%" is not a column of this Brief', v_key, v_in using errcode = '22023';
      end if;
      if v_fills ->> v_key = 'formula' and v_fills ->> v_in not in ('extract', 'formula') then
        raise exception 'column "%": input "%" must be an extract or formula column', v_key, v_in
          using errcode = '22023';
      end if;
    end loop;
  end loop;

  v_cycle := app.formula_cycle(v_inputs);
  if v_cycle is not null then
    raise exception 'formula columns form a cycle: %', array_to_string(v_cycle, ' → ') using errcode = '22023';
  end if;

  for r in select value from jsonb_array_elements(v_rules) loop
    if jsonb_typeof(r) <> 'object' then
      raise exception 'each evidence rule must be an object' using errcode = '22023';
    end if;
    v_key := r ->> 'predicate';
    if v_key is null or not (v_types ? v_key) then
      raise exception 'column "%": an evidence rule points at a column this Brief does not define',
        coalesce(v_key, '') using errcode = '22023';
    end if;
    if v_fills ->> v_key = 'formula' then
      raise exception 'column "%": an evidence rule cannot point at a formula column', v_key using errcode = '22023';
    end if;
    v_type := v_types ->> v_key;
    if r ->> 'kind' is null or r ->> 'kind' not in ('requires', 'disqualifies') then
      raise exception 'column "%": rule kind must be requires or disqualifies', v_key using errcode = '22023';
    end if;
    v_op := r ->> 'op';
    if v_op is null or not (
         (v_type = 'boolean' and v_op in ('is_true', 'is_false', 'present')) or
         (v_type = 'number'  and v_op in ('at_least', 'present')) or
         (v_type = 'enum'    and v_op in ('one_of', 'present')) or
         (v_type = 'text'    and v_op in ('contains', 'present')) or
         (v_type = 'date'    and v_op = 'present')) then
      raise exception 'column "%": a % column has no rule "%"', v_key, v_type, coalesce(v_op, '')
        using errcode = '22023';
    end if;
    v_val := r -> 'value';
    if v_op = 'one_of' then
      if jsonb_typeof(v_val) is distinct from 'array' or jsonb_array_length(v_val) = 0
         or exists (select 1 from jsonb_array_elements(v_val) e where not (v_enums -> v_key) @> jsonb_build_array(e)) then
        raise exception 'column "%": one_of needs values from its allowed values', v_key using errcode = '22023';
      end if;
    elsif v_op = 'at_least' then
      if jsonb_typeof(v_val) is distinct from 'number' then
        raise exception 'column "%": at_least needs a number', v_key using errcode = '22023';
      end if;
    elsif v_op = 'contains' then
      if jsonb_typeof(v_val) is distinct from 'array' or jsonb_array_length(v_val) = 0
         or exists (select 1 from jsonb_array_elements(v_val) e where jsonb_typeof(e) <> 'string') then
        raise exception 'column "%": contains needs a list of words', v_key using errcode = '22023';
      end if;
    elsif v_val is not null and v_val <> 'null'::jsonb then
      raise exception 'column "%": rule "%" takes no value', v_key, v_op using errcode = '22023';
    end if;
    v_n := r -> 'min_sources';
    if v_n is not null and v_n <> 'null'::jsonb
       and (jsonb_typeof(v_n) <> 'number' or v_n::numeric <> trunc(v_n::numeric)
            or v_n::numeric not between 1 and 5) then
      raise exception 'column "%": min_sources must be 1–5', v_key using errcode = '22023';
    end if;
    v_n := r -> 'max_age_days';
    if v_n is not null and v_n <> 'null'::jsonb
       and (jsonb_typeof(v_n) <> 'number' or v_n::numeric <> trunc(v_n::numeric)
            or v_n::numeric not between 1 and 3650) then
      raise exception 'column "%": max_age_days must be empty or 1–3650 days', v_key using errcode = '22023';
    end if;
  end loop;
end $$;

-- Publish sync (0039's body, unchanged except for fill_config and research inputs). A research or
-- provider column now carries its config into public.predicate, so the executor sees what the
-- Brief author wrote; research inputs resolve to input_predicate_ids like a formula's do.
create or replace function app.sync_brief_predicates() returns trigger
  language plpgsql security definer set search_path = ''
as $$
declare
  v_preds  jsonb := case when jsonb_typeof(new.config -> 'predicates') = 'array'
                         then new.config -> 'predicates' else '[]'::jsonb end;
  v_order  jsonb;
  v_prev   jsonb;
  s        jsonb;
  p        public.predicate%rowtype;
  v_id     uuid;
  v_union  jsonb;
  v_added  text[];
  v_hint   text;
  v_decay  int;
  v_fill   text;
  v_fcfg   jsonb;
  v_ins    uuid[];
  v_ver    int;
  v_fill_q uuid[] := '{}';
begin
  perform app.validate_brief_columns(new.config);

  select coalesce(jsonb_agg(e order by e ->> 'key'), '[]'::jsonb) into v_order
    from jsonb_array_elements(v_preds) e
   where jsonb_typeof(e) = 'object' and coalesce(e ->> 'fill_method', 'extract') <> 'formula';
  select v_order || coalesce(jsonb_agg(e order by o.n), '[]'::jsonb) into v_order
    from unnest(app.formula_order(
           (select coalesce(jsonb_object_agg(x ->> 'key', x -> 'inputs'), '{}'::jsonb)
              from jsonb_array_elements(v_preds) x
             where jsonb_typeof(x) = 'object' and x ->> 'fill_method' = 'formula'))) with ordinality o(k, n)
    join jsonb_array_elements(v_preds) e on jsonb_typeof(e) = 'object' and e ->> 'key' = o.k;

  for s in select e from jsonb_array_elements(v_order) e loop
    v_fill := coalesce(s ->> 'fill_method', 'extract');
    v_fcfg := case
      when v_fill = 'formula' then jsonb_build_object('formula', s ->> 'formula')
      when v_fill in ('research', 'provider') then coalesce(s -> 'fill_config', '{}'::jsonb)
      else '{}'::jsonb end;
    select coalesce(array_agg(x.id order by x.key collate "C"), '{}') into v_ins
      from public.predicate x
     where v_fill in ('formula', 'research') and x.workspace_id = new.workspace_id
       and x.key in (select jsonb_array_elements_text(s -> 'inputs'));

    select * into p from public.predicate
     where workspace_id = new.workspace_id and key = s ->> 'key' for update;
    if not found then
      v_id := null;
      insert into public.predicate
        (key, org_id, workspace_id, label, value_type, allowed_values, decay_half_life_days, extraction_hint,
         fill_method, fill_config, input_predicate_ids, auto_run)
      values
        (s ->> 'key', new.org_id, new.workspace_id, s ->> 'label', s ->> 'value_type',
         case when s ->> 'value_type' = 'enum' then s -> 'allowed_values' end,
         (s ->> 'decay_half_life_days')::numeric::int, coalesce(s ->> 'extraction_hint', ''),
         v_fill, v_fcfg, v_ins, v_fill = 'formula')
      on conflict on constraint one_key_per_scope do nothing
      returning id into v_id;
      if v_id is not null then
        perform app.audit(new.org_id, new.workspace_id, 'predicate.created', 'predicate', v_id, null,
                          jsonb_build_object('key', s ->> 'key', 'value_type', s ->> 'value_type',
                                             'brief_version_id', new.id));
        if v_fill = 'formula' then
          v_fill_q := v_fill_q || v_id;
        end if;
        continue;
      end if;
      select * into p from public.predicate
       where workspace_id = new.workspace_id and key = s ->> 'key' for update;
    end if;

    if p.value_type <> s ->> 'value_type' then
      raise exception 'column "%" already exists in this workspace as %; give it another key',
        p.key, p.value_type using errcode = '23514';
    end if;
    if p.fill_method <> v_fill then
      raise exception 'column "%" already exists in this workspace filled by %; give it another key',
        p.key, p.fill_method using errcode = '23514';
    end if;
    if p.value_type = 'enum' then
      select coalesce(p.allowed_values, '[]'::jsonb) || coalesce(jsonb_agg(e order by o), '[]'::jsonb)
        into v_union
        from jsonb_array_elements(s -> 'allowed_values') with ordinality a(e, o)
       where not coalesce(p.allowed_values, '[]'::jsonb) @> jsonb_build_array(e);
      if v_union is distinct from p.allowed_values then
        update public.predicate set allowed_values = v_union where id = p.id;
        perform app.audit(new.org_id, new.workspace_id, 'predicate.values_added', 'predicate', p.id,
                          jsonb_build_object('allowed_values', p.allowed_values),
                          jsonb_build_object('allowed_values', v_union, 'brief_version_id', new.id));
      end if;
    end if;

    v_hint  := coalesce(s ->> 'extraction_hint', '');
    v_decay := (s ->> 'decay_half_life_days')::numeric::int;
    if p.label is distinct from s ->> 'label'
       or coalesce(p.extraction_hint, '') <> v_hint
       or p.decay_half_life_days is distinct from v_decay then
      update public.predicate
         set label = s ->> 'label',
             extraction_hint = case when coalesce(extraction_hint, '') = v_hint
                                    then extraction_hint else v_hint end,
             decay_half_life_days = v_decay
       where id = p.id;
      perform app.audit(new.org_id, new.workspace_id, 'predicate.updated', 'predicate', p.id,
                        jsonb_build_object('label', p.label, 'extraction_hint', p.extraction_hint,
                                           'decay_half_life_days', p.decay_half_life_days),
                        jsonb_build_object('label', s ->> 'label', 'extraction_hint', v_hint,
                                           'decay_half_life_days', v_decay,
                                           'brief_version_id', new.id));
    end if;

    if p.fill_config is distinct from v_fcfg or p.input_predicate_ids is distinct from v_ins then
      update public.predicate set fill_config = v_fcfg, input_predicate_ids = v_ins
       where id = p.id returning version into v_ver;
      perform app.audit(new.org_id, new.workspace_id, 'predicate.fill_changed', 'predicate', p.id,
                        jsonb_build_object('fill_config', p.fill_config, 'input_predicate_ids', p.input_predicate_ids,
                                           'version', p.version),
                        jsonb_build_object('fill_config', v_fcfg, 'input_predicate_ids', v_ins,
                                           'version', v_ver, 'brief_version_id', new.id));
    end if;

    if v_fill = 'formula'
       and (select x.config_hash from public.predicate x where x.id = p.id) is distinct from p.config_hash then
      v_fill_q := v_fill_q || p.id;
    end if;
  end loop;

  select config -> 'predicates' into v_prev from public.brief_version
   where brief_id = new.brief_id and version = new.version - 1;
  v_prev := case when jsonb_typeof(v_prev) = 'array' then v_prev else '[]'::jsonb end;
  select array_agg(k order by k) into v_added
    from (select e ->> 'key' as k from jsonb_array_elements(v_preds) e where jsonb_typeof(e) = 'object'
          except
          select case jsonb_typeof(e) when 'string' then e #>> '{}' else e ->> 'key' end
            from jsonb_array_elements(v_prev) e) added;
  if v_added is not null then
    perform app.emit('predicate.backfill_requested'::public.event_type, new.org_id, new.workspace_id,
                     'brief_version', new.id,
                     jsonb_build_object('brief_id', new.brief_id, 'brief_version_id', new.id,
                                        'org_id', new.org_id, 'workspace_id', new.workspace_id,
                                        'predicate_keys', to_jsonb(v_added)));
    select v_fill_q || coalesce(array_agg(x.id), '{}') into v_fill_q
      from public.predicate x
     where x.workspace_id = new.workspace_id and x.fill_method = 'formula' and x.key = any(v_added);
  end if;
  if cardinality(v_fill_q) > 0 then
    perform app.queue_formula_backfill(array(select distinct unnest(v_fill_q)), new.id);
  end if;
  return null;
end $$;

-- Panel detail for P6 runs (Task 6): the attempts behind one run and a research receipt, safe to
-- show as-is. Provider attempts expose only ordinal/provider/status/cost/confidence/error_kind and
-- a raw_ref that the fillers write as a masked hint or URL — never a key or an unmasked email.
-- A research receipt is the run's stored inputs (queries, page URL/title, answer, citations and
-- reasoning), returned only when the run actually wrote the cell's claim or, if there is no claim,
-- is the newest successful run. Cost and status stay in the existing `runs` list.
create or replace function public.cell_detail(p_ws uuid, p_entity uuid, p_predicate uuid) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org    uuid;
  p        public.predicate%rowtype;
  c        record;
  v_me     uuid;
  v_label  text;
  v_runin  jsonb;
  v_inputs jsonb := '[]'::jsonb;
  v_claims jsonb;
  v_runs   jsonb;
  v_attempts jsonb;
  v_receipt  jsonb;
  v_last   uuid;
begin
  perform app.home_lens(p_ws, null);
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  -- A company cell is an account row; a person cell is one linked to that account's company. The
  -- email waterfall fills people, so both must open the same panel.
  if not exists (
    select 1 from public.account a join public.entity e on e.id = a.entity_id
     where a.workspace_id = p_ws and e.org_id = v_org
       and (a.entity_id = p_entity or exists (
              select 1 from public.relationship r
               where r.org_id = v_org and r.from_entity_id = p_entity
                 and r.relation = 'works_at' and r.to_entity_id = a.entity_id))) then
    raise exception 'account not found in this workspace' using errcode = '22023';
  end if;
  select * into p from public.predicate where id = p_predicate and (workspace_id = p_ws or workspace_id is null);
  if not found then
    raise exception 'column not found in this workspace' using errcode = '22023';
  end if;
  select * into c from public.cells(p_ws, array[p_entity], array[p_predicate]);
  v_me := app.current_member_id(v_org);

  select case when sr.connector_id = 'derived:manual' then
                 case when v_me is not null and exists (
                             select 1 from public.audit_log al
                              where al.target_type = 'claim' and al.target_id = cl.id
                                and al.action = 'claim.manual_value_set' and al.actor_member_id = v_me)
                      then 'Entered by you' else coalesce(cl.quote, 'Entered by hand') end
               when sr.connector_id like 'derived:%' then 'Formula'
               else 'Found in sources' end
    into v_label
    from public.claim cl join public.source_record sr on sr.id = cl.source_record_id
   where cl.id = c.claim_id;
  v_label := coalesce(v_label, case p.fill_method when 'formula' then 'Formula' when 'manual' then 'Entered by hand'
                                                  else 'Found in sources' end);

  select coalesce(jsonb_agg(x.j order by x.act desc, x.observed_at desc, x.created_at desc, x.id desc), '[]'::jsonb)
    into v_claims
    from (select cl.id, cl.observed_at, cl.created_at, cl.status = 'active' as act,
                 jsonb_build_object('id', cl.id, 'value', cl.value, 'quote', cl.quote, 'source_url', sr.url,
                                    'source_type', sr.source_type, 'observed_at', cl.observed_at, 'status', cl.status,
                                    'superseded_at', case when cl.status = 'superseded' then nx.created_at end) as j
            from public.claim cl
            join public.source_record sr on sr.id = cl.source_record_id
            left join public.claim nx on nx.id = cl.superseded_by
           where cl.entity_id = p_entity and cl.predicate_id = p_predicate and cl.org_id = v_org
           order by (cl.status = 'active') desc, cl.observed_at desc, cl.created_at desc, cl.id desc
           limit 50) x;

  if p.fill_method = 'formula' then
    if c.claim_id is not null then
      select pr.inputs into v_runin from public.predicate_run pr
       where pr.entity_id = p_entity and pr.predicate_id = p_predicate and pr.claim_id = c.claim_id
         and jsonb_typeof(pr.inputs) = 'array'
       order by pr.queued_at desc, pr.id desc limit 1;
    end if;
    if v_runin is not null then
      select coalesce(jsonb_agg(jsonb_build_object('column_label', x.label, 'value', cl.value, 'claim_id', cl.id)
                                order by x.key collate "C", cl.id), '[]'::jsonb)
        into v_inputs
        from public.claim cl join public.predicate x on x.id = cl.predicate_id
       where cl.org_id = v_org
         and cl.id in (select i::uuid from jsonb_array_elements_text(v_runin) i
                        where i ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$');
    else
      select coalesce(jsonb_agg(jsonb_build_object('column_label', x.label, 'value', iv.value, 'claim_id', iv.id)
                                order by x.key collate "C"), '[]'::jsonb)
        into v_inputs
        from public.predicate x
        cross join lateral (select cl.id, cl.value from public.claim cl
                             where cl.entity_id = p_entity and cl.predicate_id = x.id and cl.status = 'active'
                               and cl.org_id = v_org
                             order by cl.observed_at desc, cl.id desc limit 1) iv
       where x.id = any(p.input_predicate_ids);
    end if;
  end if;

  select coalesce(jsonb_agg(x.j order by x.queued_at desc, x.id desc), '[]'::jsonb)
    into v_runs
    from (select pr.id, pr.queued_at,
                 jsonb_build_object('id', pr.id, 'status', pr.status, 'cost_usd', pr.cost_usd, 'brief_version', bv.version,
                                    'queued_at', pr.queued_at, 'started_at', pr.started_at, 'finished_at', pr.finished_at,
                                    'error', pr.error) as j
            from public.predicate_run pr
            left join public.brief_version bv on bv.id = pr.brief_version_id
           where pr.entity_id = p_entity and pr.predicate_id = p_predicate and pr.workspace_id = p_ws
           order by pr.queued_at desc, pr.id desc
           limit 50) x;

  -- Last run that explains the current value, else the newest run; both lists below follow it.
  select pr.id into v_last from public.predicate_run pr
   where pr.entity_id = p_entity and pr.predicate_id = p_predicate and pr.workspace_id = p_ws
     and (c.claim_id is null or pr.claim_id = c.claim_id)
   order by (pr.claim_id = c.claim_id) desc, pr.queued_at desc, pr.id desc limit 1;

  if v_last is not null then
    select coalesce(jsonb_agg(jsonb_build_object('ordinal', a.ordinal, 'provider', a.provider, 'status', a.status,
                                                 'cost_usd', a.cost_usd, 'confidence', a.confidence,
                                                 'raw_ref', a.raw_ref, 'error_kind', a.error_kind)
                              order by a.ordinal, a.id), '[]'::jsonb)
      into v_attempts
      from public.predicate_run_attempt a where a.run_id = v_last;
    if p.fill_method = 'research' then
      select case when jsonb_typeof(pr.inputs) = 'object'
                   and (pr.inputs ? 'queries' or pr.inputs ? 'answer' or pr.inputs ? 'pages')
                  then pr.inputs end
        into v_receipt
        from public.predicate_run pr where pr.id = v_last;
    end if;
  end if;
  v_attempts := coalesce(v_attempts, '[]'::jsonb);

  return jsonb_build_object('column', app.table_column_json(p.id), 'state', c.state, 'value', c.value,
                            'confidence', c.confidence, 'claim_id', c.claim_id, 'fill_label', v_label, 'claims', v_claims,
                            'inputs', v_inputs, 'runs', v_runs, 'attempts', v_attempts, 'receipt', v_receipt);
end $$;

-- Task 6 also needs a run price for the two new fill methods, otherwise publishing a research or
-- provider column leaves Add column stuck on an "it has no run price yet" estimate. The worker keeps
-- the same prices in model_price; SQL only turns them into a per-cell maximum. The email waterfall
-- is priced as every provider in the workspace's order, since a run may reach any of them.
insert into public.ai_capability_model (capability, model) values ('write', 'claude-sonnet-5-5')
on conflict (capability) do nothing;

create or replace function app.column_cost_per_entity(p_predicate uuid, p_ws uuid) returns numeric
  language plpgsql stable security definer set search_path = ''
as $$
declare
  p        public.predicate%rowtype;
  v_model  text;
  v_in     numeric;
  v_out    numeric;
  v_recs   numeric;
  v_cost   numeric;
  v_pages  int;
  v_order  jsonb;
begin
  select * into p from public.predicate where id = p_predicate;
  if not found then
    raise exception 'column not found' using errcode = 'P0002';
  end if;
  if p.fill_method in ('formula', 'manual') then
    return 0;
  end if;

  if p.fill_method = 'provider' then
    if coalesce(p.fill_config ->> 'provider', '') = 'email_waterfall' then
      select provider_order into v_order from public.workspace_settings
       where workspace_id = coalesce(p_ws, p.workspace_id);
      v_order := coalesce(nullif(v_order, 'null'::jsonb), '["leadmagic","findymail","prospeo"]'::jsonb);
      select coalesce(sum(mp.usd_per_million / 1000000), 0) into v_cost
        from jsonb_array_elements_text(v_order) x(model)
        join public.model_price mp on mp.model = x.model and mp.unit = 'enrichment_lookup';
    else
      select mp.usd_per_million / 1000000 into v_cost
        from public.model_price mp
       where mp.model = p.fill_config ->> 'provider' and mp.unit = 'enrichment_lookup';
    end if;
    if v_cost is null or v_cost <= 0 then
      raise exception 'no price for provider "%"', coalesce(p.fill_config ->> 'provider', '') using errcode = 'P0002';
    end if;
    return round(v_cost, 6);
  end if;

  if p.fill_method = 'research' then
    select model into v_model from public.ai_capability_model where capability = 'write';
    select usd_per_million into v_in from public.model_price where model = v_model and unit = 'ai_input_tokens';
    select usd_per_million into v_out from public.model_price where model = v_model and unit = 'ai_output_tokens';
    if v_in is null or v_out is null then
      raise exception 'no price for the research model "%"', coalesce(v_model, '') using errcode = 'P0002';
    end if;
    v_cost := (3000 * v_in + 500 * v_out) / 1000000;
    if coalesce((p.fill_config ->> 'web')::boolean, false) then
      v_pages := least(5, greatest(1, coalesce((p.fill_config ->> 'max_pages')::int, 1)));
      select usd_per_million / 1000000 into v_in
        from public.model_price where model = 'brave-search' and unit = 'fetch';
      if v_in is not null then
        v_cost := v_cost + v_pages * v_in;
      end if;
    end if;
    return round(v_cost, 6);
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
           where sr.workspace_id = coalesce(p_ws, p.workspace_id) and sr.extraction_status = 'extracted'
             and m.entity_id is not null
           group by m.entity_id) x;
  v_recs := least(20, coalesce(v_recs, 1));
  return round(v_recs * (3000 * v_in + 500 * v_out) / 1000000, 6);
end $$;

-- The operating table shows a waterfall's last attempt per row without calling one RPC per cell.
-- Keys are "<entity_id>:<predicate_id>"; only the current workspace's attempts can be read.
create or replace function public.waterfall_attempts(p_ws uuid, p_entity_ids uuid[], p_predicate_ids uuid[])
returns jsonb language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org uuid;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  return coalesce((
    select jsonb_object_agg(
             last.entity_id::text || ':' || last.predicate_id::text,
             (select coalesce(jsonb_agg(jsonb_build_object('provider', a.provider, 'status', a.status,
                                                           'cost_usd', a.cost_usd, 'ordinal', a.ordinal)
                                        order by a.ordinal, a.id), '[]'::jsonb)
                from public.predicate_run_attempt a where a.run_id = last.id))
      from (
        select distinct on (pr.entity_id, pr.predicate_id) pr.id, pr.entity_id, pr.predicate_id
          from public.predicate_run pr
         where pr.workspace_id = p_ws
           and pr.entity_id = any(p_entity_ids) and pr.predicate_id = any(p_predicate_ids)
         order by pr.entity_id, pr.predicate_id, pr.queued_at desc, pr.id desc
      ) last
  ), '{}'::jsonb);
end $$;

revoke all on function public.waterfall_attempts(uuid, uuid[], uuid[]) from public, anon;
grant execute on function public.waterfall_attempts(uuid, uuid[], uuid[]) to authenticated;
