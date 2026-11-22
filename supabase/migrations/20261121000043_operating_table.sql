-- Migration 0043: the accounts operating table (P5).
--   * public.account_table is one page of the table: rows from P3's public.view_entities (the
--     filter is compiled only by app.filter_where), cells from P3's public.cells for that page's
--     rows and columns only, and the columns' shapes.
--   * public.cell_detail feeds the cell panel: value, evidence, derived inputs and run history.
--     It never runs anything.
--   * public.set_manual_value writes a person's value as a claim from the workspace's
--     derived:manual source record; public.mark_claim_wrong retracts a claim (claim.retracted,
--     emitted here for the first time) and opens a claim_wrong review item.
--   * public.view_pin / pin_view / pinned_views keep each member's pinned views (sidebar);
--     public.search_accounts is the top-bar search; public.set_auto_run pauses or resumes a
--     formula column's auto-run.
--   * The lens follows P4 (app.home_lens): a Brief of another workspace, or an Archived one, is
--     "all Briefs"; a non-member gets 42501.
--   * Written to be re-applied safely: create or replace, if not exists, drop ... if exists.

-- ── 1. Review items for a wrong claim ────────────────────────────────────────
-- Until now every review item was an identity decision on a mention. A claim_wrong item names
-- the claim and the reason instead (and has no mention).
alter table public.review_item add column if not exists type text not null default 'identity';
alter table public.review_item add column if not exists claim_id uuid references public.claim (id);
alter table public.review_item add column if not exists reason text;
alter table public.review_item alter column mention_id drop not null;
alter table public.review_item drop constraint if exists review_item_type_check;
alter table public.review_item add constraint review_item_type_check check (type in ('identity', 'claim_wrong'));
alter table public.review_item drop constraint if exists review_item_type_fields;
alter table public.review_item add constraint review_item_type_fields check (
  (type = 'identity' and mention_id is not null)
  or (type = 'claim_wrong' and claim_id is not null and reason is not null and length(reason) between 1 and 1000));
create index if not exists review_item_claim_idx on public.review_item (claim_id) where claim_id is not null;

-- ── 2. Derived source records: formula (P3) and manual ───────────────────────
-- As 0039, for kind formula or manual: one source_record per workspace and kind (connector
-- 'derived:<kind>', extraction_status 'irrelevant'), and an extractor_version (<kind>,
-- <kind>-v1, none, 1). Readers that count evidence leave both out by connector 'derived:%'.
create or replace function app.derived_source_record(p_ws uuid, p_kind text) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org  uuid;
  v_id   uuid;
  v_hash text;
begin
  if p_kind is null or p_kind not in ('formula', 'manual') then
    raise exception 'unknown derived source kind "%"', coalesce(p_kind, '') using errcode = '22023';
  end if;
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null then
    raise exception 'workspace not found' using errcode = 'P0002';
  end if;
  insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values (p_kind, p_kind || '-v1', 'none', '1')
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

-- ── 3. Pinned views ──────────────────────────────────────────────────────────
-- view_ref is a preset name (public.list_views) or a saved view id; position is the pin order.
create table if not exists public.view_pin (
  member_id    uuid not null references public.member (id),
  workspace_id uuid not null references public.workspace (id) on delete restrict,
  view_ref     text not null check (length(view_ref) between 1 and 64),
  position     int not null,
  created_at   timestamptz not null default now(),
  primary key (member_id, workspace_id, view_ref)
);
alter table public.view_pin enable row level security;
drop policy if exists view_pin_read on public.view_pin;
create policy view_pin_read on public.view_pin for select to authenticated
  using (exists (select 1 from public.member m
                  where m.id = member_id and m.user_id = (select auth.uid()) and m.status = 'active'));
grant select on public.view_pin to authenticated, service_role;

-- ── 4. Helpers ───────────────────────────────────────────────────────────────
-- A Brief's columns in spec order (P3's app.brief_column_ids gives the same set, unordered):
-- the current version's predicates, spec objects or legacy keys, resolved as app.view_predicate
-- does, each once (first place). Empty when the Brief has no published version.
create or replace function app.table_brief_columns(p_ws uuid, p_brief uuid) returns uuid[]
  language sql stable security definer set search_path = ''
as $$
  select coalesce(array_agg(x.id order by x.o), '{}')
    from (select v.id, min(c.o) as o
            from public.brief b
            join public.brief_version bv on bv.id = b.current_version_id
            cross join lateral jsonb_array_elements(case when jsonb_typeof(bv.config -> 'predicates') = 'array'
                                                         then bv.config -> 'predicates' else '[]'::jsonb end)
                       with ordinality c(spec, o)
            cross join lateral (select (app.view_predicate(p_ws, case jsonb_typeof(c.spec) when 'string' then c.spec
                                                                      else c.spec -> 'key' end)).id) v(id)
           where b.id = p_brief and b.workspace_id = p_ws and v.id is not null
           group by v.id) x
$$;

-- A column's shape: {id, key, label, value_type, fill_method, auto_run}.
create or replace function app.table_column_json(p_predicate uuid) returns jsonb
  language sql stable security definer set search_path = ''
as $$
  select jsonb_build_object('id', p.id, 'key', p.key, 'label', p.label, 'value_type', p.value_type,
                            'fill_method', p.fill_method, 'auto_run', p.auto_run)
    from public.predicate p where p.id = p_predicate
$$;

-- Why a value does not fit the column, or null. The P1 rules (app.validate_claim, and
-- signal_core.ai_gateway.registry.validate_claim_value): boolean, number, a string for text /
-- date / enum / entity_ref; an enum value among the allowed values; a date as YYYY-MM-DD (as
-- Python's date.fromisoformat and the P3 filter read it); an entity_ref an entity of the
-- organisation. A person must also give a value (null: mark the claim wrong instead) and text
-- of at least one character.
create or replace function app.manual_value_error(p_predicate uuid, p_org uuid, p_value jsonb) returns text
  language plpgsql stable security definer set search_path = ''
as $$
declare
  p   public.predicate%rowtype;
  v_t text := jsonb_typeof(p_value);
  v_s text := p_value #>> '{}';
begin
  select * into p from public.predicate where id = p_predicate;
  if p_value is null or v_t = 'null' then
    return 'a value is required (to clear a value, mark its claim wrong)';
  end if;
  if (p.value_type = 'boolean' and v_t <> 'boolean')
     or (p.value_type = 'number' and v_t <> 'number')
     or (p.value_type in ('text', 'date', 'enum', 'entity_ref') and v_t <> 'string') then
    return format('the value does not match column %s (%s)', p.key, p.value_type);
  end if;
  if p.value_type not in ('boolean', 'number', 'text', 'date', 'enum', 'entity_ref') then
    return format('unknown value type %s for column %s', p.value_type, p.key);
  end if;
  if p.value_type = 'text' and btrim(v_s) = '' then
    return format('column %s needs at least one character', p.key);
  end if;
  if p.value_type = 'enum' and not coalesce(p.allowed_values, '[]'::jsonb) @> jsonb_build_array(p_value) then
    return format('"%s" is not allowed for column %s', v_s, p.key);
  end if;
  if p.value_type = 'date' and not (v_s ~ '^\d{4}-\d{2}-\d{2}$' and app.view_is_date(v_s)) then
    return format('"%s" is not a date (YYYY-MM-DD) for column %s', v_s, p.key);
  end if;
  if p.value_type = 'entity_ref' and not exists (select 1 from public.entity x where x.id::text = v_s and x.org_id = p_org) then
    return format('"%s" is not an entity of this organisation (column %s)', v_s, p.key);
  end if;
  return null;
end $$;

-- A view a member can pin: a preset (its name and filter from public.list_views) or a saved view
-- of the workspace that is a workspace view or the member's own. No row when it is neither.
create or replace function app.view_ref(p_ws uuid, p_member uuid, p_ref text)
returns table (name text, filter jsonb)
  language sql stable security definer set search_path = ''
as $$
  select v.name, v.filter from public.list_views(p_ws, null) v where v.id is null and v.preset = p_ref
  union all
  select sv.name, sv.filter from public.saved_view sv
   where p_ref ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
     and sv.id = p_ref::uuid and sv.workspace_id = p_ws
     and (sv.visibility = 'workspace' or sv.created_by = p_member)
   limit 1
$$;

-- ── 5. The table page (Viewer+) ──────────────────────────────────────────────
-- {total: int (matches before paging),
--  rows: [{account_id, entity_id, name,
--          country: text | null (the newest mention's raw_country),
--          domain: text | null (the highest-confidence domain alias),
--          verdict: text | null (the best verdict under the lens), band: text | null,
--          priority: number | null (the highest-priority score under the lens),
--          stage: text, owner: null | {member_id, name (display name, else email)},
--          last_signal: timestamptz | null (the newest observed_at of a sourced claim that is not
--                       retracted; derived claims, connector 'derived:%', never count),
--          cells: {<predicate_id>: {state (the nine P3 states), value: jsonb | null,
--                                   confidence: int | null, claim_id: uuid | null,
--                                   run_id: uuid | null (the latest run), error: text | null}}}]
--         in view order,
--  columns: [{id, key, label, value_type, fill_method, auto_run}] in display order}
-- p_columns null: the lens Brief's columns in spec order (none under all Briefs); otherwise those
-- columns, in the given order, each once, keeping only the workspace's and global ones (at most 50).
-- p_limit 1–200 (default 50), p_offset ≥ 0. A bad filter or sort raises 22023 with the P3 path
-- message. Every row carries every column's cell.
create or replace function public.account_table(p_ws uuid, p_brief uuid, p_filter jsonb, p_sort jsonb, p_columns uuid[],
                                                p_limit int default 50, p_offset int default 0) returns jsonb
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
    from public.view_entities(p_ws, v_brief, p_filter, p_sort, v_limit, v_offset) with ordinality ve(entity_id, total, n);
  if v_total is null and v_offset > 0 then
    -- Past the last page: count the matches all the same.
    select max(v.total) into v_total from public.view_entities(p_ws, v_brief, p_filter, p_sort, 1, 0) v;
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
  select coalesce(jsonb_agg(jsonb_build_object(
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
                            where c.entity_id = e.id and c.status <> 'retracted' and sr.connector_id not like 'derived:%'),
           'cells', coalesce(v_cells -> e.id::text, '{}'::jsonb))
         order by u.n), '[]'::jsonb)
    into v_rows
    from unnest(v_ids) with ordinality u(id, n)
    join public.entity e on e.id = u.id
    join public.account a on a.workspace_id = p_ws and a.entity_id = e.id
    left join public.member om on om.id = a.owner_member_id
    left join lateral (select v.value from public.verdict v
                        where v.entity_id = e.id and v.brief_version_id = any(v_vers)
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

-- ── 6. The cell panel (Viewer+) ──────────────────────────────────────────────
-- {column: {id, key, label, value_type, fill_method, auto_run},
--  state, value: jsonb | null, confidence: int | null   (as public.cells shows the cell),
--  fill_label: 'Found in sources' | 'Formula' | 'Entered by you' | 'Entered by <name>'
--              (from the cell's claim: a derived:manual claim this member entered is "Entered by
--              you", someone else's carries its quote; derived:formula is "Formula"; a sourced one
--              "Found in sources". No claim: by the column's fill method),
--  claims: [{id, value, quote, source_url (source_record.url), source_type, observed_at,
--            status ('active' | 'superseded' | 'retracted'),
--            superseded_at: timestamptz | null (when the claim that superseded it was written)}]
--          the cell's claims in this organisation: active first, then newest; at most 50,
--  inputs: [{column_label, value, claim_id}]   formula columns only: the input claims the run that
--          wrote the value used, else the inputs' current values; [] for any other column,
--  runs: [{id, status, cost_usd, brief_version: int | null, queued_at, started_at, finished_at,
--          error}]  the cell's runs in this workspace, newest first, at most 50}
-- 22023 when the entity is not an account of the workspace or the column not one of its columns.
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
begin
  perform app.home_lens(p_ws, null);
  select w.org_id into v_org from public.workspace w where w.id = p_ws;
  if not exists (select 1 from public.account a join public.entity e on e.id = a.entity_id
                  where a.workspace_id = p_ws and a.entity_id = p_entity and e.org_id = v_org) then
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

  return jsonb_build_object('column', app.table_column_json(p.id), 'state', c.state, 'value', c.value,
                            'confidence', c.confidence, 'fill_label', v_label, 'claims', v_claims,
                            'inputs', v_inputs, 'runs', v_runs);
end $$;

-- ── 7. A manual value (Editor+) ──────────────────────────────────────────────
-- Returns the claim id. The value is checked against the column (app.manual_value_error: 22023
-- with the reason). The claim comes from the workspace's derived:manual source record, with
-- confidence 100, observed now, quote 'Entered by <display name, else email>', through
-- app.create_claim (which emits claim.created; the same value again reuses the active claim).
-- It supersedes the cell's earlier manual claims; sourced claims stay as they are (the newest
-- claim is the cell's value). Audited as claim.manual_value_set on the claim.
create or replace function public.set_manual_value(p_ws uuid, p_entity uuid, p_predicate uuid, p_value jsonb) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org  uuid;
  v_err  text;
  v_me   uuid;
  v_name text;
  v_sr   uuid;
  v_ev   uuid;
  v_id   uuid;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if not exists (select 1 from public.account a join public.entity e on e.id = a.entity_id
                  where a.workspace_id = p_ws and a.entity_id = p_entity and e.org_id = v_org) then
    raise exception 'account not found in this workspace' using errcode = '22023';
  end if;
  if not exists (select 1 from public.predicate where id = p_predicate and (workspace_id = p_ws or workspace_id is null)) then
    raise exception 'column not found in this workspace' using errcode = '22023';
  end if;
  v_err := app.manual_value_error(p_predicate, v_org, p_value);
  if v_err is not null then
    raise exception '%', v_err using errcode = '22023';
  end if;

  v_me := app.current_member_id(v_org);
  select coalesce(nullif(btrim(m.display_name), ''), m.email) into v_name from public.member m where m.id = v_me;
  -- One writer per cell at a time (as the formula writer).
  perform pg_advisory_xact_lock(hashtextextended(p_entity::text || ':' || p_predicate::text, 0));
  v_sr := app.derived_source_record(p_ws, 'manual');
  select id into v_ev from public.extractor_version
   where capability = 'manual' and prompt_version = 'manual-v1' and model = 'none' and parser_version = '1';
  v_id := app.create_claim(v_org, p_entity, p_predicate, p_value, 'Entered by ' || coalesce(v_name, 'a member'), null, null,
                           v_sr, now(), v_ev, 100::smallint);
  update public.claim set status = 'superseded', superseded_by = v_id
   where entity_id = p_entity and predicate_id = p_predicate and source_record_id = v_sr
     and status = 'active' and id <> v_id;
  perform app.audit(v_org, p_ws, 'claim.manual_value_set', 'claim', v_id, null,
                    jsonb_build_object('entity_id', p_entity, 'predicate_id', p_predicate, 'value', p_value));
  return v_id;
end $$;

-- ── 8. Mark a claim wrong (Editor+) ──────────────────────────────────────────
-- Returns the review item id. The claim (of this organisation, on an account of this workspace)
-- must be active (55000 otherwise). It is retracted; claim.retracted {claim_id, entity_id,
-- predicate_id, source_record_id, reason} is emitted on the claim, for the workspace; a pending
-- review_item of type claim_wrong carries the claim and the reason (1–1000 characters, trimmed).
-- Audited as claim.marked_wrong.
create or replace function public.mark_claim_wrong(p_ws uuid, p_claim uuid, p_reason text) returns uuid
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org    uuid;
  v_reason text := btrim(coalesce(p_reason, ''));
  cl       public.claim%rowtype;
  v_id     uuid;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if length(v_reason) not between 1 and 1000 then
    raise exception 'say why the value is wrong (1–1000 characters)' using errcode = '22023';
  end if;
  select * into cl from public.claim where id = p_claim and org_id = v_org for update;
  if not found or not exists (select 1 from public.account a where a.workspace_id = p_ws and a.entity_id = cl.entity_id) then
    raise exception 'claim not found in this workspace' using errcode = '22023';
  end if;
  if cl.status <> 'active' then
    raise exception 'this claim is already %', cl.status using errcode = '55000';
  end if;

  update public.claim set status = 'retracted' where id = p_claim;
  perform app.emit('claim.retracted'::public.event_type, v_org, p_ws, 'claim', p_claim,
                   jsonb_build_object('claim_id', p_claim, 'entity_id', cl.entity_id, 'predicate_id', cl.predicate_id,
                                      'source_record_id', cl.source_record_id, 'reason', v_reason));
  insert into public.review_item (org_id, workspace_id, type, claim_id, reason)
  values (v_org, p_ws, 'claim_wrong', p_claim, v_reason)
  returning id into v_id;
  perform app.audit(v_org, p_ws, 'claim.marked_wrong', 'claim', p_claim, jsonb_build_object('status', 'active'),
                    jsonb_build_object('status', 'retracted', 'reason', v_reason, 'review_item_id', v_id));
  return v_id;
end $$;

-- ── 9. Pinned views (Viewer+, per member) ────────────────────────────────────
-- Pins (p_pinned true, at the end of the member's pins; pinning again changes nothing) or unpins a
-- view: a preset name or a saved view id the member can see (22023 otherwise). At most 20 pins
-- per member and workspace.
create or replace function public.pin_view(p_ws uuid, p_view text, p_pinned boolean) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org uuid;
  v_me  uuid;
  v_ref text := btrim(coalesce(p_view, ''));
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  v_me := app.current_member_id(v_org);
  if v_me is null then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_pinned is null then
    raise exception 'say whether to pin or unpin' using errcode = '22023';
  end if;
  if not p_pinned then
    delete from public.view_pin where member_id = v_me and workspace_id = p_ws and view_ref = v_ref;
    return;
  end if;
  if not exists (select 1 from app.view_ref(p_ws, v_me, v_ref)) then
    raise exception 'unknown view "%"', v_ref using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended('view_pin:' || v_me::text || ':' || p_ws::text, 0));
  if exists (select 1 from public.view_pin where member_id = v_me and workspace_id = p_ws and view_ref = v_ref) then
    return;
  end if;
  if (select count(*) from public.view_pin where member_id = v_me and workspace_id = p_ws) >= 20 then
    raise exception 'at most 20 pinned views' using errcode = '22023';
  end if;
  insert into public.view_pin (member_id, workspace_id, view_ref, position)
  values (v_me, p_ws, v_ref,
          coalesce((select max(position) from public.view_pin where member_id = v_me and workspace_id = p_ws), 0) + 1);
end $$;

-- [{ref (preset name or saved view id), name,
--   count: int | null (the view's matches under the lens, the total of public.view_entities;
--          null when its filter no longer compiles),
--   href: '/accounts?view=<ref>'}]  the caller's pins in pin order. A pin whose saved view was
-- deleted (or is no longer visible) is left out. p_brief is the lens (app.home_lens).
create or replace function public.pinned_views(p_ws uuid, p_brief uuid default null) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_brief uuid := app.home_lens(p_ws, p_brief);
  v_me    uuid := app.current_member_id((select w.org_id from public.workspace w where w.id = p_ws));
  r       record;
  v       record;
  v_count int;
  v_out   jsonb := '[]'::jsonb;
begin
  for r in select vp.view_ref from public.view_pin vp
            where vp.member_id = v_me and vp.workspace_id = p_ws
            order by vp.position, vp.view_ref loop
    select * into v from app.view_ref(p_ws, v_me, r.view_ref) limit 1;
    continue when not found;
    begin
      v_count := app.home_count(p_ws, v_brief, v.filter);
    exception when sqlstate '22023' then
      v_count := null;
    end;
    v_out := v_out || jsonb_build_array(jsonb_build_object('ref', r.view_ref, 'name', v.name, 'count', v_count,
                                                           'href', '/accounts?view=' || r.view_ref));
  end loop;
  return v_out;
end $$;

-- ── 10. Search (Viewer+) ─────────────────────────────────────────────────────
-- [{account_id, name, domain: text | null, href: '/workspace/<ws>/accounts/<account_id>'}]
-- At most 8 of the workspace's active company accounts whose name or a domain alias contains
-- p_q (trimmed, case-insensitive, % and _ taken literally); names that start with it first, then
-- by name. Fewer than 2 characters: [].
create or replace function public.search_accounts(p_ws uuid, p_q text) returns jsonb
  language plpgsql stable security definer set search_path = ''
as $$
declare
  v_org uuid;
  v_q   text := left(lower(btrim(coalesce(p_q, ''))), 200);
  v_esc text;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Viewer') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if length(v_q) < 2 then
    return '[]'::jsonb;
  end if;
  v_esc := replace(replace(replace(v_q, '\', '\\'), '%', '\%'), '_', '\_');
  return coalesce((
    select jsonb_agg(jsonb_build_object('account_id', x.aid, 'name', x.name, 'domain', x.domain,
                                        'href', '/workspace/' || p_ws || '/accounts/' || x.aid)
                     order by x.rank, lower(x.name), x.eid)
      from (select a.id as aid, e.id as eid, e.display_name as name,
                   (select al.value_normalised from public.entity_alias al
                     where al.entity_id = e.id and al.alias_type = 'domain'
                     order by al.confidence desc, al.created_at, al.id limit 1) as domain,
                   case when lower(e.display_name) like v_esc || '%' escape '\' then 0 else 1 end as rank
              from public.account a
              join public.entity e on e.id = a.entity_id
             where a.workspace_id = p_ws and e.org_id = v_org and e.entity_type = 'company' and e.status = 'active'
               and (lower(e.display_name) like '%' || v_esc || '%' escape '\'
                    or exists (select 1 from public.entity_alias al
                                where al.entity_id = e.id and al.alias_type = 'domain'
                                  and lower(al.value_normalised) like '%' || v_esc || '%' escape '\'))
             order by rank, lower(e.display_name), e.id
             limit 8) x), '[]'::jsonb);
end $$;

-- ── 11. Auto-run (Editor+) ───────────────────────────────────────────────────
-- Pauses (false) or resumes (true) a formula column of the workspace (22023 for any other
-- column). auto_run is not part of the config hash, so the column's version stays. Audited as
-- predicate.auto_run_changed {auto_run} when it changes.
create or replace function public.set_auto_run(p_ws uuid, p_predicate uuid, p_on boolean) returns void
  language plpgsql security definer set search_path = ''
as $$
declare
  v_org uuid;
  p     public.predicate%rowtype;
begin
  select org_id into v_org from public.workspace where id = p_ws;
  if v_org is null or not app.has_workspace_role(p_ws, 'Editor') then
    raise exception 'permission denied' using errcode = '42501';
  end if;
  if p_on is null then
    raise exception 'say whether auto-run is on or off' using errcode = '22023';
  end if;
  select * into p from public.predicate where id = p_predicate and workspace_id = p_ws for update;
  if not found then
    raise exception 'column not found in this workspace' using errcode = '22023';
  end if;
  if p.fill_method <> 'formula' then
    raise exception 'column "%" is filled by %; only a formula column runs automatically', p.key, p.fill_method
      using errcode = '22023';
  end if;
  if p.auto_run is distinct from p_on then
    update public.predicate set auto_run = p_on where id = p.id;
    perform app.audit(v_org, p_ws, 'predicate.auto_run_changed', 'predicate', p.id,
                      jsonb_build_object('auto_run', p.auto_run), jsonb_build_object('auto_run', p_on));
  end if;
end $$;

-- ── 12. Grants ───────────────────────────────────────────────────────────────
revoke execute on function
  app.table_brief_columns(uuid, uuid), app.table_column_json(uuid), app.manual_value_error(uuid, uuid, jsonb),
  app.view_ref(uuid, uuid, text)
  from public, anon, authenticated;

revoke execute on function
  public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int), public.cell_detail(uuid, uuid, uuid),
  public.set_manual_value(uuid, uuid, uuid, jsonb), public.mark_claim_wrong(uuid, uuid, text),
  public.pin_view(uuid, text, boolean), public.pinned_views(uuid, uuid), public.search_accounts(uuid, text),
  public.set_auto_run(uuid, uuid, boolean)
  from public, anon;
grant execute on function
  public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int), public.cell_detail(uuid, uuid, uuid),
  public.set_manual_value(uuid, uuid, uuid, jsonb), public.mark_claim_wrong(uuid, uuid, text),
  public.pin_view(uuid, text, boolean), public.pinned_views(uuid, uuid), public.search_accounts(uuid, text),
  public.set_auto_run(uuid, uuid, boolean)
  to authenticated;
