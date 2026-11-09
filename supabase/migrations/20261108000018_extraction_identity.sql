-- Phase 1d: Extraction/Identity write paths.
-- Adds:
--   - public.review_item (human-in-the-loop queue for unresolved mentions)
--   - app.update_extraction_status   (service_role only)
--   - app.create_mention             (service_role only)
--   - app.create_claim               (service_role only)
--   - app.create_entity              (service_role only)
--   - app.create_entity_alias        (service_role only)
--   - app.resolve_mention            (service_role only)
--   - app.create_account             (service_role only)
--   - app.merge_entities             (service_role only)
--   - app.create_review_item         (service_role only)
--   - app.resolve_review_item        (authenticated — Editor check inside)
--   - Kill-switch seeds for extraction/identity job types

-- ── 1. review_item table ─────────────────────────────────────────────────────
create table public.review_item (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  mention_id       uuid not null references public.mention (id),
  candidate_entity uuid references public.entity (id),
  match_method     text,
  match_confidence smallint check (match_confidence between 0 and 100),
  status           text not null default 'pending' check (status in ('pending', 'merged', 'skipped', 'created_new')),
  resolved_by      uuid references public.member (id),
  resolved_at      timestamptz,
  created_at       timestamptz not null default now()
);
create index review_item_ws_status_idx on public.review_item (workspace_id, status, created_at)
  where status = 'pending';

alter table public.review_item enable row level security;

-- Viewer may read; Editors may insert (review_item_write policy enforces the role check).
-- Service-role workers insert via app.create_review_item; authenticated Editors call
-- app.resolve_review_item to change status.
create policy review_item_read on public.review_item
  for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));

create policy review_item_write on public.review_item
  for insert to authenticated
  with check (app.has_workspace_role(workspace_id, 'Editor'));

grant select, insert on public.review_item to authenticated;

-- ── 2. app.update_extraction_status ─────────────────────────────────────────
create function app.update_extraction_status(
  p_id     uuid,
  p_status public.extraction_status,
  p_detail text default null
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.source_record
  set extraction_status = p_status,
      status_detail     = p_detail,
      updated_at        = now()
  where id = p_id;
end $$;

revoke execute on function app.update_extraction_status(uuid, public.extraction_status, text)
  from public, anon, authenticated;
grant execute on function app.update_extraction_status(uuid, public.extraction_status, text)
  to postgres, service_role;

-- ── 3. app.create_mention ────────────────────────────────────────────────────
create function app.create_mention(
  p_org            uuid,
  p_source_record  uuid,
  p_entity_type    public.entity_type,
  p_raw_name       text,
  p_raw_domain     text,
  p_raw_country    text,
  p_raw_attributes jsonb
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.mention (org_id, source_record_id, entity_type, raw_name, raw_domain, raw_country, raw_attributes)
  values (p_org, p_source_record, p_entity_type, p_raw_name, p_raw_domain, p_raw_country, coalesce(p_raw_attributes, '{}'))
  returning id into v_id;
  return v_id;
end $$;

revoke execute on function
  app.create_mention(uuid, uuid, public.entity_type, text, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function
  app.create_mention(uuid, uuid, public.entity_type, text, text, text, jsonb)
  to postgres, service_role;

-- ── 4. app.create_claim ──────────────────────────────────────────────────────
create function app.create_claim(
  p_org               uuid,
  p_entity            uuid,
  p_predicate         uuid,
  p_value             jsonb,
  p_quote             text,
  p_quote_original    text,
  p_quote_language    text,
  p_source_record     uuid,
  p_observed_at       timestamptz,
  p_extractor_version uuid,
  p_confidence        smallint
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.claim (org_id, entity_id, predicate_id, value, quote, quote_original, quote_language,
                             source_record_id, observed_at, extractor_version_id, confidence)
  values (p_org, p_entity, p_predicate, p_value, p_quote, p_quote_original, p_quote_language,
          p_source_record, p_observed_at, p_extractor_version, p_confidence)
  returning id into v_id;
  return v_id;
end $$;

revoke execute on function
  app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  from public, anon, authenticated;
grant execute on function
  app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  to postgres, service_role;

-- ── 5. app.create_entity ─────────────────────────────────────────────────────
create function app.create_entity(
  p_org         uuid,
  p_entity_type public.entity_type,
  p_display_name text,
  p_scope       public.data_scope default 'tenant'
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.entity (org_id, entity_type, display_name, scope)
  values (p_org, p_entity_type, p_display_name, p_scope)
  returning id into v_id;

  perform app.audit(p_org, null, 'entity.created', 'entity', v_id, null,
    jsonb_build_object('entity_type', p_entity_type::text, 'display_name', p_display_name, 'scope', p_scope::text));

  return v_id;
end $$;

revoke execute on function
  app.create_entity(uuid, public.entity_type, text, public.data_scope)
  from public, anon, authenticated;
grant execute on function
  app.create_entity(uuid, public.entity_type, text, public.data_scope)
  to postgres, service_role;

-- ── 6. app.create_entity_alias ───────────────────────────────────────────────
create function app.create_entity_alias(
  p_org              uuid,
  p_entity           uuid,
  p_alias_type       public.alias_type,
  p_value_normalised text,
  p_confidence       smallint,
  p_source_record    uuid
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
  values (p_org, p_entity, p_alias_type, p_value_normalised, p_confidence, p_source_record)
  on conflict (org_id, alias_type, value_normalised, entity_id)
  do update set confidence = greatest(entity_alias.confidence, excluded.confidence)
  returning id into v_id;
  return v_id;
end $$;

revoke execute on function
  app.create_entity_alias(uuid, uuid, public.alias_type, text, smallint, uuid)
  from public, anon, authenticated;
grant execute on function
  app.create_entity_alias(uuid, uuid, public.alias_type, text, smallint, uuid)
  to postgres, service_role;

-- ── 7. app.resolve_mention ───────────────────────────────────────────────────
create function app.resolve_mention(
  p_mention          uuid,
  p_entity           uuid,
  p_match_method     text,
  p_match_confidence smallint
) returns void
language plpgsql security definer set search_path = '' as $$
begin
  update public.mention
  set entity_id        = p_entity,
      match_method     = p_match_method,
      match_confidence = p_match_confidence
  where id = p_mention;
end $$;

revoke execute on function
  app.resolve_mention(uuid, uuid, text, smallint)
  from public, anon, authenticated;
grant execute on function
  app.resolve_mention(uuid, uuid, text, smallint)
  to postgres, service_role;

-- ── 8. app.create_account ────────────────────────────────────────────────────
create function app.create_account(
  p_org       uuid,
  p_workspace uuid,
  p_entity    uuid
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.account (org_id, workspace_id, entity_id)
  values (p_org, p_workspace, p_entity)
  on conflict (workspace_id, entity_id) do nothing
  returning id into v_id;

  if v_id is null then
    -- Duplicate: return existing id.
    select id into v_id from public.account where workspace_id = p_workspace and entity_id = p_entity;
  end if;

  return v_id;
end $$;

revoke execute on function
  app.create_account(uuid, uuid, uuid)
  from public, anon, authenticated;
grant execute on function
  app.create_account(uuid, uuid, uuid)
  to postgres, service_role;

-- ── 9. app.merge_entities ────────────────────────────────────────────────────
create function app.merge_entities(
  p_winner uuid,
  p_loser  uuid,
  p_actor  uuid
) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org uuid;
begin
  select org_id into v_org from public.entity where id = p_winner;

  -- Re-point all mentions of the loser to the winner.
  update public.mention set entity_id = p_winner where entity_id = p_loser;

  -- Re-point all aliases of the loser to the winner.
  update public.entity_alias set entity_id = p_winner where entity_id = p_loser;

  -- Mark the loser as merged.
  update public.entity
  set status = 'merged', merged_into = p_winner, updated_at = now()
  where id = p_loser;

  perform app.audit(v_org, null, 'entity.merged', 'entity', p_loser,
    jsonb_build_object('loser', p_loser, 'winner', p_winner),
    jsonb_build_object('merged_into', p_winner, 'actor', p_actor));
end $$;

revoke execute on function
  app.merge_entities(uuid, uuid, uuid)
  from public, anon, authenticated;
grant execute on function
  app.merge_entities(uuid, uuid, uuid)
  to postgres, service_role;

-- ── 10. app.create_review_item ───────────────────────────────────────────────
create function app.create_review_item(
  p_org              uuid,
  p_workspace        uuid,
  p_mention          uuid,
  p_candidate_entity uuid,
  p_match_method     text,
  p_match_confidence smallint
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  insert into public.review_item (org_id, workspace_id, mention_id, candidate_entity, match_method, match_confidence)
  values (p_org, p_workspace, p_mention, p_candidate_entity, p_match_method, p_match_confidence)
  returning id into v_id;
  return v_id;
end $$;

revoke execute on function
  app.create_review_item(uuid, uuid, uuid, uuid, text, smallint)
  from public, anon, authenticated;
grant execute on function
  app.create_review_item(uuid, uuid, uuid, uuid, text, smallint)
  to postgres, service_role;

-- ── 11. app.resolve_review_item ──────────────────────────────────────────────
-- Callable by authenticated clients; performs its own Editor role check.
-- action: 'merge' | 'skip' | 'create_new'
create function app.resolve_review_item(
  p_id              uuid,
  p_action          text,
  p_new_entity_name text default null
) returns void
language plpgsql security definer set search_path = '' as $$
declare
  r         public.review_item%rowtype;
  v_entity  uuid;
  v_org     uuid;
  v_ws      uuid;
begin
  select * into r from public.review_item where id = p_id;

  if not found then
    raise exception 'review_item not found' using errcode = 'P0002';
  end if;

  -- Editor check.
  if not app.has_workspace_role(r.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;

  if r.status <> 'pending' then
    raise exception 'review_item is not pending' using errcode = '23514';
  end if;

  if p_action not in ('merge', 'skip', 'create_new') then
    raise exception 'invalid action: %', p_action using errcode = '22023';
  end if;

  -- Fetch context.
  select org_id into v_org from public.organisation o
    join public.workspace w on w.org_id = o.id
    where w.id = r.workspace_id;
  v_ws := r.workspace_id;

  if p_action = 'merge' then
    perform app.resolve_mention(r.mention_id, r.candidate_entity, r.match_method, r.match_confidence);
    perform app.create_account(v_org, v_ws, r.candidate_entity);
  elsif p_action = 'create_new' then
    declare
      v_mention_entity_type public.entity_type;
    begin
      select entity_type into v_mention_entity_type from public.mention where id = r.mention_id;
      v_entity := app.create_entity(v_org, v_mention_entity_type, coalesce(p_new_entity_name, ''), 'tenant');
    end;
    perform app.resolve_mention(r.mention_id, v_entity, 'manual_create', 100);
    perform app.create_account(v_org, v_ws, v_entity);
  end if;
  -- 'skip': no entity action needed.

  update public.review_item
  set status      = case p_action
                      when 'merge'      then 'merged'
                      when 'skip'       then 'skipped'
                      when 'create_new' then 'created_new'
                    end,
      resolved_by = app.current_member_id(v_org),
      resolved_at = now()
  where id = p_id;
end $$;

revoke execute on function
  app.resolve_review_item(uuid, text, text)
  from public, anon, authenticated;
grant execute on function
  app.resolve_review_item(uuid, text, text)
  to authenticated;

-- Public wrapper so clients can call it via PostgREST RPC.
create function public.resolve_review_item(p_id uuid, p_action text, p_new_entity_name text default null)
  returns void language sql security definer set search_path = '' as $$
  select app.resolve_review_item(p_id, p_action, p_new_entity_name)
$$;
revoke execute on function public.resolve_review_item(uuid, text, text) from public, anon, authenticated;
grant execute on function public.resolve_review_item(uuid, text, text) to authenticated;

-- ── 12. Kill-switch seeds ────────────────────────────────────────────────────
insert into public.job_type_config (job_type, enabled) values
  ('extract.relevance_judge', true),
  ('extract.structured',      true),
  ('identity.resolve',        true),
  ('identity.duplicate_sweep', true)
on conflict do nothing;
