-- Migration 0037: engine integrity.
--   1. app.create_claim is idempotent per (entity, predicate, source_record, value)
--      among active claims, and emits claim.created on insert only.
--   2. app.merge_entities emits entity.merged to the outbox.
--   3. meeting.person_entity_id gets a real foreign key.

-- ── 1. Claim guard: allow confidence and quote refresh inside create_claim ────
-- A re-extraction of the same fact may raise confidence or supply a missing
-- quote. The flag app.claim_dedupe is set transaction-locally by create_claim.
create or replace function app.claim_guard() returns trigger
language plpgsql set search_path = '' as $$
declare
  allowed text[] := array['status', 'superseded_by'];
  col     text;
begin
  if coalesce(current_setting('app.claim_dedupe', true), '') = 'true' then
    allowed := allowed || array['confidence', 'quote'];
  end if;
  for col in select jsonb_object_keys(to_jsonb(new)) loop
    if col = 'entity_id' then
      if (to_jsonb(new) -> col) is distinct from (to_jsonb(old) -> col)
         and not app.is_merging() then
        raise exception 'claim.entity_id is immutable outside a merge'
          using errcode = '42501';
      end if;
    elsif not (col = any(allowed))
      and (to_jsonb(new) -> col) is distinct from (to_jsonb(old) -> col) then
      raise exception 'column % of claim is immutable', col
        using errcode = '42501';
    end if;
  end loop;
  return new;
end $$;

create index if not exists claim_dedupe_idx
  on public.claim (entity_id, predicate_id, source_record_id) where status = 'active';

-- ── 2. app.create_claim ───────────────────────────────────────────────────────
create or replace function app.create_claim(
  p_org uuid,
  p_entity uuid,
  p_predicate uuid,
  p_value jsonb,
  p_quote text,
  p_quote_original text,
  p_quote_language text,
  p_source_record uuid,
  p_observed_at timestamptz,
  p_extractor_version uuid,
  p_confidence smallint
) returns uuid
language plpgsql security definer set search_path = '' as $$
declare v_id uuid;
begin
  select id into v_id from public.claim
  where entity_id = p_entity and predicate_id = p_predicate
    and source_record_id = p_source_record and value = p_value
    and status = 'active'
  order by created_at
  limit 1;

  if v_id is not null then
    perform set_config('app.claim_dedupe', 'true', true);
    update public.claim
    set confidence = greatest(confidence, p_confidence),
        quote = coalesce(quote, p_quote)
    where id = v_id;
    perform set_config('app.claim_dedupe', 'false', true);
    return v_id;
  end if;

  insert into public.claim (org_id, entity_id, predicate_id, value, quote, quote_original, quote_language,
    source_record_id, observed_at, extractor_version_id, confidence)
  values (p_org, p_entity, p_predicate, p_value, p_quote, p_quote_original, p_quote_language,
    p_source_record, p_observed_at, p_extractor_version, p_confidence)
  returning id into v_id;

  perform app.emit('claim.created', p_org, null, 'claim', v_id,
    jsonb_build_object('entity_id', p_entity, 'predicate_id', p_predicate,
                       'source_record_id', p_source_record));
  return v_id;
end $$;

revoke execute on function app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  from public, anon, authenticated;
grant execute on function app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  to service_role;

-- ── 3. app.merge_entities: emit entity.merged ─────────────────────────────────
create or replace function app.merge_entities(
  p_winner uuid,
  p_loser  uuid,
  p_actor  uuid
) returns void
language plpgsql security definer set search_path = '' as $$
declare
  v_org      uuid;
  v_loser_org uuid;
begin
  select org_id into v_org from public.entity where id = p_winner;
  select org_id into v_loser_org from public.entity where id = p_loser;

  if p_winner = p_loser then
    raise exception 'cannot merge entity with itself' using errcode = '22023';
  end if;

  if v_org is distinct from v_loser_org then
    raise exception 'entities from different orgs cannot be merged' using errcode = '22023';
  end if;

  if (select status from public.entity where id = p_winner) = 'merged' then
    raise exception 'winner entity is already merged' using errcode = '22023';
  end if;

  update public.mention set entity_id = p_winner where entity_id = p_loser;

  update public.entity_alias set entity_id = p_winner
  where entity_id = p_loser
    and not exists (
      select 1 from public.entity_alias ea2
      where ea2.entity_id = p_winner
        and ea2.org_id = entity_alias.org_id
        and ea2.alias_type = entity_alias.alias_type
        and ea2.value_normalised = entity_alias.value_normalised
    );
  delete from public.entity_alias where entity_id = p_loser;

  -- Set merging flag so claim_guard allows entity_id update.
  perform set_config('app.merging', 'true', true);
  update public.claim set entity_id = p_winner where entity_id = p_loser;
  perform set_config('app.merging', 'false', true);

  update public.account set entity_id = p_winner where entity_id = p_loser
    and not exists (
      select 1 from public.account a2
      where a2.entity_id = p_winner and a2.workspace_id = account.workspace_id
    );
  delete from public.account where entity_id = p_loser;

  update public.entity
  set status = 'merged', merged_into = p_winner, updated_at = now()
  where id = p_loser;

  perform app.audit(v_org, null, 'entity.merged', 'entity', p_loser,
    jsonb_build_object('loser', p_loser, 'winner', p_winner),
    jsonb_build_object('merged_into', p_winner, 'actor', p_actor));

  perform app.emit('entity.merged', v_org, null, 'entity', p_winner,
    jsonb_build_object('winner', p_winner, 'loser', p_loser));
end $$;

revoke execute on function app.merge_entities(uuid, uuid, uuid)
  from public, anon, authenticated;
grant execute on function app.merge_entities(uuid, uuid, uuid)
  to postgres, service_role;

-- ── 4. meeting.person_entity_id foreign key ───────────────────────────────────
-- Orphans are cleared, not invented: a dangling person reference becomes null.
update public.meeting set person_entity_id = null
where person_entity_id is not null
  and not exists (select 1 from public.entity e where e.id = meeting.person_entity_id);

alter table public.meeting
  add constraint meeting_person_entity_fk foreign key (person_entity_id) references public.entity (id);
