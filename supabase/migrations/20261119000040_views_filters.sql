-- Migration 0040: one filter syntax, account views, saved views (P3).
--   * app.filter_where is the only filter compiler. It turns the shared filter AST into a SQL
--     boolean over the aliases e (entity) and a (account). Every value is quoted with %L; keys
--     resolve to predicate ids, which are quoted too. Errors are 22023 and name the JSON path.
--   * The AST is the Global Constraints one, plus one run leaf: {"field": {"run": "*"}, "op":
--     "one_of", "value": [states]} holds when ANY column in scope is in one of the states. The
--     scope is the given Brief's columns (its current version), else every workspace predicate
--     plus the global ones a workspace key does not shadow (as app.view_predicate resolves keys).
--     It compiles to one exists; the stale and errored presets use it.
--   * Limits: at most 50 conditions, depth at most 8 and at most 200 nodes (groups and {} count).
--   * public.view_entities lists the company accounts of a workspace through a filter and a sort.
--   * public.saved_view holds named views; public.list_views returns the five presets, then the
--     saved views the caller can see.

-- ── 1. Brief context ─────────────────────────────────────────────────────────
-- A condition over alias b (public.brief): the given Brief, or any Pilot or Active Brief of the
-- workspace when none is given. Verdict, band, priority and verdict sort join b on its current version.
create function app.view_brief_ctx(p_ws uuid, p_brief uuid) returns text
  language sql stable set search_path = ''
as $$
  select case when p_brief is not null then format('b.id = %L::uuid', p_brief)
              else format('b.workspace_id = %L::uuid and b.state in (''Pilot'', ''Active'')', p_ws) end
$$;

-- The predicate a column key names in a workspace: the workspace's own, else a global one.
create function app.view_predicate(p_ws uuid, p_key jsonb) returns public.predicate
  language sql stable security definer set search_path = ''
as $$
  select p.* from public.predicate p
   where jsonb_typeof(p_key) = 'string' and p.key = p_key #>> '{}'
     and (p.workspace_id = p_ws or p.workspace_id is null)
   order by p.workspace_id nulls last
   limit 1
$$;

-- The first key of p_obj (bytewise order) that is not in p_allowed, or null.
create function app.filter_unknown_key(p_obj jsonb, p_allowed text[]) returns text
  language sql immutable set search_path = ''
as $$
  select k from jsonb_object_keys(p_obj) k where not (k = any(p_allowed)) order by k collate "C" limit 1
$$;

-- The predicates a Brief's current version names as columns (spec objects or legacy keys),
-- resolved as app.view_predicate does. Empty when the Brief has no published version.
create function app.brief_column_ids(p_ws uuid, p_brief uuid) returns uuid[]
  language sql stable security definer set search_path = ''
as $$
  select coalesce(array_agg(distinct v.id) filter (where v.id is not null), '{}')
    from public.brief b
    join public.brief_version bv on bv.id = b.current_version_id
    cross join lateral jsonb_array_elements(case when jsonb_typeof(bv.config -> 'predicates') = 'array'
                                                 then bv.config -> 'predicates' else '[]'::jsonb end) c
    cross join lateral (select (app.view_predicate(p_ws, case jsonb_typeof(c) when 'string' then c
                                                              else c -> 'key' end)).id) v(id)
   where b.id = p_brief and b.workspace_id = p_ws
$$;

-- A run-state test, set-based: one exists over the predicates p_source yields (pid, fill), each
-- joined to its latest run (queued_at desc, then id desc). The state follows app.cell_state
-- exactly, in its order: queued / running, error, skipped (condition_not_met), then — only for
-- a cell with an active claim — stale (its latest finished, non-cancelled run with an
-- inputs_hash differs from app.run_inputs_hash now), derived (formula) or value; then no_data;
-- else empty. app.run_inputs_hash is called only for those candidate cells, and only when the
-- asked states include stale, derived or value. States that depend on the latest run alone
-- compile to a test of its status.
create function app.filter_run_sql(p_source text, p_states text[]) returns text
  language sql immutable set search_path = ''
as $$
  select format(
    'exists (select 1 from (%s) fp left join lateral (select pr.status from public.predicate_run pr'
    ' where pr.entity_id = e.id and pr.predicate_id = fp.pid order by pr.queued_at desc, pr.id desc limit 1) lr on true'
    ' where %s)',
    p_source,
    case when p_states <@ array['queued', 'running', 'error', 'skipped'] then
      format('lr.status = any(%L::text[])', array_replace(p_states, 'skipped', 'condition_not_met'))
    else format(
      '(case when lr.status in (''queued'', ''running'') then lr.status'
      ' when lr.status = ''error'' then ''error'''
      ' when lr.status = ''condition_not_met'' then ''skipped'''
      ' when exists (select 1 from public.claim fc where fc.entity_id = e.id and fc.predicate_id = fp.pid'
      ' and fc.status = ''active'') then %s'
      ' when lr.status = ''no_data'' then ''no_data'''
      ' else ''empty'' end) = any(%L::text[])',
      case when p_states && array['stale', 'derived', 'value'] then
        '(case when (select fr.inputs_hash is distinct from app.run_inputs_hash(e.id, fp.pid)'
        ' from public.predicate_run fr where fr.entity_id = e.id and fr.predicate_id = fp.pid'
        ' and fr.finished_at is not null and fr.inputs_hash is not null and fr.status <> ''cancelled'''
        ' order by fr.queued_at desc, fr.id desc limit 1) then ''stale'''
        ' when fp.fill = ''formula'' then ''derived'' else ''value'' end)'
      else '''value''' end,
      p_states) end)
$$;

-- ── 2. The compiler ──────────────────────────────────────────────────────────
-- One node of the AST at p_path and depth p_depth (the root is depth 1). p_count / p_nodes are the
-- conditions / nodes compiled before this node; o_count / o_nodes are those numbers after it.
create function app.filter_node(p_ws uuid, p_brief uuid, p_node jsonb, p_path text, p_depth int, p_count int,
                                p_nodes int, out o_sql text, out o_count int, out o_nodes int)
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_key    text;
  v_kids   jsonb;
  v_parts  text[] := '{}';
  v_child  record;
  v_i      int;
  v_field  jsonb;
  v_op     text;
  v_val    jsonb;
  v_has    boolean;
  v_list   jsonb;
  v_el     jsonb;
  v_pred   public.predicate;
  v_type   text;
  v_sys    text;
  v_ops    text[];
  v_all    text[] := array['is_empty', 'is_not_empty', 'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'contains',
                           'one_of', 'is_true', 'is_false', 'within_days', 'older_than_days'];
  v_states text[] := array['empty', 'queued', 'running', 'value', 'derived', 'no_data', 'skipped', 'error', 'stale'];
  v_want   text;
  v_test   text;
  v_ctx    text;
  v_src    text;
begin
  o_nodes := p_nodes + 1;
  if o_nodes > 200 then
    raise exception '%: a filter has at most 200 nodes', p_path using errcode = '22023';
  end if;
  if p_depth > 8 then
    raise exception '%: a filter nests at most 8 levels deep', p_path using errcode = '22023';
  end if;
  if jsonb_typeof(p_node) is distinct from 'object' then
    raise exception '%: a filter must be an object', p_path using errcode = '22023';
  end if;
  o_count := p_count;

  if p_node = '{}'::jsonb then
    o_sql := 'true';
    return;
  end if;

  -- ── Groups ──
  if not (p_node ? 'field') then
    v_key := app.filter_unknown_key(p_node, array['all', 'any', 'not']);
    if v_key is not null then
      raise exception '%: unknown key "%"', p_path, v_key using errcode = '22023';
    end if;
    if (select count(*) from jsonb_object_keys(p_node)) > 1 then
      raise exception '%: a group has exactly one of all, any or not', p_path using errcode = '22023';
    end if;
    if p_node ? 'not' then
      select * into v_child from app.filter_node(p_ws, p_brief, p_node -> 'not', p_path || '.not', p_depth + 1,
                                                 o_count, o_nodes);
      o_count := v_child.o_count;
      o_nodes := v_child.o_nodes;
      o_sql := format('(not %s)', v_child.o_sql);
      return;
    end if;
    v_key := case when p_node ? 'all' then 'all' else 'any' end;
    v_kids := p_node -> v_key;
    if jsonb_typeof(v_kids) is distinct from 'array' then
      raise exception '%.%: must be a list of filters', p_path, v_key using errcode = '22023';
    end if;
    for v_i in 0 .. jsonb_array_length(v_kids) - 1 loop
      select * into v_child from app.filter_node(p_ws, p_brief, v_kids -> v_i,
                                                 format('%s.%s[%s]', p_path, v_key, v_i), p_depth + 1,
                                                 o_count, o_nodes);
      o_count := v_child.o_count;
      o_nodes := v_child.o_nodes;
      v_parts := v_parts || v_child.o_sql;
    end loop;
    if cardinality(v_parts) = 0 then
      o_sql := case when v_key = 'all' then 'true' else 'false' end;
    else
      o_sql := '(' || array_to_string(v_parts, case when v_key = 'all' then ' and ' else ' or ' end) || ')';
    end if;
    return;
  end if;

  -- ── A condition ──
  o_count := p_count + 1;
  if o_count > 50 then
    raise exception '%: a filter has at most 50 conditions', p_path using errcode = '22023';
  end if;
  v_key := app.filter_unknown_key(p_node, array['field', 'op', 'value']);
  if v_key is not null then
    raise exception '%: unknown key "%"', p_path, v_key using errcode = '22023';
  end if;
  v_field := p_node -> 'field';
  v_op := case when jsonb_typeof(p_node -> 'op') = 'string' then p_node ->> 'op' end;
  v_val := p_node -> 'value';
  v_has := v_val is not null and v_val <> 'null'::jsonb;
  if jsonb_typeof(v_field) is distinct from 'object' then
    raise exception '%.field: must be {"column": key}, {"system": name} or {"run": key}', p_path using errcode = '22023';
  end if;
  if v_op is null then
    raise exception '%.op: an op is required', p_path using errcode = '22023';
  end if;
  if not (v_op = any(v_all)) then
    raise exception '%.op: unknown op "%"', p_path, v_op using errcode = '22023';
  end if;
  -- one_of always takes a non-empty list; its elements are checked per field below.
  if v_op = 'one_of' and (jsonb_typeof(v_val) is distinct from 'array' or jsonb_array_length(v_val) = 0) then
    raise exception '%.value: one_of needs a list of values', p_path using errcode = '22023';
  end if;
  if v_op in ('is_empty', 'is_not_empty', 'is_true', 'is_false') and v_has then
    raise exception '%.value: % takes no value', p_path, v_op using errcode = '22023';
  end if;
  v_ctx := app.view_brief_ctx(p_ws, p_brief);

  -- ── {"column": key} ──
  if v_field ? 'column' then
    v_key := app.filter_unknown_key(v_field, array['column']);
    if v_key is not null then
      raise exception '%.field: unknown key "%"', p_path, v_key using errcode = '22023';
    end if;
    v_pred := app.view_predicate(p_ws, v_field -> 'column');
    if v_pred.id is null then
      raise exception '%.field: unknown column "%"', p_path, coalesce(v_field ->> 'column', '') using errcode = '22023';
    end if;
    v_type := v_pred.value_type;
    v_ops := case v_type
      when 'boolean' then array['is_empty', 'is_not_empty', 'eq', 'neq', 'one_of', 'is_true', 'is_false']
      when 'number'  then array['is_empty', 'is_not_empty', 'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'one_of']
      when 'date'    then array['is_empty', 'is_not_empty', 'eq', 'neq', 'gt', 'gte', 'lt', 'lte', 'one_of']
      when 'enum'    then array['is_empty', 'is_not_empty', 'eq', 'neq', 'contains', 'one_of']
      when 'text'    then array['is_empty', 'is_not_empty', 'eq', 'neq', 'contains', 'one_of']
      else                array['is_empty', 'is_not_empty', 'eq', 'neq', 'one_of'] end;
    if not (v_op = any(v_ops)) then
      raise exception '%.op: % does not apply to % column "%"', p_path, v_op, v_type, v_pred.key using errcode = '22023';
    end if;

    if v_op in ('is_empty', 'is_not_empty') then
      o_sql := format('(%s exists (select 1 from public.claim fc where fc.entity_id = e.id and fc.predicate_id = %L::uuid'
                      ' and fc.status = ''active''))',
                      case when v_op = 'is_empty' then 'not' else '' end, v_pred.id);
      return;
    end if;

    -- The value(s) must fit the column type: contains takes text; the rest take the column's type.
    v_list := case when v_op = 'one_of' then v_val else jsonb_build_array(v_val) end;
    if v_op not in ('is_true', 'is_false') then
      for v_i in 0 .. jsonb_array_length(v_list) - 1 loop
        v_el := v_list -> v_i;
        v_want := case when v_op = 'contains' then 'text' else v_type end;
        if not (case v_want
                  when 'boolean' then jsonb_typeof(v_el) = 'boolean'
                  when 'number'  then jsonb_typeof(v_el) = 'number'
                  when 'date'    then jsonb_typeof(v_el) = 'string' and (v_el #>> '{}') ~ '^\d{4}-\d{2}-\d{2}$'
                                      and app.view_is_date(v_el #>> '{}')
                  else jsonb_typeof(v_el) is not distinct from 'string' end) then
          raise exception '%: % needs %',
            case when v_op = 'one_of' then format('%s.value[%s]', p_path, v_i) else p_path || '.value' end,
            v_op,
            case v_want when 'boolean' then 'true or false' when 'number' then 'a number'
                        when 'date' then 'a date (YYYY-MM-DD)' else 'text' end
            using errcode = '22023';
        end if;
        if v_type = 'enum' and v_op <> 'contains' and not (coalesce(v_pred.allowed_values, '[]'::jsonb) @> jsonb_build_array(v_el)) then
          raise exception '%: "%" is not a value of column "%"',
            case when v_op = 'one_of' then format('%s.value[%s]', p_path, v_i) else p_path || '.value' end,
            v_el #>> '{}', v_pred.key using errcode = '22023';
        end if;
      end loop;
    end if;

    v_test := case v_op
      when 'eq'       then format('fc.value = %L::jsonb', v_val)
      when 'neq'      then format('fc.value <> %L::jsonb', v_val)
      when 'one_of'   then format('%L::jsonb @> fc.value', v_val)
      when 'is_true'  then 'fc.value = ''true''::jsonb'
      when 'is_false' then 'fc.value = ''false''::jsonb'
      when 'contains' then format('strpos(lower(fc.value #>> ''{}''), %L) > 0', lower(v_val #>> '{}'))
      else format(case when v_type = 'number' then '(fc.value)::numeric %s %L::numeric'
                       else '(fc.value #>> ''{}'')::date %s %L::date' end,
                  case v_op when 'gt' then '>' when 'gte' then '>=' when 'lt' then '<' else '<=' end,
                  v_val #>> '{}') end;
    o_sql := format('exists (select 1 from public.claim fc where fc.entity_id = e.id and fc.predicate_id = %L::uuid'
                    ' and fc.status = ''active'' and %s)', v_pred.id, v_test);
    return;
  end if;

  -- ── {"run": key} and {"run": "*"} (any column in scope) ──
  if v_field ? 'run' then
    v_key := app.filter_unknown_key(v_field, array['run']);
    if v_key is not null then
      raise exception '%.field: unknown key "%"', p_path, v_key using errcode = '22023';
    end if;
    if v_field -> 'run' = '"*"'::jsonb then
      v_src := case when p_brief is not null
        then format('select p.id as pid, p.fill_method as fill from public.predicate p where p.id = any(%L::uuid[])',
                    app.brief_column_ids(p_ws, p_brief))
        else format('select p.id as pid, p.fill_method as fill from public.predicate p where p.workspace_id = %L::uuid'
                    ' or (p.workspace_id is null and not exists (select 1 from public.predicate w'
                    ' where w.workspace_id = %L::uuid and w.key = p.key))', p_ws, p_ws) end;
    else
      v_pred := app.view_predicate(p_ws, v_field -> 'run');
      if v_pred.id is null then
        raise exception '%.field: unknown column "%"', p_path, coalesce(v_field ->> 'run', '') using errcode = '22023';
      end if;
      v_src := format('select p.id as pid, p.fill_method as fill from public.predicate p where p.id = %L::uuid', v_pred.id);
    end if;
    if v_op <> 'one_of' then
      raise exception '%.op: % does not apply to a run state', p_path, v_op using errcode = '22023';
    end if;
    for v_i in 0 .. jsonb_array_length(v_val) - 1 loop
      v_el := v_val -> v_i;
      if jsonb_typeof(v_el) <> 'string' or not ((v_el #>> '{}') = any(v_states)) then
        raise exception '%.value[%]: "%" is not a cell state', p_path, v_i, v_el #>> '{}' using errcode = '22023';
      end if;
    end loop;
    o_sql := app.filter_run_sql(v_src, array(select distinct jsonb_array_elements_text(v_val) order by 1));
    return;
  end if;

  -- ── {"system": name} ──
  if not (v_field ? 'system') then
    raise exception '%.field: must be {"column": key}, {"system": name} or {"run": key}', p_path using errcode = '22023';
  end if;
  v_sys := case when jsonb_typeof(v_field -> 'system') = 'string' then v_field ->> 'system' end;
  if v_sys is null or v_sys not in ('verdict', 'band', 'stage', 'label', 'name', 'domain', 'created_at') then
    raise exception '%.field: unknown system field "%"', p_path, coalesce(v_field ->> 'system', '') using errcode = '22023';
  end if;
  v_key := app.filter_unknown_key(v_field, case when v_sys = 'label' then array['system', 'kind'] else array['system'] end);
  if v_key is not null then
    raise exception '%.field: unknown key "%"', p_path, v_key using errcode = '22023';
  end if;
  v_ops := case
    when v_sys in ('verdict', 'band', 'stage', 'label') then array['one_of']
    when v_sys in ('name', 'domain') then array['eq', 'contains', 'is_empty', 'is_not_empty']
    else array['within_days', 'older_than_days'] end;
  if not (v_op = any(v_ops)) then
    raise exception '%.op: % does not apply to %', p_path, v_op, v_sys using errcode = '22023';
  end if;

  if v_sys in ('verdict', 'band', 'stage', 'label') then
    if v_sys = 'label' and (jsonb_typeof(v_field -> 'kind') is distinct from 'string'
                            or length(btrim(v_field ->> 'kind')) = 0) then
      raise exception '%.field.kind: a label filter needs a kind', p_path using errcode = '22023';
    end if;
    for v_i in 0 .. jsonb_array_length(v_val) - 1 loop
      v_el := v_val -> v_i;
      if jsonb_typeof(v_el) <> 'string' then
        raise exception '%.value[%]: one_of needs text', p_path, v_i using errcode = '22023';
      end if;
      if v_sys = 'verdict' and not ((v_el #>> '{}') = any(enum_range(null::public.verdict_value)::text[])) then
        raise exception '%.value[%]: "%" is not a verdict', p_path, v_i, v_el #>> '{}' using errcode = '22023';
      elsif v_sys = 'band' and not ((v_el #>> '{}') = any(enum_range(null::public.band)::text[])) then
        raise exception '%.value[%]: "%" is not a band', p_path, v_i, v_el #>> '{}' using errcode = '22023';
      elsif v_sys = 'stage' and not ((v_el #>> '{}') = any(enum_range(null::public.stage)::text[])) then
        raise exception '%.value[%]: "%" is not a stage', p_path, v_i, v_el #>> '{}' using errcode = '22023';
      end if;
    end loop;
    o_sql := case v_sys
      when 'verdict' then format('exists (select 1 from public.verdict fv join public.brief b on b.current_version_id = fv.brief_version_id'
                                 ' where %s and fv.entity_id = e.id and %L::jsonb @> to_jsonb(fv.value::text))', v_ctx, v_val)
      when 'band'    then format('exists (select 1 from public.score fs join public.brief b on b.current_version_id = fs.brief_version_id'
                                 ' where %s and fs.account_id = a.id and %L::jsonb @> to_jsonb(fs.band::text))', v_ctx, v_val)
      when 'stage'   then format('(%L::jsonb @> to_jsonb(a.stage::text))', v_val)
      else format('exists (select 1 from public.label fl where fl.account_id = a.id and fl.kind = %L'
                  ' and %L::jsonb @> to_jsonb(fl.value)%s)', v_field ->> 'kind', v_val,
                  case when p_brief is null then ''
                       else format(' and (fl.brief_version_id is null or fl.brief_version_id ='
                                   ' (select b.current_version_id from public.brief b where b.id = %L::uuid))', p_brief) end) end;
    return;
  end if;

  if v_sys in ('name', 'domain') then
    if v_op in ('eq', 'contains') and jsonb_typeof(v_val) is distinct from 'string' then
      raise exception '%.value: % needs text', p_path, v_op using errcode = '22023';
    end if;
    v_test := case v_op
      when 'eq'       then format('= %L', lower(v_val #>> '{}'))
      when 'contains' then format('%L', lower(v_val #>> '{}')) end;
    if v_sys = 'name' then
      o_sql := case v_op
        when 'eq'           then format('(lower(e.display_name) %s)', v_test)
        when 'contains'     then format('(strpos(lower(e.display_name), %s) > 0)', v_test)
        when 'is_empty'     then '(btrim(e.display_name) = '''')'
        else                     '(btrim(e.display_name) <> '''')' end;
    else
      o_sql := format('(%s exists (select 1 from public.entity_alias fa where fa.entity_id = e.id and fa.alias_type = ''domain''%s))',
                      case when v_op = 'is_empty' then 'not' else '' end,
                      case v_op
                        when 'eq'       then ' and lower(fa.value_normalised) ' || v_test
                        when 'contains' then format(' and strpos(lower(fa.value_normalised), %s) > 0', v_test)
                        else '' end);
    end if;
    return;
  end if;

  -- created_at: the account's.
  if jsonb_typeof(v_val) is distinct from 'number' or v_val::numeric <> trunc(v_val::numeric)
     or v_val::numeric not between 0 and 36500 then
    raise exception '%.value: % needs a whole number of days (0–36500)', p_path, v_op using errcode = '22023';
  end if;
  o_sql := format('(a.created_at %s now() - make_interval(days => %L::int))',
                  case when v_op = 'within_days' then '>=' else '<' end, v_val::numeric::int);
end $$;

create function app.view_is_date(p_text text) returns boolean
  language plpgsql immutable set search_path = ''
as $$
begin
  perform p_text::date;
  return true;
exception when others then
  return false;
end $$;

-- The only filter compiler. A null filter matches everything.
create function app.filter_where(p_ws uuid, p_brief uuid, p_filter jsonb, p_path text default 'filter') returns text
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_path text := coalesce(p_path, 'filter');
  r      record;
begin
  if p_brief is not null and not exists (select 1 from public.brief where id = p_brief and workspace_id = p_ws) then
    raise exception '%: the Brief is not in this workspace', v_path using errcode = '22023';
  end if;
  if p_filter is null or p_filter = 'null'::jsonb then
    return 'true';
  end if;
  select * into r from app.filter_node(p_ws, p_brief, p_filter, v_path, 1, 0, 0);
  return r.o_sql;
end $$;

-- ── 3. Sort ──────────────────────────────────────────────────────────────────
-- An ORDER BY list over e and a. Empty values sort last in both directions; name asc, then id,
-- break ties.
create function app.view_order(p_ws uuid, p_brief uuid, p_sort jsonb) returns text
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_ctx   text := app.view_brief_ctx(p_ws, p_brief);
  v_parts text[] := '{}';
  v_item  jsonb;
  v_i     int;
  v_key   text;
  v_dir   text;
  v_expr  text;
  v_pred  public.predicate;
begin
  if p_sort is null or p_sort = 'null'::jsonb then
    p_sort := '[]';
  end if;
  if jsonb_typeof(p_sort) <> 'array' then
    raise exception 'sort: must be a list' using errcode = '22023';
  end if;
  if jsonb_array_length(p_sort) > 5 then
    raise exception 'sort: at most 5 sort keys' using errcode = '22023';
  end if;
  for v_i in 0 .. jsonb_array_length(p_sort) - 1 loop
    v_item := p_sort -> v_i;
    if jsonb_typeof(v_item) <> 'object' or (v_item ? 'system') = (v_item ? 'column') then
      raise exception 'sort[%]: must be {"system": name, "dir": ...} or {"column": key, "dir": ...}', v_i
        using errcode = '22023';
    end if;
    v_key := app.filter_unknown_key(v_item, array['system', 'column', 'dir']);
    if v_key is not null then
      raise exception 'sort[%]: unknown key "%"', v_i, v_key using errcode = '22023';
    end if;
    v_dir := coalesce(nullif(v_item -> 'dir', 'null'::jsonb), '"asc"'::jsonb) #>> '{}';
    if jsonb_typeof(coalesce(nullif(v_item -> 'dir', 'null'::jsonb), '"asc"'::jsonb)) <> 'string'
       or v_dir not in ('asc', 'desc') then
      raise exception 'sort[%].dir: must be asc or desc', v_i using errcode = '22023';
    end if;
    if v_item ? 'system' then
      v_key := case when jsonb_typeof(v_item -> 'system') = 'string' then v_item ->> 'system' end;
      v_expr := case v_key
        when 'name'       then 'lower(e.display_name)'
        when 'created_at' then 'a.created_at'
        when 'priority'   then format('(select max(fs.priority) from public.score fs join public.brief b'
                                      ' on b.current_version_id = fs.brief_version_id where %s and fs.account_id = a.id)', v_ctx)
        when 'verdict'    then format('(select min(array_position(enum_range(null::public.verdict_value), fv.value))'
                                      ' from public.verdict fv join public.brief b on b.current_version_id = fv.brief_version_id'
                                      ' where %s and fv.entity_id = e.id)', v_ctx) end;
      if v_expr is null then
        raise exception 'sort[%].system: unknown sort "%"', v_i, coalesce(v_item ->> 'system', '') using errcode = '22023';
      end if;
    else
      v_pred := app.view_predicate(p_ws, v_item -> 'column');
      if v_pred.id is null then
        raise exception 'sort[%].column: unknown column "%"', v_i, coalesce(v_item ->> 'column', '') using errcode = '22023';
      end if;
      v_expr := format('(select fc.value from public.claim fc where fc.entity_id = e.id and fc.predicate_id = %L::uuid'
                       ' and fc.status = ''active'' order by fc.observed_at desc, fc.id desc limit 1)', v_pred.id);
    end if;
    v_parts := v_parts || format('%s %s nulls last', v_expr, v_dir);
  end loop;
  return array_to_string(v_parts || array['lower(e.display_name) asc', 'e.id asc'], ', ');
end $$;

-- ── 4. The view query ────────────────────────────────────────────────────────
-- The company entities that have an account in the workspace, filtered, sorted and paged.
-- total is the number of matches before paging.
create function public.view_entities(p_ws uuid, p_brief uuid, p_filter jsonb, p_sort jsonb, p_limit int, p_offset int,
                                     p_entity_type public.entity_type default 'company')
returns table (entity_id uuid, total bigint)
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
  if p_entity_type = 'person' then
    raise exception 'person views come with People (P6)' using errcode = '22023';
  elsif p_entity_type is distinct from 'company' then
    raise exception 'only company views are available' using errcode = '22023';
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
  return query execute format(
    'select e.id, count(*) over () from public.account a join public.entity e on e.id = a.entity_id'
    ' where a.workspace_id = $1 and e.org_id = $4 and e.entity_type = ''company'' and e.status = ''active'' and %s'
    ' order by %s limit $2 offset $3', v_where, v_order)
    using p_ws, v_limit, v_offset, v_org;
end $$;

-- ── 5. Saved views ───────────────────────────────────────────────────────────
create table public.saved_view (
  id           uuid primary key default gen_random_uuid(),
  org_id       uuid not null references public.organisation (id) on delete restrict,
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  brief_id     uuid references public.brief (id),                -- null = all Briefs
  entity_type  public.entity_type not null default 'company',
  name         text not null check (length(btrim(name)) between 1 and 120),
  columns      jsonb not null default '[]' check (jsonb_typeof(columns) = 'array'),   -- [{kind, ref, width, pinned}]
  filter       jsonb not null default '{}' check (jsonb_typeof(filter) = 'object'),   -- the shared filter AST
  sort         jsonb not null default '[]' check (jsonb_typeof(sort) = 'array'),
  layout       text not null default 'table' check (layout in ('table', 'board', 'map')),
  visibility   text not null default 'workspace' check (visibility in ('personal', 'workspace')),
  preset       text check (preset is null or preset in ('ready_to_contact', 'needs_evidence', 'stale', 'errored', 'by_verdict')),
  created_by   uuid references public.member (id),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index saved_view_ws_idx on public.saved_view (workspace_id, brief_id);

alter table public.saved_view enable row level security;
create policy saved_view_read on public.saved_view for select to authenticated
  using (app.has_workspace_role(workspace_id, 'Viewer')
         and (visibility = 'workspace' or created_by = app.current_member_id(org_id)));
grant select on public.saved_view to authenticated, service_role;

-- columns: a list of at most 100 {kind: predicate | system, ref, width, pinned}.
create function app.validate_view_columns(p_ws uuid, p_columns jsonb) returns void
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_i   int;
  v_c   jsonb;
  v_key text;
  v_w   jsonb;
begin
  if jsonb_typeof(p_columns) is distinct from 'array' then
    raise exception 'columns: must be a list' using errcode = '22023';
  end if;
  if jsonb_array_length(p_columns) > 100 then
    raise exception 'columns: a view has at most 100 columns' using errcode = '22023';
  end if;
  for v_i in 0 .. jsonb_array_length(p_columns) - 1 loop
    v_c := p_columns -> v_i;
    if jsonb_typeof(v_c) <> 'object' then
      raise exception 'columns[%]: must be an object', v_i using errcode = '22023';
    end if;
    v_key := app.filter_unknown_key(v_c, array['kind', 'ref', 'width', 'pinned']);
    if v_key is not null then
      raise exception 'columns[%]: unknown key "%"', v_i, v_key using errcode = '22023';
    end if;
    if v_c ->> 'kind' is null or jsonb_typeof(v_c -> 'kind') <> 'string' or v_c ->> 'kind' not in ('predicate', 'system') then
      raise exception 'columns[%].kind: must be predicate or system', v_i using errcode = '22023';
    end if;
    if v_c ->> 'kind' = 'predicate' and (app.view_predicate(p_ws, v_c -> 'ref')).id is null then
      raise exception 'columns[%].ref: unknown column "%"', v_i, coalesce(v_c ->> 'ref', '') using errcode = '22023';
    end if;
    if v_c ->> 'kind' = 'system' and (jsonb_typeof(v_c -> 'ref') is distinct from 'string'
                                      or v_c ->> 'ref' !~ '^[a-z][a-z0-9_]{1,39}$') then
      raise exception 'columns[%].ref: unknown system column "%"', v_i, coalesce(v_c ->> 'ref', '') using errcode = '22023';
    end if;
    v_w := v_c -> 'width';
    if v_w is not null and v_w <> 'null'::jsonb
       and (jsonb_typeof(v_w) <> 'number' or v_w::numeric <> trunc(v_w::numeric) or v_w::numeric not between 40 and 2000) then
      raise exception 'columns[%].width: must be 40–2000 pixels', v_i using errcode = '22023';
    end if;
    if v_c -> 'pinned' is not null and v_c -> 'pinned' <> 'null'::jsonb and jsonb_typeof(v_c -> 'pinned') <> 'boolean' then
      raise exception 'columns[%].pinned: must be true or false', v_i using errcode = '22023';
    end if;
  end loop;
end $$;

-- Creates (p_id null) or updates a view. Any member saves a personal view; a workspace view needs
-- an Editor, and so does any change to a view that is a workspace view now (even by its creator).
-- An update is for the creator or an Admin; a parameter left null keeps the stored value (pass
-- p_clear_brief to move a view to all Briefs). The filter and sort are checked by compiling them.
create function public.save_view(p_ws uuid, p_id uuid default null, p_name text default null, p_brief uuid default null,
                                 p_columns jsonb default null, p_filter jsonb default null, p_sort jsonb default null,
                                 p_layout text default null, p_visibility text default null,
                                 p_entity_type public.entity_type default null,
                                 p_clear_brief boolean default false) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org     uuid;
  v_me      uuid;
  r         public.saved_view%rowtype;
  v_name    text;
  v_brief   uuid;
  v_columns jsonb;
  v_filter  jsonb;
  v_sort    jsonb;
  v_layout  text;
  v_vis     text;
  v_type    public.entity_type;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  v_me := app.current_member_id(v_org);

  if p_id is not null then
    select * into r from public.saved_view where id = p_id and workspace_id = p_ws for update;
    -- A missing view and a forbidden one look the same.
    if not found or (r.created_by is distinct from v_me and not app.has_workspace_role(p_ws, 'Admin')) then
      raise exception 'only the creator or an Admin can change this view' using errcode = '42501';
    end if;
    if r.visibility = 'workspace' and not app.has_workspace_role(p_ws, 'Editor') then
      raise exception 'only an Editor can change a workspace view' using errcode = '42501';
    end if;
  end if;
  v_name    := coalesce(p_name, r.name);
  v_brief   := case when p_clear_brief then null else coalesce(p_brief, r.brief_id) end;
  v_columns := coalesce(nullif(p_columns, 'null'::jsonb), r.columns, '[]'::jsonb);
  v_filter  := coalesce(nullif(p_filter, 'null'::jsonb), r.filter, '{}'::jsonb);
  v_sort    := coalesce(nullif(p_sort, 'null'::jsonb), r.sort, '[]'::jsonb);
  v_layout  := coalesce(p_layout, r.layout, 'table');
  v_vis     := coalesce(p_visibility, r.visibility, 'workspace');
  v_type    := coalesce(p_entity_type, r.entity_type, 'company');

  if v_vis not in ('personal', 'workspace') then
    raise exception 'visibility must be personal or workspace' using errcode = '22023';
  end if;
  if v_vis = 'workspace' and not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'only an Editor can save a workspace view' using errcode = '42501';
  end if;
  if v_name is null or length(btrim(v_name)) not between 1 and 120 then
    raise exception 'a view needs a name of 1–120 characters' using errcode = '22023';
  end if;
  if v_layout not in ('table', 'board', 'map') then
    raise exception 'layout must be table, board or map' using errcode = '22023';
  end if;
  if v_brief is not null and not exists (select 1 from public.brief where id = v_brief and workspace_id = p_ws) then
    raise exception 'the Brief is not in this workspace' using errcode = '22023';
  end if;
  perform app.validate_view_columns(p_ws, v_columns);
  perform app.filter_where(p_ws, v_brief, v_filter, 'filter');
  perform app.view_order(p_ws, v_brief, v_sort);
  if jsonb_typeof(v_filter) <> 'object' then
    raise exception 'filter: a filter must be an object' using errcode = '22023';
  end if;

  if p_id is null then
    insert into public.saved_view (org_id, workspace_id, brief_id, entity_type, name, columns, filter, sort, layout,
                                   visibility, created_by)
    values (v_org, p_ws, v_brief, v_type, btrim(v_name), v_columns, v_filter, v_sort, v_layout, v_vis, v_me)
    returning * into r;
    perform app.audit(v_org, p_ws, 'saved_view.created', 'saved_view', r.id, null, to_jsonb(r));
    return r.id;
  end if;

  update public.saved_view
     set brief_id = v_brief, entity_type = v_type, name = btrim(v_name), columns = v_columns,
         filter = v_filter, sort = v_sort, layout = v_layout, visibility = v_vis, updated_at = now()
   where id = p_id;
  perform app.audit(v_org, p_ws, 'saved_view.updated', 'saved_view', p_id, to_jsonb(r),
                    (select to_jsonb(x) from public.saved_view x where x.id = p_id));
  return p_id;
end $$;

-- For the creator (an Editor or above for a workspace view) or an Admin. A missing view and a
-- forbidden one both raise 42501.
create function public.delete_view(p_id uuid) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  r public.saved_view%rowtype;
begin
  select * into r from public.saved_view where id = p_id for update;
  if not found
     or not ((app.has_workspace_role(r.workspace_id, case when r.visibility = 'workspace' then 'Editor' else 'Viewer' end::public.member_role)
              and r.created_by = app.current_member_id(r.org_id))
             or app.has_workspace_role(r.workspace_id, 'Admin')) then
    raise exception 'only the creator or an Admin can delete this view' using errcode = '42501';
  end if;
  delete from public.saved_view where id = p_id;
  perform app.audit(r.org_id, r.workspace_id, 'saved_view.deleted', 'saved_view', p_id, to_jsonb(r), null);
end $$;

-- The five presets (in the Global Constraints order), then the saved views the caller can see:
-- workspace views and their own personal views, for the given Brief or for all Briefs.
-- stale and errored use the {"run": "*"} leaf, so "any column" is resolved when the view runs
-- (the Brief's columns, else every workspace and global column), with no cap.
create function public.list_views(p_ws uuid, p_brief uuid default null)
returns table (id uuid, preset text, name text, brief_id uuid, entity_type public.entity_type, columns jsonb,
               filter jsonb, sort jsonb, layout text, visibility text, created_by uuid,
               created_at timestamptz, updated_at timestamptz)
  language plpgsql stable security definer set search_path = ''
as $$
#variable_conflict use_column
declare
  v_org uuid;
  v_me  uuid;
begin
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_brief is not null and not exists (select 1 from public.brief b where b.id = p_brief and b.workspace_id = p_ws) then
    raise exception 'the Brief is not in this workspace' using errcode = '22023';
  end if;
  v_me := app.current_member_id(v_org);

  return query
  select null::uuid, x.preset, x.name, p_brief, 'company'::public.entity_type, '[]'::jsonb, x.filter, x.sort,
         'table'::text, 'workspace'::text, null::uuid, null::timestamptz, null::timestamptz
    from (values
      (1, 'ready_to_contact', 'Ready to contact',
       '{"all": [{"field": {"system": "verdict"}, "op": "one_of", "value": ["Confirmed", "Likely"]},
                 {"field": {"system": "band"}, "op": "one_of", "value": ["High", "Medium"]}]}'::jsonb, '[]'::jsonb),
      (2, 'needs_evidence', 'Needs evidence',
       '{"field": {"system": "verdict"}, "op": "one_of", "value": ["Unverified"]}'::jsonb, '[]'::jsonb),
      (3, 'stale', 'Stale', '{"field": {"run": "*"}, "op": "one_of", "value": ["stale"]}'::jsonb, '[]'::jsonb),
      (4, 'errored', 'Errored', '{"field": {"run": "*"}, "op": "one_of", "value": ["error"]}'::jsonb, '[]'::jsonb),
      (5, 'by_verdict', 'By verdict', '{}'::jsonb, '[{"system": "verdict", "dir": "asc"}]'::jsonb)
    ) x(n, preset, name, filter, sort)
   order by x.n;

  return query
  select sv.id, sv.preset, sv.name, sv.brief_id, sv.entity_type, sv.columns, sv.filter, sv.sort, sv.layout,
         sv.visibility, sv.created_by, sv.created_at, sv.updated_at
    from public.saved_view sv
   where sv.workspace_id = p_ws
     and (sv.visibility = 'workspace' or sv.created_by = v_me)
     and (p_brief is null or sv.brief_id is null or sv.brief_id = p_brief)
   order by lower(sv.name), sv.created_at, sv.id;
end $$;

-- ── 6. Grants ────────────────────────────────────────────────────────────────
revoke execute on function
  app.view_brief_ctx(uuid, uuid), app.view_predicate(uuid, jsonb), app.filter_unknown_key(jsonb, text[]),
  app.brief_column_ids(uuid, uuid), app.filter_run_sql(text, text[]),
  app.filter_node(uuid, uuid, jsonb, text, int, int, int), app.view_is_date(text),
  app.filter_where(uuid, uuid, jsonb, text), app.view_order(uuid, uuid, jsonb),
  app.validate_view_columns(uuid, jsonb)
  from public, anon, authenticated;
grant execute on function
  app.filter_where(uuid, uuid, jsonb, text), app.view_order(uuid, uuid, jsonb)
  to postgres, service_role;

revoke execute on function
  public.view_entities(uuid, uuid, jsonb, jsonb, int, int, public.entity_type),
  public.save_view(uuid, uuid, text, uuid, jsonb, jsonb, jsonb, text, text, public.entity_type, boolean),
  public.delete_view(uuid), public.list_views(uuid, uuid)
  from public, anon;
grant execute on function
  public.view_entities(uuid, uuid, jsonb, jsonb, int, int, public.entity_type),
  public.save_view(uuid, uuid, text, uuid, jsonb, jsonb, jsonb, text, text, public.entity_type, boolean),
  public.delete_view(uuid), public.list_views(uuid, uuid)
  to authenticated;
