begin;
create extension if not exists pgtap with schema extensions;
select plan(149);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Alpha: A owner (Ann Owner), K Viewer. Beta: B owner, with its own account and claim. X is
-- signed in but a member of nothing.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000027a1', 'a@ot.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000027a2', 'k@ot.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000027a3', 'b@ot.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000027a5', 'x@ot.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
select public.create_organisation('OT Alpha', 'Alpha WS');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a3","role":"authenticated"}', true);
select public.create_organisation('OT Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'OT Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'OT Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'OT Beta';
insert into ids select 'ws_b', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'OT Beta';
insert into ids select 'm_a', id from public.member where user_id = '00000000-0000-4000-8000-0000000027a1';
update public.member set display_name = 'Ann Owner' where user_id = '00000000-0000-4000-8000-0000000027a1';
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000027a2', (select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000027a2', 'k@ot.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000027a2', (select v from ids where k = 'ws'), 'Viewer');
create function pg_temp.id(p text) returns uuid language sql as $$ select v from ids where k = p $$;
create function pg_temp.w() returns uuid language sql as $$ select v from ids where k = 'ws' $$;
create function pg_temp.b() returns uuid language sql as $$ select v from ids where k = 'b1' $$;

-- The Brief: an extract column (ot_sites) and a formula column (ot_double), in that spec order
-- (key order would put ot_double first). A third workspace column (ot_note) is not in the Brief.
insert into public.predicate (key, org_id, workspace_id, label, value_type)
values ('ot_note', pg_temp.id('org'), pg_temp.w(), 'Note', 'text');
insert into ids select 'p_note', id from public.predicate where key = 'ot_note';
insert into ids values ('b1', gen_random_uuid()), ('bv1', gen_random_uuid()), ('bf', gen_random_uuid()), ('bvf', gen_random_uuid());
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
values (pg_temp.id('b1'), pg_temp.id('org'), pg_temp.w(), 'Drone docks', 'Draft', 'done'),
       (pg_temp.id('bf'), pg_temp.id('org_b'), pg_temp.id('ws_b'), 'Foreign', 'Active', 'done');
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, published_at)
values (pg_temp.id('bv1'), pg_temp.id('b1'), pg_temp.id('org'), pg_temp.w(), 1,
        '{"predicates": [{"key": "ot_sites", "label": "Sites", "value_type": "number", "role": "scale"},
                         {"key": "ot_double", "label": "Double", "value_type": "number", "role": "info",
                          "fill_method": "formula", "formula": "ot_sites * 2", "inputs": ["ot_sites"]}]}',
        '{}', '{}', now() - interval '3 days'),
       (pg_temp.id('bvf'), pg_temp.id('bf'), pg_temp.id('org_b'), pg_temp.id('ws_b'), 1, '{}', '{}', '{}', now());
update public.brief set state = 'Active', current_version_id = pg_temp.id('bv1') where id = pg_temp.id('b1');
update public.brief set current_version_id = pg_temp.id('bvf') where id = pg_temp.id('bf');
insert into ids select 'p_sites', id from public.predicate where key = 'ot_sites';
insert into ids select 'p_dbl', id from public.predicate where key = 'ot_double';

-- Accounts: E1 "OT Alpha 100%" (owned by A), E2 "OT Beta_one", E3 "OT Gamma"; Beta's EB.
insert into ids select 'e' || n, app.create_entity(pg_temp.id('org'), 'company', nm, 'tenant')
  from (values (1, 'OT Alpha 100%'), (2, 'OT Beta_one'), (3, 'OT Gamma')) v(n, nm);
insert into ids select 'eb', app.create_entity(pg_temp.id('org_b'), 'company', 'OT Foreign', 'tenant');
select set_config('app.writer', 'derivation', true);
insert into public.account (org_id, workspace_id, entity_id, owner_member_id)
select pg_temp.id('org'), pg_temp.w(), pg_temp.id('e' || n), case when n = 1 then pg_temp.id('m_a') end
  from generate_series(1, 3) n;
insert into public.account (org_id, workspace_id, entity_id) values (pg_temp.id('org_b'), pg_temp.id('ws_b'), pg_temp.id('eb'));
insert into ids select 'a' || n, a.id from generate_series(1, 3) n
  join public.account a on a.entity_id = pg_temp.id('e' || n) and a.workspace_id = pg_temp.w();
select set_config('app.writer', '', true);

-- Sources: one record (mentions E1 in NL), the derived formula record, Beta's record.
insert into public.extractor_version (capability, prompt_version, model, parser_version) values ('extract.ot27', '1', 'test', '1');
insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status)
values (pg_temp.id('org'), pg_temp.w(), 'radar:x', 'news', 'https://ot27.example/1', now(), 'ot27-1', 'extracted'),
       (pg_temp.id('org_b'), pg_temp.id('ws_b'), 'radar:y', 'news', 'https://ot27.example/b', now(), 'ot27-b', 'extracted');
insert into ids select 's1', id from public.source_record where content_hash = 'ot27-1';
insert into ids select 'sb', id from public.source_record where content_hash = 'ot27-b';
insert into ids select 'sd', app.derived_source_record(pg_temp.w(), 'formula');
insert into public.mention (org_id, source_record_id, entity_type, raw_name, raw_country, entity_id, match_method)
values (pg_temp.id('org'), pg_temp.id('s1'), 'company', 'OT Alpha 100%', 'NL', pg_temp.id('e1'), 'exact');
insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
values (pg_temp.id('org'), pg_temp.id('e1'), 'domain', 'alpha.example', 95, pg_temp.id('s1')),
       (pg_temp.id('org'), pg_temp.id('e2'), 'domain', 'beta-ot.example', 95, pg_temp.id('s1'));

-- Claims. E1: 12 sites (sourced, 2 days old) and a derived 24 (newer: never the last signal).
-- E2: 4 sites (stale). Beta: a claim on its own entity.
create function pg_temp.claim(o text, e text, p text, val jsonb, q text, src text, age interval) returns uuid language sql as $$
  select app.create_claim(pg_temp.id(o), pg_temp.id(e), pg_temp.id(p), val, q, null, null, pg_temp.id(src), now() - age,
                          (select id from public.extractor_version where capability = 'extract.ot27'), 80::smallint)
$$;
insert into ids select 'c1_sites', pg_temp.claim('org', 'e1', 'p_sites', '12', 'E1 runs twelve sites', 's1', interval '2 days');
insert into ids select 'c1_dbl', pg_temp.claim('org', 'e1', 'p_dbl', '24', null, 'sd', interval '0 days');
insert into ids select 'c2_sites', pg_temp.claim('org', 'e2', 'p_sites', '4', 'E2 has four', 's1', interval '1 day');
insert into public.predicate (key, org_id, workspace_id, label, value_type) values ('ot_b_note', pg_temp.id('org_b'), pg_temp.id('ws_b'), 'B note', 'text');
insert into ids select 'p_bnote', id from public.predicate where key = 'ot_b_note';
insert into ids select 'cb', pg_temp.claim('org_b', 'eb', 'p_bnote', '"secret"', 'Beta only', 'sb', interval '1 day');

-- Verdict and score: E1 Confirmed / High 90 under v1.
select set_config('app.writer', 'derivation', true);
insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids)
values (pg_temp.id('org'), pg_temp.w(), pg_temp.id('bv1'), pg_temp.id('e1'), 'Confirmed', array[pg_temp.id('c1_sites')]);
insert into public.score (org_id, workspace_id, account_id, brief_version_id, priority, band)
values (pg_temp.id('org'), pg_temp.w(), pg_temp.id('a1'), pg_temp.id('bv1'), 90, 'High');
select set_config('app.writer', '', true);

-- Runs. E1 ot_double succeeded on the current inputs (derived); E2 ot_sites succeeded on an old
-- hash (stale); E3 ot_sites errored; E3 ot_double queued.
insert into ids values ('r1_dbl', gen_random_uuid()), ('r3_err', gen_random_uuid()), ('r3_q', gen_random_uuid());
insert into public.predicate_run (id, org_id, workspace_id, entity_id, predicate_id, predicate_version, brief_version_id, status,
                                  queued_at, started_at, finished_at, inputs_hash, inputs, claim_id, error, cost_usd)
values (pg_temp.id('r1_dbl'), pg_temp.id('org'), pg_temp.w(), pg_temp.id('e1'), pg_temp.id('p_dbl'), 1, pg_temp.id('bv1'), 'succeeded',
        now() - interval '1 hour', now() - interval '1 hour', now() - interval '59 minutes',
        app.run_inputs_hash(pg_temp.id('e1'), pg_temp.id('p_dbl')), jsonb_build_array(pg_temp.id('c1_sites')), pg_temp.id('c1_dbl'), null, 0),
       (gen_random_uuid(), pg_temp.id('org'), pg_temp.w(), pg_temp.id('e2'), pg_temp.id('p_sites'), 1, pg_temp.id('bv1'), 'succeeded',
        now() - interval '2 days', now() - interval '2 days', now() - interval '2 days', 'old-hash', null, pg_temp.id('c2_sites'), null, 0.01),
       (pg_temp.id('r3_err'), pg_temp.id('org'), pg_temp.w(), pg_temp.id('e3'), pg_temp.id('p_sites'), 1, pg_temp.id('bv1'), 'error',
        now() - interval '1 day', now() - interval '1 day', now() - interval '1 day', null, null, null, 'boom', 0.01),
       (pg_temp.id('r3_q'), pg_temp.id('org'), pg_temp.w(), pg_temp.id('e3'), pg_temp.id('p_dbl'), 1, null, 'queued',
        now(), null, null, null, null, null, null, 0);
-- E2's note column: 55 cancelled runs and 52 claims (the panel shows at most 50 of each).
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at)
select pg_temp.id('org'), pg_temp.w(), pg_temp.id('e2'), pg_temp.id('p_note'), 1, 'cancelled', now() - make_interval(mins => n), now()
  from generate_series(1, 55) n;
select count(pg_temp.claim('org', 'e2', 'p_note', to_jsonb('note ' || n), 'q', 's1', make_interval(mins => n))) from generate_series(1, 52) n;

-- A workspace view (A's), and a personal view of A's that K cannot see.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
insert into ids select 'sv', public.save_view(pg_temp.w(), p_name => 'OT names', p_filter => '{"field": {"system": "name"}, "op": "contains", "value": "ot "}',
                                             p_visibility => 'workspace');
insert into ids select 'sv_priv', public.save_view(pg_temp.w(), p_name => 'Mine', p_visibility => 'personal');
reset role;

select is(app.cell_state(pg_temp.id('e1'), pg_temp.id('p_sites')) || ',' || app.cell_state(pg_temp.id('e1'), pg_temp.id('p_dbl')) || ','
          || app.cell_state(pg_temp.id('e2'), pg_temp.id('p_sites')) || ',' || app.cell_state(pg_temp.id('e3'), pg_temp.id('p_sites')) || ','
          || app.cell_state(pg_temp.id('e3'), pg_temp.id('p_dbl')),
  'value,derived,stale,error,queued', '1. seeded: value, derived, stale, error and queued cells');

-- Reads run as K, a Viewer.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a2","role":"authenticated"}', true);
create function pg_temp.t(f jsonb default null, s jsonb default null, c uuid[] default null, l int default 50, o int default 0,
                          br uuid default pg_temp.b()) returns jsonb language sql as $$
  select public.account_table(pg_temp.w(), br, f, s, c, l, o) $$;

-- ── account_table ────────────────────────────────────────────────────────────
select is((select string_agg(k, ',' order by k) from jsonb_object_keys(pg_temp.t()) k), 'columns,rows,total', '2. the page has total, rows and columns');
select is(pg_temp.t() -> 'columns',
  jsonb_build_array(
    jsonb_build_object('id', pg_temp.id('p_sites'), 'key', 'ot_sites', 'label', 'Sites', 'value_type', 'number', 'fill_method', 'extract', 'auto_run', false, 'allowed_values', null),
    jsonb_build_object('id', pg_temp.id('p_dbl'), 'key', 'ot_double', 'label', 'Double', 'value_type', 'number', 'fill_method', 'formula', 'auto_run', true, 'allowed_values', null)),
  '3. no p_columns: the Brief''s columns in spec order, each with its shape');
select is(pg_temp.t() -> 'total', '3'::jsonb, '4. total counts every match');
select is((select string_agg(r ->> 'name', ',' order by o) from jsonb_array_elements(pg_temp.t() -> 'rows') with ordinality x(r, o)),
  'OT Alpha 100%,OT Beta_one,OT Gamma', '5. rows in view order (name)');
select is((pg_temp.t() -> 'rows' -> 0) - 'cells',
  jsonb_build_object('account_id', pg_temp.id('a1'), 'entity_id', pg_temp.id('e1'), 'name', 'OT Alpha 100%', 'country', 'NL',
                     'domain', 'alpha.example', 'verdict', 'Confirmed', 'band', 'High', 'priority', 90.00, 'stage', 'Untouched',
                     'owner', jsonb_build_object('member_id', pg_temp.id('m_a'), 'name', 'Ann Owner'),
                     'last_signal', now() - interval '2 days'),
  '6. a row''s system fields; last_signal is the newest sourced claim, not the newer derived one');
select is((pg_temp.t() -> 'rows' -> 2) - 'cells',
  jsonb_build_object('account_id', pg_temp.id('a3'), 'entity_id', pg_temp.id('e3'), 'name', 'OT Gamma', 'country', null,
                     'domain', null, 'verdict', null, 'band', null, 'priority', null, 'stage', 'Untouched', 'owner', null,
                     'last_signal', null),
  '7. a bare row: nulls, never missing keys');
select is(pg_temp.t() #> array['rows', '0', 'cells', pg_temp.id('p_sites')::text],
  jsonb_build_object('state', 'value', 'value', 12, 'confidence', 80, 'claim_id', pg_temp.id('c1_sites'), 'run_id', null, 'error', null),
  '8. a value cell');
select is(pg_temp.t() #> array['rows', '0', 'cells', pg_temp.id('p_dbl')::text],
  jsonb_build_object('state', 'derived', 'value', 24, 'confidence', 80, 'claim_id', pg_temp.id('c1_dbl'), 'run_id', pg_temp.id('r1_dbl'), 'error', null),
  '9. a derived cell');
select is(pg_temp.t() #>> array['rows', '1', 'cells', pg_temp.id('p_sites')::text, 'state'], 'stale', '10. a stale cell');
select is(pg_temp.t() #> array['rows', '2', 'cells', pg_temp.id('p_sites')::text],
  jsonb_build_object('state', 'error', 'value', null, 'confidence', null, 'claim_id', null, 'run_id', pg_temp.id('r3_err'), 'error', 'boom'),
  '11. an error cell carries the reason');
select is(pg_temp.t() #>> array['rows', '2', 'cells', pg_temp.id('p_dbl')::text, 'state'], 'queued', '12. a queued cell');
select is((select count(*)::int from jsonb_array_elements(pg_temp.t() -> 'rows') r, jsonb_object_keys(r -> 'cells') k), 6,
  '13. cells for this page only: rows × columns');
select is((select jsonb_agg(jsonb_build_object('e', r -> 'entity_id', 'n', (select count(*) from jsonb_object_keys(r -> 'cells'))) order by o)
             from jsonb_array_elements(pg_temp.t(l => 2) -> 'rows') with ordinality x(r, o)),
  jsonb_build_array(jsonb_build_object('e', pg_temp.id('e1'), 'n', 2), jsonb_build_object('e', pg_temp.id('e2'), 'n', 2)),
  '13b. Review Focus 4: with limit 2, cells for the 2 page rows only (2 columns each)');

-- Paging.
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(pg_temp.t(l => 2) -> 'rows') r), '["OT Alpha 100%", "OT Beta_one"]'::jsonb, '14. limit 2');
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(pg_temp.t(l => 2, o => 2) -> 'rows') r), '["OT Gamma"]'::jsonb, '15. page 2');
select is(pg_temp.t(l => 2, o => 2) -> 'total', '3'::jsonb, '16. total on page 2');
select is(pg_temp.t(l => 2, o => 10) - 'columns', '{"total": 3, "rows": []}'::jsonb, '17. past the end: no rows, total still known');
select throws_ok(format('select public.account_table(%L::uuid, null, null, null, null, 201, 0)', pg_temp.w()), '22023', null, '18. p_limit at most 200');
select throws_ok(format('select public.account_table(%L::uuid, null, null, null, null, 0, 0)', pg_temp.w()), '22023', null, '19. p_limit at least 1');

-- Columns.
select is(pg_temp.t(c => array[pg_temp.id('p_note')]) -> 'columns' -> 0 ->> 'key', 'ot_note', '20. p_columns replaces the Brief''s columns');
select is((select string_agg(k, ',') from jsonb_object_keys(pg_temp.t(c => array[pg_temp.id('p_note')]) #> '{rows,0,cells}') k),
  pg_temp.id('p_note')::text, '21. and its cells');
select is((select jsonb_agg(c ->> 'key') from jsonb_array_elements(pg_temp.t(c => array[pg_temp.id('p_dbl'), pg_temp.id('p_bnote'), gen_random_uuid(), pg_temp.id('p_dbl'), pg_temp.id('p_sites')]) -> 'columns') c),
  '["ot_double", "ot_sites"]'::jsonb, '22. p_columns keeps its order, drops duplicates and columns of other workspaces');
select is(pg_temp.t(br => null) -> 'columns', '[]'::jsonb, '23. all Briefs: no predicate columns by default');
select is(pg_temp.t(br => null) #> '{rows,0,cells}', '{}'::jsonb, '24. so no cells');
select is(pg_temp.t(br => null) #>> '{rows,0,verdict}', 'Confirmed', '25. all Briefs: verdicts of the Pilot and Active Briefs');
select is(pg_temp.t(br => null, c => array[pg_temp.id('p_sites')]) -> 'columns' -> 0 ->> 'key', 'ot_sites', '26. all Briefs with p_columns');
select is(pg_temp.t(br => pg_temp.id('bf')), pg_temp.t(br => null), '27. a foreign Brief behaves as all Briefs');

-- Filter and sort.
select is(pg_temp.t(f => '{"field": {"system": "name"}, "op": "contains", "value": "beta"}') -> 'total', '1'::jsonb, '28. the filter applies');
select is(pg_temp.t(s => '[{"system": "name", "dir": "desc"}]') #>> '{rows,0,name}', 'OT Gamma', '29. the sort applies');
select throws_ok(format('select public.account_table(%L::uuid, %L::uuid, %L::jsonb, null, null)', pg_temp.w(), pg_temp.b(),
                        '{"field": {"column": "nope"}, "op": "eq", "value": 1}'),
  '22023', 'filter.field: unknown column "nope"', '30. a bad filter raises 22023 with the P3 path message');
select throws_ok(format('select public.account_table(%L::uuid, %L::uuid, %L::jsonb, null, null)', pg_temp.w(), pg_temp.b(),
                        '{"field": {"system": "name"}, "op": "contains", "value": "a", "sql": "true) or (1=1"}'),
  '22023', 'filter: unknown key "sql"', '31. a tampered filter is refused');
select is(pg_temp.t(f => '{"field": {"system": "name"}, "op": "contains", "value": "x''); drop table public.claim; --"}') -> 'total',
  '0'::jsonb, '31b. an injection attempt in a value is only a value');

-- ── cell_detail ──────────────────────────────────────────────────────────────
create function pg_temp.cd(e text, p text) returns jsonb language sql as $$ select public.cell_detail(pg_temp.w(), pg_temp.id(e), pg_temp.id(p)) $$;
select is((select string_agg(k, ',' order by k) from jsonb_object_keys(pg_temp.cd('e1', 'p_sites')) k),
  'claim_id,claims,column,confidence,fill_label,inputs,runs,state,value', '32. the panel''s keys');
select is(pg_temp.cd('e1', 'p_sites') - 'claims' - 'runs',
  jsonb_build_object('column', jsonb_build_object('id', pg_temp.id('p_sites'), 'key', 'ot_sites', 'label', 'Sites', 'value_type', 'number',
                                                  'fill_method', 'extract', 'auto_run', false, 'allowed_values', null),
                     'state', 'value', 'value', 12, 'confidence', 80, 'claim_id', pg_temp.id('c1_sites'), 'fill_label', 'Found in sources',
                     'inputs', '[]'::jsonb),
  '33. a sourced value');
select is(pg_temp.cd('e1', 'p_sites') -> 'claims',
  jsonb_build_array(jsonb_build_object('id', pg_temp.id('c1_sites'), 'value', 12, 'quote', 'E1 runs twelve sites',
                                       'source_url', 'https://ot27.example/1', 'source_type', 'news',
                                       'observed_at', now() - interval '2 days', 'status', 'active', 'superseded_at', null)),
  '34. its evidence');
select is(pg_temp.cd('e1', 'p_sites') -> 'runs', '[]'::jsonb, '35. never run: no history');
select is(pg_temp.cd('e1', 'p_dbl') ->> 'fill_label', 'Formula', '36. a derived value is labelled Formula');
select is(pg_temp.cd('e1', 'p_dbl') -> 'inputs',
  jsonb_build_array(jsonb_build_object('column_label', 'Sites', 'value', 12, 'claim_id', pg_temp.id('c1_sites'))),
  '37. a derived cell lists its input claims');
select is(pg_temp.cd('e1', 'p_dbl') -> 'runs',
  jsonb_build_array(jsonb_build_object('id', pg_temp.id('r1_dbl'), 'status', 'succeeded', 'cost_usd', 0, 'brief_version', 1,
                                       'queued_at', now() - interval '1 hour', 'started_at', now() - interval '1 hour',
                                       'finished_at', now() - interval '59 minutes', 'error', null)),
  '38. its run history');
select is((pg_temp.cd('e3', 'p_sites') #> '{runs,0}') - 'queued_at' - 'started_at' - 'finished_at',
  jsonb_build_object('id', pg_temp.id('r3_err'), 'status', 'error', 'cost_usd', 0.01, 'brief_version', 1, 'error', 'boom'),
  '39. an errored run with its cost and reason');
select is(pg_temp.cd('e3', 'p_sites') - 'column' - 'runs',
  '{"state": "error", "value": null, "confidence": null, "claim_id": null, "fill_label": "Found in sources", "claims": [], "inputs": []}'::jsonb,
  '40. an error cell with no value');
select is(jsonb_array_length(pg_temp.cd('e2', 'p_note') -> 'runs'), 50, '41. at most 50 runs');
select is(jsonb_array_length(pg_temp.cd('e2', 'p_note') -> 'claims'), 50, '42. at most 50 claims');
select is(pg_temp.cd('e2', 'p_note') #>> '{claims,0,value}', 'note 1', '43. newest claim first');
select is((select jsonb_agg(r -> 'queued_at' order by o) from jsonb_array_elements(pg_temp.cd('e2', 'p_note') -> 'runs') with ordinality x(r, o)),
          (select jsonb_agg(r -> 'queued_at' order by (r ->> 'queued_at')::timestamptz desc) from jsonb_array_elements(pg_temp.cd('e2', 'p_note') -> 'runs') r),
  '44. runs newest first');
select throws_ok(format('select public.cell_detail(%L::uuid, %L::uuid, %L::uuid)', pg_temp.w(), pg_temp.id('eb'), pg_temp.id('p_sites')),
  '22023', null, '45. another organisation''s account is not found');
select throws_ok(format('select public.cell_detail(%L::uuid, %L::uuid, %L::uuid)', pg_temp.w(), pg_temp.id('e1'), pg_temp.id('p_bnote')),
  '22023', null, '46. another workspace''s column is not found');
select is((select count(*)::int from jsonb_array_elements(pg_temp.cd('e1', 'p_sites') -> 'claims') c where c ->> 'value' = '"secret"'), 0,
  '47. claims of other organisations are never visible');

-- ── Pins (K, per member) ─────────────────────────────────────────────────────
select lives_ok(format('select public.pin_view(%L::uuid, ''stale'', true)', pg_temp.w()), '48. pin a preset');
select lives_ok(format('select public.pin_view(%L::uuid, %L, true)', pg_temp.w(), pg_temp.id('sv')), '49. pin a workspace view');
select lives_ok(format('select public.pin_view(%L::uuid, ''stale'', true)', pg_temp.w()), '50. pinning twice is a no-op');
select lives_ok(format('select public.pin_view(%L::uuid, ''errored'', true)', pg_temp.w()), '51. pin another preset');
select is(public.pinned_views(pg_temp.w(), pg_temp.b()),
  jsonb_build_array(
    jsonb_build_object('ref', 'stale', 'name', 'Stale', 'count', 1, 'href', '/workspace/' || pg_temp.w() || '/accounts?view=stale&brief=' || pg_temp.b()),
    jsonb_build_object('ref', pg_temp.id('sv')::text, 'name', 'OT names', 'count', 3,
                       'href', '/workspace/' || pg_temp.w() || '/accounts?view=' || pg_temp.id('sv') || '&brief=' || pg_temp.b()),
    jsonb_build_object('ref', 'errored', 'name', 'Errored', 'count', 1, 'href', '/workspace/' || pg_temp.w() || '/accounts?view=errored&brief=' || pg_temp.b())),
  '52. pinned views in pin order, with live counts under the lens');
select lives_ok(format('select public.pin_view(%L::uuid, %L, false)', pg_temp.w(), pg_temp.id('sv')), '53. unpin');
select is((select jsonb_agg(p ->> 'ref') from jsonb_array_elements(public.pinned_views(pg_temp.w(), null)) p), '["stale", "errored"]'::jsonb,
  '54. unpinned; all Briefs as the lens');
select is(public.pinned_views(pg_temp.w(), null) -> 0 ->> 'href', '/workspace/' || pg_temp.w() || '/accounts?view=stale',
  '54b. all Briefs: the canonical href carries no brief');
-- R10 (I4): a saved view keeps its own Brief. Under a second Active Brief (B2, no verdicts) the
-- view saved under B1 still counts, and opens, under B1.
reset role;
insert into ids values ('b2', gen_random_uuid()), ('bv2', gen_random_uuid());
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
values (pg_temp.id('b2'), pg_temp.id('org'), pg_temp.w(), 'Second', 'Draft', 'done');
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, published_at)
values (pg_temp.id('bv2'), pg_temp.id('b2'), pg_temp.id('org'), pg_temp.w(), 1, '{}', '{}', '{}', now());
update public.brief set state = 'Active', current_version_id = pg_temp.id('bv2') where id = pg_temp.id('b2');
set local role authenticated;
insert into ids select 'sv_b1', public.save_view(pg_temp.w(), p_name => 'Confirmed (B1)', p_brief => pg_temp.b(), p_visibility => 'personal',
                                                p_filter => '{"field": {"system": "verdict"}, "op": "one_of", "value": ["Confirmed"]}');
select lives_ok(format('select public.pin_view(%L::uuid, %L, true)', pg_temp.w(), pg_temp.id('sv_b1')), '54c. pin a view saved under B1');
select is((select p - 'name' - 'ref' from jsonb_array_elements(public.pinned_views(pg_temp.w(), pg_temp.id('b2'))) p
            where p ->> 'ref' = pg_temp.id('sv_b1')::text),
  jsonb_build_object('count', 1, 'href', '/workspace/' || pg_temp.w() || '/accounts?view=' || pg_temp.id('sv_b1') || '&brief=' || pg_temp.b()),
  '54d. under another lens it counts, and links, under its own Brief');
select is((select p -> 'count' from jsonb_array_elements(public.pinned_views(pg_temp.w(), null)) p where p ->> 'ref' = pg_temp.id('sv_b1')::text),
  '1'::jsonb, '54e. and under all Briefs too');
select lives_ok(format('select public.pin_view(%L::uuid, %L, false)', pg_temp.w(), pg_temp.id('sv_b1')), '54f. unpin it');
select throws_ok(format('select public.pin_view(%L::uuid, ''nope'', true)', pg_temp.w()), '22023', null, '55. an unknown preset is refused');
select throws_ok(format('select public.pin_view(%L::uuid, %L, true)', pg_temp.w(), pg_temp.id('sv_priv')), '22023', null,
  '56. someone else''s personal view is refused');
select throws_ok(format('select public.pin_view(%L::uuid, %L, true)', pg_temp.w(), gen_random_uuid()), '22023', null, '57. an unknown view id is refused');
reset role;
select is((select count(*)::int from public.view_pin where member_id = pg_temp.id('m_a')), 0, '58. pins are per member: A has none');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
select is(public.pinned_views(pg_temp.w(), null), '[]'::jsonb, '59. A sees no pins of K''s');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a2","role":"authenticated"}', true);

-- ── Search (K) ───────────────────────────────────────────────────────────────
select is(public.search_accounts(pg_temp.w(), 'alpha'),
  jsonb_build_array(jsonb_build_object('account_id', pg_temp.id('a1'), 'name', 'OT Alpha 100%', 'domain', 'alpha.example',
                                       'href', '/workspace/' || pg_temp.w() || '/accounts/' || pg_temp.id('a1'))),
  '60. by name, case-insensitive');
select is(public.search_accounts(pg_temp.w(), 'alpha', pg_temp.b()) -> 0 ->> 'href',
  '/workspace/' || pg_temp.w() || '/accounts/' || pg_temp.id('a1') || '?brief=' || pg_temp.b(), '60b. a lens Brief rides along on the href');
select is(public.search_accounts(pg_temp.w(), 'alpha', pg_temp.id('bf')) -> 0 ->> 'href',
  '/workspace/' || pg_temp.w() || '/accounts/' || pg_temp.id('a1'), '60c. a foreign Brief is no lens');
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(public.search_accounts(pg_temp.w(), '  BETA-OT ')) r), '["OT Beta_one"]'::jsonb,
  '61. by domain, trimmed');
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(public.search_accounts(pg_temp.w(), '0%')) r), '["OT Alpha 100%"]'::jsonb,
  '62. a % is a literal %');
select is(public.search_accounts(pg_temp.w(), 'a%'), '[]'::jsonb, '63. a % is not a wildcard');
select is(public.search_accounts(pg_temp.w(), '1_0'), '[]'::jsonb, '64. an _ is not a wildcard');
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(public.search_accounts(pg_temp.w(), 'a_one')) r), '["OT Beta_one"]'::jsonb,
  '65. an _ matches itself');
select is(public.search_accounts(pg_temp.w(), ' b '), '[]'::jsonb, '66. at least 2 characters after trimming');
select is(public.search_accounts(pg_temp.w(), null), '[]'::jsonb, '67. null finds nothing');
select is(public.search_accounts(pg_temp.w(), 'foreign'), '[]'::jsonb, '68. other workspaces'' accounts are never found');

-- ── Writes are for Editors: a Viewer is refused (42501) ──────────────────────
select throws_ok(format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''5'')', pg_temp.w(), pg_temp.id('e1'), pg_temp.id('p_sites')),
  '42501', null, '69. Viewer: set_manual_value');
select throws_ok(format('select public.mark_claim_wrong(%L::uuid, %L::uuid, ''no'')', pg_temp.w(), pg_temp.id('c1_sites')),
  '42501', null, '70. Viewer: mark_claim_wrong');
select throws_ok(format('select public.set_auto_run(%L::uuid, %L::uuid, false)', pg_temp.w(), pg_temp.id('p_dbl')),
  '42501', null, '71. Viewer: set_auto_run');

-- ── set_manual_value (A) ─────────────────────────────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
create function pg_temp.mv(e text, p text, v jsonb) returns uuid language sql as $$
  select public.set_manual_value(pg_temp.w(), pg_temp.id(e), pg_temp.id(p), v) $$;
select throws_ok(format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''"seven"'')', pg_temp.w(), pg_temp.id('e2'), pg_temp.id('p_sites')),
  '22023', null, '72. a value of the wrong type is refused');
select throws_ok(format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''null'')', pg_temp.w(), pg_temp.id('e2'), pg_temp.id('p_sites')),
  '22023', null, '73. a null value is refused');
select throws_ok(format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''7'')', pg_temp.w(), pg_temp.id('e2'), pg_temp.id('p_note')),
  '22023', null, '74. a number for a text column is refused');
select throws_ok(format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''7'')', pg_temp.w(), pg_temp.id('eb'), pg_temp.id('p_sites')),
  '22023', null, '75. another organisation''s account is refused');
insert into ids select 'm1', pg_temp.mv('e2', 'p_sites', '7');
reset role;
select is((select jsonb_build_object('value', c.value, 'confidence', c.confidence, 'quote', c.quote, 'status', c.status,
                                     'connector', sr.connector_id, 'observed_at', c.observed_at)
             from public.claim c join public.source_record sr on sr.id = c.source_record_id where c.id = pg_temp.id('m1')),
  jsonb_build_object('value', 7, 'confidence', 100, 'quote', 'Entered by Ann Owner', 'status', 'active',
                     'connector', 'derived:manual', 'observed_at', now()),
  '76. a manual claim: confidence 100, observed now, entered by the member, from the derived:manual source');
select is((select count(*)::int from public.outbox where event = 'claim.created' and aggregate_id = pg_temp.id('m1')), 1,
  '77. it emits claim.created');
set local role authenticated;
insert into ids select 'm2', pg_temp.mv('e2', 'p_sites', '8');
reset role;
select is((select status::text || ':' || superseded_by from public.claim where id = pg_temp.id('m1')), 'superseded:' || pg_temp.id('m2'),
  '78. a second manual value supersedes the first');
select is((select count(*)::int from public.claim c join public.source_record sr on sr.id = c.source_record_id
            where c.entity_id = pg_temp.id('e2') and c.predicate_id = pg_temp.id('p_sites') and c.status = 'active' and sr.connector_id = 'derived:manual'),
  1, '79. one active manual claim per cell');
select is((select status::text from public.claim where id = pg_temp.id('c2_sites')), 'active', '80. the sourced claim is left alone');
set local role authenticated;
select is(pg_temp.cd('e2', 'p_sites') - 'column' - 'claims' - 'runs' - 'state' - 'claim_id',
  '{"value": 8, "confidence": 100, "fill_label": "Entered by you", "inputs": []}'::jsonb, '81. the panel: the manual value, entered by you');
select is(pg_temp.cd('e2', 'p_sites') ->> 'claim_id', pg_temp.id('m2')::text,
  '81b. I2: the panel names the cell''s claim (the manual value), so the other active claims show as overridden and Mark wrong acts on it');
select is((select jsonb_agg(jsonb_build_object('value', c -> 'value', 'status', c -> 'status', 'struck', c -> 'superseded_at' <> 'null'::jsonb) order by o)
             from jsonb_array_elements(pg_temp.cd('e2', 'p_sites') -> 'claims') with ordinality x(c, o)),
  '[{"value": 8, "status": "active", "struck": false}, {"value": 4, "status": "active", "struck": false},
    {"value": 7, "status": "superseded", "struck": true}]'::jsonb,
  '82. active claims first, then the old manual value, superseded, with its date');
select is(pg_temp.cd('e2', 'p_sites') #>> '{claims,2,superseded_at}', (select to_jsonb(created_at) #>> '{}' from public.claim where id = pg_temp.id('m2')),
  '83. superseded_at is when the newer claim was written');
select is(pg_temp.t() #> array['rows', '1', 'cells', pg_temp.id('p_sites')::text, 'value'], '8'::jsonb, '84. the table shows the manual value');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a2","role":"authenticated"}', true);
select is(pg_temp.cd('e2', 'p_sites') ->> 'fill_label', 'Entered by Ann Owner', '85. to another member it names who entered it');
select is(pg_temp.t() #>> '{rows,1,last_signal}', (select to_jsonb(now()) #>> '{}'), '85b. a manual value is a signal (R5)');
reset role;
select is(app.home_quote(pg_temp.id('e2'), null, true) ->> 'quote', 'Entered by Ann Owner', '85c. and evidence for Home''s quotes (R5)');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
-- I1: a manual value on the errored cell shows; filters agree with the cell.
insert into ids select 'm3', pg_temp.mv('e3', 'p_sites', '3');
select is((pg_temp.t() #> array['rows', '2', 'cells', pg_temp.id('p_sites')::text]) - 'run_id',
  jsonb_build_object('state', 'value', 'value', 3, 'confidence', 100, 'claim_id', pg_temp.id('m3'), 'error', 'boom'),
  '85e. a manual edit after the errored run shows its value (the run''s error stays readable)');
select is(pg_temp.t(f => '{"field": {"run": "ot_sites"}, "op": "one_of", "value": ["error"]}') -> 'total', '0'::jsonb,
  '85f. the run-state filter (fast path) agrees: no errored ot_sites cell');
select is((select jsonb_agg(r ->> 'name') from jsonb_array_elements(pg_temp.t(f => '{"field": {"run": "ot_sites"}, "op": "one_of", "value": ["value", "empty"]}') -> 'rows') r), '["OT Alpha 100%", "OT Gamma"]'::jsonb,
  '85g. the run-state filter (full path) agrees: E3 is a value');
select is(pg_temp.t(f => '{"field": {"run": "ot_double"}, "op": "one_of", "value": ["queued"]}') -> 'total', '1'::jsonb,
  '85h. a queued cell with no newer claim is still queued');
reset role;
select is((select count(*)::int from public.account a where a.workspace_id = pg_temp.w()
              and app.cell_state(a.entity_id, pg_temp.id('p_sites')) = 'error'), 0, '85i. cell_state agrees');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);

-- ── mark_claim_wrong (A) ─────────────────────────────────────────────────────
select throws_ok(format('select public.mark_claim_wrong(%L::uuid, %L::uuid, ''  '')', pg_temp.w(), pg_temp.id('c1_sites')),
  '22023', null, '86. a reason is required');
select throws_ok(format('select public.mark_claim_wrong(%L::uuid, %L::uuid, ''no'')', pg_temp.w(), pg_temp.id('cb')),
  '22023', null, '87. another organisation''s claim is not found');
insert into ids select 'ri', public.mark_claim_wrong(pg_temp.w(), pg_temp.id('c1_sites'), 'Wrong company');
reset role;
select is((select status::text from public.claim where id = pg_temp.id('c1_sites')), 'retracted', '88. the claim is retracted');
select is((select jsonb_build_object('ws', workspace_id, 'payload', payload) from public.outbox
            where event = 'claim.retracted' and aggregate_id = pg_temp.id('c1_sites')),
  jsonb_build_object('ws', pg_temp.w(), 'payload', jsonb_build_object('claim_id', pg_temp.id('c1_sites'), 'entity_id', pg_temp.id('e1'),
                     'predicate_id', pg_temp.id('p_sites'), 'source_record_id', pg_temp.id('s1'), 'reason', 'Wrong company')),
  '89. it emits claim.retracted');
select is((select jsonb_build_object('type', type, 'claim_id', claim_id, 'reason', reason, 'status', status, 'mention_id', mention_id, 'ws', workspace_id)
             from public.review_item where id = pg_temp.id('ri')),
  jsonb_build_object('type', 'claim_wrong', 'claim_id', pg_temp.id('c1_sites'), 'reason', 'Wrong company', 'status', 'pending',
                     'mention_id', null, 'ws', pg_temp.w()),
  '90. a pending claim_wrong review item carries the reason and the claim');
set local role authenticated;
select throws_ok(format('select public.mark_claim_wrong(%L::uuid, %L::uuid, ''again'')', pg_temp.w(), pg_temp.id('c1_sites')),
  '55000', null, '91. a retracted claim cannot be marked again');
select is(pg_temp.t() #>> array['rows', '0', 'cells', pg_temp.id('p_sites')::text, 'state'], 'empty', '92. the cell no longer shows it');
reset role;
-- I2: the same fact found again is not written back.
select is(pg_temp.claim('org', 'e1', 'p_sites', '12', 'E1 runs twelve sites', 's1', interval '0 days'), pg_temp.id('c1_sites'),
  '92b. re-extracting a claim marked wrong returns the retracted claim');
select is((select count(*)::int from public.claim where entity_id = pg_temp.id('e1') and predicate_id = pg_temp.id('p_sites')), 1,
  '92c. no new claim');
select is((select count(*)::int from public.outbox where event = 'claim.created'
            and payload ->> 'entity_id' = pg_temp.id('e1')::text and payload ->> 'predicate_id' = pg_temp.id('p_sites')::text), 1,
  '92d. and no new claim.created');
select is((select status::text from public.claim where id = pg_temp.id('c1_sites')), 'retracted', '92e. the wrong holds');
-- A system retraction (no claim_wrong item) blocks nothing.
update public.claim set status = 'retracted' where id = pg_temp.id('c1_dbl');
select isnt(pg_temp.claim('org', 'e1', 'p_dbl', '24', null, 'sd', interval '0 days'), pg_temp.id('c1_dbl'), '92f. a system retraction does not block a new claim');
-- R7: resolving claim_wrong items.
set local role authenticated;
select throws_ok(format('select public.resolve_review_item(%L::uuid, ''merge'')', pg_temp.id('ri')), '22023', null, '92g. a wrong-claim item cannot be merged');
select throws_ok(format('select public.resolve_review_item(%L::uuid, ''create_new'')', pg_temp.id('ri')), '22023', null, '92h. nor created new');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a2","role":"authenticated"}', true);
select throws_ok(format('select public.resolve_review_item(%L::uuid, ''restore'')', pg_temp.id('ri')), '42501', null, '92i. a Viewer cannot restore');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a1","role":"authenticated"}', true);
select lives_ok(format('select public.resolve_review_item(%L::uuid, ''restore'')', pg_temp.id('ri')), '92j. restore');
reset role;
select is((select status::text from public.claim where id = pg_temp.id('c1_sites')) || '/' || (select status from public.review_item where id = pg_temp.id('ri')),
  'active/restored', '92k. the claim is active again and the item restored');
select is((select count(*)::int from public.outbox where event = 'claim.created' and aggregate_id = pg_temp.id('c1_sites')), 2,
  '92l. restore emits claim.created, so things recompute');
select is((select count(*)::int from public.audit_log where action = 'claim.restored' and target_id = pg_temp.id('c1_sites')), 1, '92m. audited');
set local role authenticated;
insert into ids select 'ri2', public.mark_claim_wrong(pg_temp.w(), pg_temp.id('c2_sites'), 'Old');
select lives_ok(format('select public.resolve_review_item(%L::uuid, ''skip'')', pg_temp.id('ri2')), '92n. dismiss');
reset role;
select is((select status::text from public.claim where id = pg_temp.id('c2_sites')) || '/' || (select status from public.review_item where id = pg_temp.id('ri2')),
  'retracted/skipped', '92o. dismissed: the claim stays retracted');
select is(cardinality(app.queue_auto_runs(pg_temp.id('c2_sites'))), 1,
  '92o2. a claim a person marked wrong re-runs the formula reading it (claim.retracted auto-run)');
select is((select count(*)::int from public.feedback where target_id in (pg_temp.id('ri'), pg_temp.id('ri2'))), 0,
  '92p. no identity label for wrong-claim items');
insert into public.mention (org_id, source_record_id, entity_type, raw_name, match_method)
values (pg_temp.id('org'), pg_temp.id('s1'), 'company', 'OT Identity', 'exact');
insert into ids select 'ri3', app.create_review_item(pg_temp.id('org'), pg_temp.w(),
  (select id from public.mention where raw_name = 'OT Identity'), pg_temp.id('e1'), 'fuzzy', 70::smallint);
set local role authenticated;
select throws_ok(format('select public.resolve_review_item(%L::uuid, ''restore'')', pg_temp.id('ri3')), '22023', null, '92q. an identity item cannot be restored');
-- M1: entering a value that was marked wrong again resolves its Review item (restored).
insert into ids select 'ri4', public.mark_claim_wrong(pg_temp.w(), pg_temp.id('m2'), 'Typo');
select is(pg_temp.mv('e2', 'p_sites', '8'), pg_temp.id('m2'), '92r. entering the value again brings back the same claim');
reset role;
select is((select status from public.review_item where id = pg_temp.id('ri4')) || '/' || (select status::text from public.claim where id = pg_temp.id('m2')),
  'restored/active', '92s. its Review item is no longer pending: restored, the value active');
select is((select count(*)::int from public.audit_log where action = 'claim.restored' and target_id = pg_temp.id('m2')), 1, '92t. audited as restored');
-- M2: restoring an older manual value while a newer one is active: the restored one wins (the newer is superseded).
set local role authenticated;
insert into ids select 'ri5', public.mark_claim_wrong(pg_temp.w(), pg_temp.id('m2'), 'Typo again');
insert into ids select 'm5', pg_temp.mv('e2', 'p_sites', '9');
select lives_ok(format('select public.resolve_review_item(%L::uuid, ''restore'')', pg_temp.id('ri5')), '92u. restore the older manual value');
reset role;
select is((select status::text || ':' || superseded_by from public.claim where id = pg_temp.id('m5')), 'superseded:' || pg_temp.id('m2'),
  '92v. the newer manual value is superseded by the restored one');
select is((select count(*)::int from public.claim c join public.source_record sr on sr.id = c.source_record_id
            where c.entity_id = pg_temp.id('e2') and c.predicate_id = pg_temp.id('p_sites') and c.status = 'active' and sr.connector_id = 'derived:manual'),
  1, '92w. still one active manual claim per cell');
set local role authenticated;

-- ── set_auto_run (A) ─────────────────────────────────────────────────────────
select lives_ok(format('select public.set_auto_run(%L::uuid, %L::uuid, false)', pg_temp.w(), pg_temp.id('p_dbl')), '93. pause auto-run');
select is(pg_temp.t() -> 'columns' -> 1 -> 'auto_run', 'false'::jsonb, '94. the column shows it paused');
reset role;
select is((select jsonb_build_object('before', before, 'after', after) from public.audit_log
            where action = 'predicate.auto_run_changed' and target_id = pg_temp.id('p_dbl')),
  '{"before": {"auto_run": true}, "after": {"auto_run": false}}'::jsonb, '95. audited');
select is((select version from public.predicate where id = pg_temp.id('p_dbl')), 1, '96. auto-run is not a config change (version kept)');
set local role authenticated;
select throws_ok(format('select public.set_auto_run(%L::uuid, %L::uuid, false)', pg_temp.w(), pg_temp.id('p_sites')),
  '22023', null, '97. formula columns only');

-- ── Non-members are refused (42501) by every RPC ─────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a5","role":"authenticated"}', true);
select throws_ok(q, '42501', null, '98. non-member: ' || split_part(q, '(', 1))
  from unnest(array[
    format('select public.account_table(%L::uuid, null, null, null, null)', pg_temp.w()),
    format('select public.cell_detail(%L::uuid, %L::uuid, %L::uuid)', pg_temp.w(), pg_temp.id('e1'), pg_temp.id('p_sites')),
    format('select public.set_manual_value(%L::uuid, %L::uuid, %L::uuid, ''5'')', pg_temp.w(), pg_temp.id('e1'), pg_temp.id('p_sites')),
    format('select public.mark_claim_wrong(%L::uuid, %L::uuid, ''x'')', pg_temp.w(), pg_temp.id('c2_sites')),
    format('select public.pin_view(%L::uuid, ''stale'', true)', pg_temp.w()),
    format('select public.pinned_views(%L::uuid, null)', pg_temp.w()),
    format('select public.search_accounts(%L::uuid, ''alpha'')', pg_temp.w()),
    format('select public.set_auto_run(%L::uuid, %L::uuid, true)', pg_temp.w(), pg_temp.id('p_dbl'))]) q;
-- B (Beta's owner) is no member of Alpha.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a3","role":"authenticated"}', true);
select throws_ok(format('select public.search_accounts(%L::uuid, ''alpha'')', pg_temp.w()), '42501', null, '99. a member of another workspace is refused');

-- ── Search returns at most 8 ─────────────────────────────────────────────────
reset role;
select set_config('app.writer', 'derivation', true);
insert into public.account (org_id, workspace_id, entity_id)
select pg_temp.id('org'), pg_temp.w(), app.create_entity(pg_temp.id('org'), 'company', 'Zed filler ' || n, 'tenant') from generate_series(1, 9) n;
select set_config('app.writer', '', true);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000027a2","role":"authenticated"}', true);
select is(jsonb_array_length(public.search_accounts(pg_temp.w(), 'zed')), 8, '100. top 8');
reset role;

-- ── Grants ───────────────────────────────────────────────────────────────────
select ok(not has_function_privilege('anon', 'public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int, public.entity_type)', 'execute')
          and has_function_privilege('authenticated', 'public.account_table(uuid, uuid, jsonb, jsonb, uuid[], int, int, public.entity_type)', 'execute')
          and has_function_privilege('authenticated', 'public.search_accounts(uuid, text, uuid)', 'execute'),
  '101. account_table is for signed-in users');
select ok(not has_function_privilege('authenticated', 'app.table_brief_columns(uuid, uuid)', 'execute'), '102. app helpers are not granted');

select * from finish();
rollback;
