-- Migration 0038: Brief columns and rubric (P2).
--   * A Brief's config.predicates holds typed column specs (key, label, value_type, allowed_values,
--     extraction_hint, decay_half_life_days, role); config.evidence_rules holds the rules a verdict
--     is checked against. Legacy configs may hold plain key strings: "a key with no spec".
--   * Publishing a version syncs its column specs into the workspace predicate registry and asks
--     for a backfill of the keys the version added.
--   * A Brief created without a name is named by the AI intake (name_auto) until a person renames it.

-- ── 1. Event catalogue (reviewed addition) ───────────────────────────────────
alter type public.event_type add value 'predicate.backfill_requested';

-- ── 2. AI-named Briefs ───────────────────────────────────────────────────────
alter table public.brief add column name_auto boolean not null default false;

-- ── 3. Column spec and evidence rule checks ──────────────────────────────────
-- Raises 22023 naming the column for the first invalid spec or rule. A config without
-- predicates or evidence_rules, or with plain key strings, is valid.
create function app.validate_brief_columns(p_cfg jsonb) returns void
  language plpgsql immutable set search_path = ''
as $$
declare
  v_preds jsonb := coalesce(nullif(p_cfg -> 'predicates', 'null'::jsonb), '[]'::jsonb);
  v_rules jsonb := coalesce(nullif(p_cfg -> 'evidence_rules', 'null'::jsonb), '[]'::jsonb);
  v_types jsonb := '{}'::jsonb;    -- key -> value_type of each spec
  v_enums jsonb := '{}'::jsonb;    -- key -> allowed_values of each enum spec
  s       jsonb;
  r       jsonb;
  v_key   text;
  v_type  text;
  v_op    text;
  v_val   jsonb;
  v_n     jsonb;
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
      continue;                                   -- legacy: a key with no spec
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
    v_types := v_types || jsonb_build_object(v_key, v_type);
  end loop;

  for r in select value from jsonb_array_elements(v_rules) loop
    if jsonb_typeof(r) <> 'object' then
      raise exception 'each evidence rule must be an object' using errcode = '22023';
    end if;
    v_key := r ->> 'predicate';
    if v_key is null or not (v_types ? v_key) then
      raise exception 'column "%": an evidence rule points at a column this Brief does not define',
        coalesce(v_key, '') using errcode = '22023';
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

-- ── 4. Publish and save check the columns ────────────────────────────────────
create or replace function app.assert_brief_publishable(p_cfg jsonb) returns void
  language plpgsql immutable set search_path = ''
as $$
begin
  if coalesce(trim(p_cfg ->> 'offer'), '') = '' then
    raise exception 'draft_config.offer is required and cannot be empty' using errcode = '42501';
  end if;
  if coalesce(trim(p_cfg ->> 'hypothesis'), '') = '' then
    raise exception 'draft_config.hypothesis is required and cannot be empty' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'examples' -> 'positive', '[]'::jsonb)) < 3 then
    raise exception 'draft_config.examples.positive requires at least 3 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'examples' -> 'negative', '[]'::jsonb)) < 2 then
    raise exception 'draft_config.examples.negative requires at least 2 items' using errcode = '42501';
  end if;
  if jsonb_array_length(coalesce(p_cfg -> 'sources' -> 'radar_types', '[]'::jsonb)) < 1 then
    raise exception 'draft_config.sources.radar_types requires at least 1 item' using errcode = '42501';
  end if;
  perform app.validate_brief_columns(p_cfg);
end $$;

create or replace function public.save_brief_draft_ui(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare v_ws uuid;
begin
  select workspace_id into v_ws from public.brief where id = p_brief;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  perform app.validate_brief_columns(p_config);
  perform app.update_brief_draft(p_brief, p_config);
end $$;

-- ── 5. Predicate sync on publish ─────────────────────────────────────────────
-- Every new version's column specs become workspace predicates, in key order. A key that
-- already exists with another value_type aborts the publish (the whole transaction, so no
-- version, predicate or outbox row is left); an existing enum gains the new allowed values.
-- The keys this version added over the Brief's previous version are queued for backfill.
create function app.sync_brief_predicates() returns trigger
  language plpgsql security definer set search_path = ''
as $$
declare
  v_preds  jsonb := case when jsonb_typeof(new.config -> 'predicates') = 'array'
                         then new.config -> 'predicates' else '[]'::jsonb end;
  v_prev   jsonb;
  s        jsonb;
  p        public.predicate%rowtype;
  v_id     uuid;
  v_union  jsonb;
  v_added  text[];
begin
  perform app.validate_brief_columns(new.config);

  for s in select e from jsonb_array_elements(v_preds) e
            where jsonb_typeof(e) = 'object' order by e ->> 'key' loop
    select * into p from public.predicate
     where workspace_id = new.workspace_id and key = s ->> 'key' for update;
    if not found then
      v_id := null;
      insert into public.predicate
        (key, org_id, workspace_id, label, value_type, allowed_values, decay_half_life_days, extraction_hint)
      values
        (s ->> 'key', new.org_id, new.workspace_id, s ->> 'label', s ->> 'value_type',
         case when s ->> 'value_type' = 'enum' then s -> 'allowed_values' end,
         (s ->> 'decay_half_life_days')::int, coalesce(s ->> 'extraction_hint', ''))
      on conflict on constraint one_key_per_scope do nothing
      returning id into v_id;
      if v_id is not null then
        perform app.audit(new.org_id, new.workspace_id, 'predicate.created', 'predicate', v_id, null,
                          jsonb_build_object('key', s ->> 'key', 'value_type', s ->> 'value_type',
                                             'brief_version_id', new.id));
        continue;
      end if;
      -- Another transaction created it first: compare against that row.
      select * into p from public.predicate
       where workspace_id = new.workspace_id and key = s ->> 'key' for update;
    end if;

    if p.value_type <> s ->> 'value_type' then
      raise exception 'column "%" already exists in this workspace as %; give it another key',
        p.key, p.value_type using errcode = '23514';
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
  end if;
  return null;
end $$;

create trigger brief_version_sync_predicates after insert on public.brief_version
  for each row execute function app.sync_brief_predicates();

-- ── 6. Names ─────────────────────────────────────────────────────────────────
drop function public.create_brief(uuid, text, text);
drop function app.create_brief(uuid, text, text);

create function app.create_brief(p_ws uuid, p_name text, p_description text default null,
                                 p_name_auto boolean default false) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org   uuid;
  v_brief uuid;
  v_desc  text := nullif(btrim(coalesce(p_description, '')), '');
begin
  if not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if length(v_desc) > 4000 then
    raise exception 'keep the description under 4000 characters' using errcode = '22023';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.brief (org_id, workspace_id, name, name_auto, state, intake_status, created_by)
  values (v_org, p_ws, p_name, coalesce(p_name_auto, false), 'Draft', 'pending', app.current_member_id(v_org))
  returning id into v_brief;
  perform app.audit(v_org, p_ws, 'brief.created', 'brief', v_brief, null,
                    jsonb_build_object('name', p_name, 'name_auto', coalesce(p_name_auto, false)));
  perform app.emit('brief.intake_requested'::public.event_type, v_org, p_ws, 'brief', v_brief,
                   jsonb_build_object('brief_id', v_brief, 'name', p_name,
                                      'description', coalesce(v_desc, p_name),
                                      'name_auto', coalesce(p_name_auto, false),
                                      'org_id', v_org, 'workspace_id', p_ws));
  return v_brief;
end $$;

create function public.create_brief(p_ws uuid, p_name text, p_description text default null,
                                    p_name_auto boolean default false) returns uuid
  language plpgsql security definer set search_path = ''
as $$
begin
  return app.create_brief(p_ws, p_name, p_description, p_name_auto);
end $$;

-- The intake's drafted name, applied only while the Brief still carries an automatic name.
-- A name taken in the workspace gets " 2", " 3"… Returns the Brief's name afterwards.
create function app.apply_drafted_name(p_brief uuid, p_name text) returns text
  language plpgsql security definer set search_path = ''
as $$
declare
  b      public.brief%rowtype;
  v_base text := left(regexp_replace(btrim(coalesce(p_name, '')), '\s+', ' ', 'g'), 190);
  v_name text;
  v_n    int := 2;
begin
  select * into b from public.brief where id = p_brief for update;
  if not found then
    raise exception 'brief not found' using errcode = 'P0002';
  end if;
  if not b.name_auto or v_base = '' then
    return b.name;
  end if;
  v_name := v_base;
  while exists (select 1 from public.brief
                 where workspace_id = b.workspace_id and name = v_name and id <> p_brief) loop
    v_name := v_base || ' ' || v_n;
    v_n := v_n + 1;
  end loop;
  update public.brief set name = v_name, name_auto = false, updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.renamed', 'brief', p_brief,
                    jsonb_build_object('name', b.name, 'name_auto', true),
                    jsonb_build_object('name', v_name, 'name_auto', false, 'by', 'intake'));
  return v_name;
end $$;

create function public.rename_brief(p_brief uuid, p_name text) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  b      public.brief%rowtype;
  v_name text := btrim(coalesce(p_name, ''));
begin
  select * into b from public.brief where id = p_brief for update;
  if not found or not app.has_workspace_role(b.workspace_id, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if v_name = '' or length(v_name) > 200 then
    raise exception 'a Brief name needs 1–200 characters' using errcode = '22023';
  end if;
  if exists (select 1 from public.brief
              where workspace_id = b.workspace_id and name = v_name and id <> p_brief) then
    raise exception 'another Brief in this workspace is already called "%"', v_name using errcode = '23505';
  end if;
  update public.brief set name = v_name, name_auto = false, updated_at = now() where id = p_brief;
  perform app.audit(b.org_id, b.workspace_id, 'brief.renamed', 'brief', p_brief,
                    jsonb_build_object('name', b.name, 'name_auto', b.name_auto),
                    jsonb_build_object('name', v_name, 'name_auto', false));
end $$;

-- ── 7. Grants (as migration 0035) ────────────────────────────────────────────
revoke execute on function
  app.validate_brief_columns(jsonb), app.sync_brief_predicates(),
  app.create_brief(uuid, text, text, boolean), app.apply_drafted_name(uuid, text)
  from public, anon, authenticated;
grant execute on function
  app.create_brief(uuid, text, text, boolean), app.apply_drafted_name(uuid, text)
  to postgres, service_role;

revoke execute on function
  public.create_brief(uuid, text, text, boolean), public.rename_brief(uuid, text)
  from public, anon;
grant execute on function
  public.create_brief(uuid, text, text, boolean), public.rename_brief(uuid, text)
  to authenticated;
