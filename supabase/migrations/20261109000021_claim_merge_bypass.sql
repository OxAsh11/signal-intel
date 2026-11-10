-- Migration 0021: allow app.merge_entities to re-point claim.entity_id.
--
-- The claim_immutable trigger (migration 0007) only allows status and
-- superseded_by to be updated. app.merge_entities line 274 must also
-- update entity_id when re-pointing claims from the loser to the winner.
-- This migration:
--   1. Adds app.is_merging() — returns true inside app.merge_entities.
--   2. Replaces claim_immutable with claim_guard, which allows entity_id
--      changes only when is_merging() is true.
--   3. Replaces app.merge_entities to set the flag before the claim update.

-- ── 1. is_merging() helper ────────────────────────────────────────────────────
create function app.is_merging() returns boolean
language sql stable set search_path = '' as $$
  select coalesce(current_setting('app.merging', true), '') = 'true'
$$;

revoke execute on function app.is_merging() from public, anon, authenticated;
-- authenticated needs execute so claim_guard (invoked on authenticated UPDATE) can call it.
grant execute on function app.is_merging() to postgres, service_role, authenticated;

-- ── 2. Replace claim_immutable trigger ───────────────────────────────────────
drop trigger claim_immutable on public.claim;

create function app.claim_guard() returns trigger
language plpgsql set search_path = '' as $$
declare
  allowed text[] := array['status', 'superseded_by'];
  col     text;
begin
  for col in select jsonb_object_keys(to_jsonb(new)) loop
    if col = 'entity_id' then
      -- entity_id may only change inside app.merge_entities
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

create trigger claim_immutable before update on public.claim
  for each row execute function app.claim_guard();

revoke execute on function app.claim_guard() from public, anon, authenticated;
grant execute on function app.claim_guard() to postgres, service_role;

-- ── 3. Replace app.merge_entities with merge-flag ────────────────────────────
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
end $$;

revoke execute on function app.merge_entities(uuid, uuid, uuid)
  from public, anon, authenticated;
grant execute on function app.merge_entities(uuid, uuid, uuid)
  to postgres, service_role;
