-- Migration 0039: column runtime (P3).
--   * A predicate (column) has a fill method (extract or formula; provider, research and manual
--     are reserved for P5/P6), a fill_config ({"formula": "..."} for a formula), the predicates
--     it reads, an optional run condition, auto_run, a config_hash and a version that goes up
--     whenever the config_hash changes.
--   * Every fill of a cell is a predicate_run. A cell's state is computed from its latest run and
--     its active claim (app.cell_state): nine states, in the Global Constraints order.
--   * A formula writes derived claims against one source_record per workspace (derived:formula),
--     which extraction never picks up.
--   * Brief column specs gain fill_method, formula and inputs; publish syncs them to predicates.

-- ── 1. Predicate fill fields ─────────────────────────────────────────────────
alter table public.predicate
  add column fill_method         text not null default 'extract'
    constraint predicate_fill_method check (fill_method in ('extract', 'provider', 'research', 'formula', 'manual')),
  add column fill_config         jsonb not null default '{}',
  add column input_predicate_ids uuid[] not null default '{}',
  add column run_condition       jsonb,
  add column auto_run            boolean not null default false,
  add column version             int not null default 1 constraint predicate_version_positive check (version >= 1),
  add column config_hash         text;
alter table public.predicate add constraint predicate_formula_has_formula
  check (fill_method <> 'formula' or jsonb_typeof(fill_config -> 'formula') = 'string');

-- md5 of the canonical JSON of {fill_method, fill_config, input keys sorted, value_type,
-- allowed_values sorted}. Sorting is bytewise (collate "C") so every server agrees.
create function app.predicate_config_hash(p_fill_method text, p_fill_config jsonb, p_input_keys text[],
                                          p_value_type text, p_allowed jsonb) returns text
  language sql immutable set search_path = ''
as $$
  select md5(jsonb_build_object(
    'fill_method',    p_fill_method,
    'fill_config',    coalesce(p_fill_config, '{}'::jsonb),
    'inputs',         coalesce((select jsonb_agg(k order by k collate "C") from unnest(p_input_keys) k), '[]'::jsonb),
    'value_type',     p_value_type,
    'allowed_values', case when jsonb_typeof(p_allowed) = 'array'
                           then (select coalesce(jsonb_agg(e order by e #>> '{}' collate "C"), '[]'::jsonb)
                                   from jsonb_array_elements(p_allowed) e) end
  )::text)
$$;

-- Existing rows: no inputs yet, version 1.
update public.predicate
   set config_hash = app.predicate_config_hash(fill_method, fill_config, '{}', value_type, allowed_values);

-- Every write stamps config_hash; a changed hash moves version up by one.
create function app.predicate_config_stamp() returns trigger
  language plpgsql security definer set search_path = ''
as $$
declare
  v_keys text[];
begin
  select coalesce(array_agg(p.key), '{}') into v_keys
    from public.predicate p where p.id = any(new.input_predicate_ids);
  new.config_hash := app.predicate_config_hash(new.fill_method, new.fill_config, v_keys,
                                               new.value_type, new.allowed_values);
  if tg_op = 'UPDATE' then
    new.version := case when old.config_hash is not null and new.config_hash is distinct from old.config_hash
                         then old.version + 1 else old.version end;
  end if;
  return new;
end $$;

create trigger predicate_config_stamp before insert or update on public.predicate
  for each row execute function app.predicate_config_stamp();

-- ── 2. Runs ──────────────────────────────────────────────────────────────────
create table public.predicate_run (
  id                uuid primary key default gen_random_uuid(),
  org_id            uuid not null references public.organisation (id) on delete restrict,
  workspace_id      uuid not null references public.workspace (id) on delete restrict,
  entity_id         uuid not null references public.entity (id),
  predicate_id      uuid not null references public.predicate (id),
  predicate_version int not null,
  brief_version_id  uuid references public.brief_version (id),
  status            text not null default 'queued' constraint predicate_run_status check (status in
                      ('queued', 'running', 'succeeded', 'no_data', 'condition_not_met', 'error', 'cancelled')),
  inputs_hash       text,
  not_run_reason    jsonb,
  cost_usd          numeric(14,6) not null default 0 check (cost_usd >= 0),
  claim_id          uuid references public.claim (id),
  inputs            jsonb,                 -- the input claim ids the run used
  error             text,
  queued_at         timestamptz not null default now(),
  started_at        timestamptz,
  finished_at       timestamptz
);
create index predicate_run_cell_idx on public.predicate_run (entity_id, predicate_id, queued_at desc);
create index predicate_run_ws_idx on public.predicate_run (workspace_id, queued_at desc);
-- One queued-or-running run per cell.
create unique index predicate_run_one_open on public.predicate_run (entity_id, predicate_id)
  where status in ('queued', 'running');

alter table public.predicate_run enable row level security;
create policy predicate_run_read on public.predicate_run for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer'));
grant select on public.predicate_run to authenticated;
grant select on public.predicate_run to service_role;

alter table public.usage_ledger add column predicate_run_id uuid references public.predicate_run (id);
create index usage_ledger_run_idx on public.usage_ledger (predicate_run_id) where predicate_run_id is not null;

-- ── 3. Column spec checks (P2 checks kept; fill_method, formula, inputs, cycles added) ──
create or replace function app.validate_brief_columns(p_cfg jsonb) returns void
  language plpgsql immutable set search_path = ''
as $$
declare
  v_preds  jsonb := coalesce(nullif(p_cfg -> 'predicates', 'null'::jsonb), '[]'::jsonb);
  v_rules  jsonb := coalesce(nullif(p_cfg -> 'evidence_rules', 'null'::jsonb), '[]'::jsonb);
  v_types  jsonb := '{}'::jsonb;    -- key -> value_type of each spec
  v_enums  jsonb := '{}'::jsonb;    -- key -> allowed_values of each enum spec
  v_fills  jsonb := '{}'::jsonb;    -- key -> fill_method of each spec
  v_inputs jsonb := '{}'::jsonb;    -- formula key -> its input keys
  s        jsonb;
  r        jsonb;
  v_key    text;
  v_type   text;
  v_fill   text;
  v_op     text;
  v_val    jsonb;
  v_n      jsonb;
  v_in     text;
  v_cycle  text[];
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

    -- P3: fill method, formula and inputs. An absent (or null) field means the default.
    v_val := s -> 'fill_method';
    if v_val is null or v_val = 'null'::jsonb then
      v_fill := 'extract';
    elsif jsonb_typeof(v_val) = 'string' and v_val #>> '{}' in ('extract', 'formula') then
      v_fill := v_val #>> '{}';
    else
      raise exception 'column "%": fill_method must be extract or formula', v_key using errcode = '22023';
    end if;
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
    elsif v_val is not null and v_val <> 'null'::jsonb and v_val <> '[]'::jsonb then
      raise exception 'column "%": only a formula column has inputs', v_key using errcode = '22023';
    end if;

    v_types := v_types || jsonb_build_object(v_key, v_type);
    v_fills := v_fills || jsonb_build_object(v_key, v_fill);
  end loop;

  -- Each input is another extract or formula column of this Brief.
  for v_key in select k from jsonb_object_keys(v_inputs) k order by k collate "C" loop
    for v_in in select jsonb_array_elements_text(v_inputs -> v_key) loop
      if v_in = v_key then
        raise exception 'column "%": a formula cannot read its own column', v_key using errcode = '22023';
      end if;
      if not (v_fills ? v_in) then
        raise exception 'column "%": input "%" is not a column of this Brief', v_key, v_in using errcode = '22023';
      end if;
      if v_fills ->> v_in not in ('extract', 'formula') then
        raise exception 'column "%": input "%" must be an extract or formula column', v_key, v_in
          using errcode = '22023';
      end if;
    end loop;
  end loop;

  -- No dependency cycle among formula columns: report the shortest, starting at the first key.
  with recursive edges(src, dst) as (
    select e.key, i.value
      from jsonb_each(v_inputs) e, jsonb_array_elements_text(e.value) i
  ), walk(start, cur, path) as (
    select src, dst, array[src, dst] from edges
    union all
    select w.start, e.dst, w.path || e.dst
      from walk w join edges e on e.src = w.cur
     where w.cur <> w.start and not (e.dst = any(w.path[2:]))
  )
  select path into v_cycle from walk where cur = start
   order by array_length(path, 1), start collate "C" limit 1;
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
    -- Derived claims carry no quote, so they can never satisfy a rule.
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

-- ── 4. Save refuses a type clash (P2) and a fill-method clash (P3) ───────────
create or replace function public.save_brief_draft_ui(p_brief uuid, p_config jsonb) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_ws   uuid;
  v_key  text;
  v_type text;
  v_cols jsonb := case when jsonb_typeof(p_config -> 'predicates') = 'array'
                       then p_config -> 'predicates' else '[]'::jsonb end;
begin
  select workspace_id into v_ws from public.brief where id = p_brief;
  if not app.has_workspace_role(v_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  perform app.validate_brief_columns(p_config);
  select p.key, p.value_type into v_key, v_type
    from jsonb_array_elements(v_cols) e
    join public.predicate p on p.workspace_id = v_ws and p.key = e ->> 'key'
   where jsonb_typeof(e) = 'object' and p.value_type <> e ->> 'value_type'
   order by p.key
   limit 1;
  if v_key is not null then
    raise exception 'column "%" already exists in this workspace as %; give it another key',
      v_key, v_type using errcode = '23514';
  end if;
  select p.key, p.fill_method into v_key, v_type
    from jsonb_array_elements(v_cols) e
    join public.predicate p on p.workspace_id = v_ws and p.key = e ->> 'key'
   where jsonb_typeof(e) = 'object' and p.fill_method <> coalesce(e ->> 'fill_method', 'extract')
   order by p.key
   limit 1;
  if v_key is not null then
    raise exception 'column "%" already exists in this workspace filled by %; give it another key',
      v_key, v_type using errcode = '23514';
  end if;
  perform app.update_brief_draft(p_brief, p_config);
end $$;

-- ── 5. Predicate sync on publish (P2 behaviour kept) ─────────────────────────
-- Extract columns are synced in key order (as in P2), then formula columns in dependency order
-- (ready ones in key order), so each formula's inputs exist when it is written and its
-- config_hash is right the first time (no spurious version bump). input_predicate_ids are held in
-- input key order. A key held with another fill_method aborts the publish with 23514.
-- A formula column is created with auto_run true. A change of formula or inputs is audited as
-- predicate.fill_changed; the predicate's version moves with its config_hash (trigger).
create or replace function app.sync_brief_predicates() returns trigger
  language plpgsql security definer set search_path = ''
as $$
declare
  v_preds  jsonb := case when jsonb_typeof(new.config -> 'predicates') = 'array'
                         then new.config -> 'predicates' else '[]'::jsonb end;
  v_order  jsonb;
  v_done   text[];
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
begin
  perform app.validate_brief_columns(new.config);

  select coalesce(jsonb_agg(e order by e ->> 'key'), '[]'::jsonb) into v_order
    from jsonb_array_elements(v_preds) e
   where jsonb_typeof(e) = 'object' and coalesce(e ->> 'fill_method', 'extract') <> 'formula';
  select coalesce(array_agg(x ->> 'key'), '{}') into v_done from jsonb_array_elements(v_order) x;
  loop
    select e into s from jsonb_array_elements(v_preds) e
     where jsonb_typeof(e) = 'object' and e ->> 'fill_method' = 'formula'
       and not (e ->> 'key' = any(v_done))
       and not exists (select 1 from jsonb_array_elements_text(e -> 'inputs') i where not (i = any(v_done)))
     order by e ->> 'key' collate "C" limit 1;
    exit when not found;
    v_order := v_order || jsonb_build_array(s);
    v_done  := v_done || (s ->> 'key');
  end loop;

  for s in select e from jsonb_array_elements(v_order) e loop
    v_fill := coalesce(s ->> 'fill_method', 'extract');
    v_fcfg := case when v_fill = 'formula' then jsonb_build_object('formula', s ->> 'formula') else '{}'::jsonb end;
    select coalesce(array_agg(x.id order by x.key collate "C"), '{}') into v_ins
      from public.predicate x
     where v_fill = 'formula' and x.workspace_id = new.workspace_id
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

    -- A missing hint and an empty one are the same ("no hint"): no update for that alone.
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

    -- P3: formula and inputs (last publish wins).
    if p.fill_config is distinct from v_fcfg or p.input_predicate_ids is distinct from v_ins then
      update public.predicate set fill_config = v_fcfg, input_predicate_ids = v_ins
       where id = p.id returning version into v_ver;
      perform app.audit(new.org_id, new.workspace_id, 'predicate.fill_changed', 'predicate', p.id,
                        jsonb_build_object('fill_config', p.fill_config, 'input_predicate_ids', p.input_predicate_ids,
                                           'version', p.version),
                        jsonb_build_object('fill_config', v_fcfg, 'input_predicate_ids', v_ins,
                                           'version', v_ver, 'brief_version_id', new.id));
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

-- ── 6. Derived claims ────────────────────────────────────────────────────────
-- One source_record per workspace and kind. Its extraction_status is 'irrelevant' (status_detail
-- 'derived'): extraction picks up only 'pending' records and the column backfill only 'extracted'
-- ones, so neither ever touches it. Inserted directly, so no source_record.created is emitted.
create function app.derived_source_record(p_ws uuid, p_kind text) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org  uuid;
  v_id   uuid;
  v_hash text;
begin
  if p_kind is distinct from 'formula' then
    raise exception 'unknown derived source kind "%"', coalesce(p_kind, '') using errcode = '22023';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('formula', 'formula-v1', 'none', '1')
  on conflict (capability, prompt_version, model, parser_version) do nothing;

  v_hash := 'derived:' || p_kind || ':' || p_ws::text;
  insert into public.source_record
    (org_id, workspace_id, connector_id, source_type, fetched_at, content_hash, extraction_status, status_detail)
  values (v_org, p_ws, 'derived:' || p_kind, p_kind, now(), v_hash, 'irrelevant', 'derived')
  on conflict (org_id, content_hash) do nothing
  returning id into v_id;
  if v_id is null then
    select id into v_id from public.source_record where org_id = v_org and content_hash = v_hash;
  end if;
  return v_id;
end $$;

-- Writes a formula run's value as a derived claim. An active formula claim with the same value
-- is reused (no event), so a re-run with unchanged inputs cannot loop. Otherwise a new claim
-- (confidence 100, no quote) supersedes the cell's other active formula claims.
create function app.write_formula_claim(p_run uuid, p_value jsonb) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  r      public.predicate_run%rowtype;
  v_fill text;
  v_sr   uuid;
  v_ev   uuid;
  v_id   uuid;
begin
  select * into r from public.predicate_run where id = p_run for update;
  if not found then
    raise exception 'run not found' using errcode = 'P0002';
  end if;
  select fill_method into v_fill from public.predicate where id = r.predicate_id;
  if v_fill is distinct from 'formula' then
    raise exception 'run % is not a formula run', p_run using errcode = '22023';
  end if;
  if p_value is null or p_value = 'null'::jsonb then
    raise exception 'a formula claim needs a value (a null result is no_data)' using errcode = '22023';
  end if;
  -- One writer per cell at a time.
  perform pg_advisory_xact_lock(hashtextextended(r.entity_id::text || ':' || r.predicate_id::text, 0));

  v_sr := app.derived_source_record(r.workspace_id, 'formula');
  select id into v_ev from public.extractor_version
   where capability = 'formula' and prompt_version = 'formula-v1' and model = 'none' and parser_version = '1';

  select id into v_id from public.claim
   where entity_id = r.entity_id and predicate_id = r.predicate_id and source_record_id = v_sr
     and status = 'active' and value = p_value
   order by created_at, id limit 1;
  if v_id is null then
    v_id := app.create_claim(r.org_id, r.entity_id, r.predicate_id, p_value, null, null, null,
                             v_sr, now(), v_ev, 100::smallint);
    update public.claim set status = 'superseded', superseded_by = v_id
     where entity_id = r.entity_id and predicate_id = r.predicate_id and source_record_id = v_sr
       and status = 'active' and id <> v_id;
  end if;
  update public.predicate_run set claim_id = v_id where id = p_run;
  return v_id;
end $$;

-- ── 7. Inputs hash and cell state ────────────────────────────────────────────
-- The only implementation: md5(config_hash | sorted active input claim ids). For an extract
-- column (no inputs) it is md5(config_hash || '|').
create function app.run_inputs_hash(p_entity uuid, p_predicate uuid) returns text
  language sql stable security definer set search_path = ''
as $$
  select md5(coalesce(p.config_hash, '') || '|' ||
             coalesce((select string_agg(c.id::text, ',' order by c.id::text collate "C")
                         from public.claim c
                        where c.entity_id = p_entity and c.predicate_id = any(p.input_predicate_ids)
                          and c.status = 'active'), ''))
    from public.predicate p where p.id = p_predicate
$$;

-- Nine states, checked in order: queued / running, error, skipped, stale, derived, value,
-- no_data, empty. "Latest" is by queued_at desc, then id. The run that stale compares against is
-- the latest finished run that recorded an inputs_hash (a cancelled run never counts).
create function app.cell_state(p_entity uuid, p_predicate uuid) returns text
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_last text;
  v_hash text;
  v_fill text;
begin
  select status into v_last from public.predicate_run
   where entity_id = p_entity and predicate_id = p_predicate
   order by queued_at desc, id desc limit 1;
  if v_last in ('queued', 'running') then
    return v_last;
  elsif v_last = 'error' then
    return 'error';
  elsif v_last = 'condition_not_met' then
    return 'skipped';
  end if;
  if exists (select 1 from public.claim
              where entity_id = p_entity and predicate_id = p_predicate and status = 'active') then
    select inputs_hash into v_hash from public.predicate_run
     where entity_id = p_entity and predicate_id = p_predicate
       and finished_at is not null and inputs_hash is not null and status <> 'cancelled'
     order by queued_at desc, id desc limit 1;
    if v_hash is not null and v_hash is distinct from app.run_inputs_hash(p_entity, p_predicate) then
      return 'stale';
    end if;
    select fill_method into v_fill from public.predicate where id = p_predicate;
    return case when v_fill = 'formula' then 'derived' else 'value' end;
  end if;
  if v_last = 'no_data' then
    return 'no_data';
  end if;
  return 'empty';
end $$;

-- A grid of cells for a Viewer+: at most 500 entities × 50 predicates. Entities outside the
-- workspace's organisation and predicates outside the workspace (or global) are left out.
-- value / confidence / claim_id come from the latest active claim (observed_at desc, then id).
create function public.cells(p_ws uuid, p_entity_ids uuid[], p_predicate_ids uuid[])
returns table (entity_id uuid, predicate_id uuid, state text, value jsonb, confidence int, claim_id uuid,
               run_id uuid, run_status text, error text, not_run_reason jsonb, cost_usd numeric)
  language plpgsql stable security definer set search_path = ''
as $$
#variable_conflict use_column
declare
  v_org uuid;
begin
  if not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if coalesce(cardinality(p_entity_ids), 0) > 500 then
    raise exception 'ask for at most 500 entities at a time' using errcode = '22023';
  end if;
  if coalesce(cardinality(p_predicate_ids), 0) > 50 then
    raise exception 'ask for at most 50 columns at a time' using errcode = '22023';
  end if;
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  return query
  select e.id, p.id, app.cell_state(e.id, p.id), c.value, c.confidence::int, c.id,
         r.id, r.status, r.error, r.not_run_reason, r.cost_usd
    from (select u.id, min(u.o) as o from unnest(p_entity_ids) with ordinality u(id, o) group by u.id) eu
    join public.entity e on e.id = eu.id and e.org_id = v_org
    cross join lateral (select v.id, min(v.o) as o from unnest(p_predicate_ids) with ordinality v(id, o) group by v.id) pu
    join public.predicate p on p.id = pu.id and (p.workspace_id = p_ws or p.workspace_id is null)
    left join lateral (select cl.id, cl.value, cl.confidence from public.claim cl
                        where cl.entity_id = e.id and cl.predicate_id = p.id and cl.status = 'active'
                        order by cl.observed_at desc, cl.id desc limit 1) c on true
    left join lateral (select pr.id, pr.status, pr.error, pr.not_run_reason, pr.cost_usd from public.predicate_run pr
                        where pr.entity_id = e.id and pr.predicate_id = p.id
                        order by pr.queued_at desc, pr.id desc limit 1) r on true
   order by eu.o, pu.o;
end $$;

-- ── 8. Grants ────────────────────────────────────────────────────────────────
revoke execute on function
  app.predicate_config_hash(text, jsonb, text[], text, jsonb), app.predicate_config_stamp(),
  app.derived_source_record(uuid, text), app.write_formula_claim(uuid, jsonb),
  app.run_inputs_hash(uuid, uuid), app.cell_state(uuid, uuid)
  from public, anon, authenticated;
grant execute on function
  app.predicate_config_hash(text, jsonb, text[], text, jsonb),
  app.derived_source_record(uuid, text), app.write_formula_claim(uuid, jsonb),
  app.run_inputs_hash(uuid, uuid), app.cell_state(uuid, uuid)
  to postgres, service_role;

revoke execute on function public.cells(uuid, uuid[], uuid[]) from public, anon;
grant execute on function public.cells(uuid, uuid[], uuid[]) to authenticated;
