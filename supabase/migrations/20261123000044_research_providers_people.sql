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
