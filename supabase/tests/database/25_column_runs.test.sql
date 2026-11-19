begin;
create extension if not exists pgtap with schema extensions;
select plan(87);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A = owner, J = Editor, K = Viewer.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000025a1', 'a@cr.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000025a2', 'j@cr.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000025a3', 'k@cr.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a1","role":"authenticated"}', true);
select public.create_organisation('CR Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'CR Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'CR Alpha';
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000025a2', (select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a2', 'j@cr.test'),
  ('00000000-0000-4000-8000-0000000025a3', (select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a3', 'k@cr.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a2', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a3', (select v from ids where k = 'ws'), 'Viewer');

-- The FlytBase columns plus the formula column (as suite 23).
create temp table cfg (k text primary key, v jsonb) on commit drop;
grant all on cfg to authenticated;
insert into cfg values ('full', '{"offer":"Drone docks","hypothesis":"Runs a drone programme",
  "examples":{"positive":["P1","P2","P3"],"negative":["N1","N2"]},
  "sources":{"radar_types":["news_publications"]},
  "predicates": [
   {"key": "drone_programme", "label": "Runs or plans a drone programme", "value_type": "boolean", "role": "signal"},
   {"key": "industry", "label": "Industry", "value_type": "enum",
    "allowed_values": ["energy", "utilities", "public safety", "mining", "drone vendor"], "role": "fit.vertical"},
   {"key": "site_count", "label": "Industrial sites", "value_type": "number", "role": "scale"},
   {"key": "has_programme_and_sites", "label": "Programme and sites", "value_type": "boolean",
    "fill_method": "formula", "formula": "drone_programme and site_count > 0",
    "inputs": ["drone_programme", "site_count"], "role": "info"}]}');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
insert into ids select 'b', public.create_brief((select v from ids where k = 'ws'), 'Drones');
select public.save_brief_draft_ui((select v from ids where k = 'b'), (select v from cfg where k = 'full'));
insert into ids select 'bv', public.start_pilot((select v from ids where k = 'b'));
reset role;
insert into ids select 'dp', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'drone_programme';
insert into ids select 'ind', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'industry';
insert into ids select 'sc', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'site_count';
insert into ids select 'f', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'has_programme_and_sites';

-- Twelve accounts, E01…E12.
insert into ids select 'e' || lpad(n::text, 2, '0'),
       app.create_entity((select v from ids where k = 'org'), 'company', 'CR E' || lpad(n::text, 2, '0'), 'tenant')
  from generate_series(1, 12) n;
insert into public.account (org_id, workspace_id, entity_id)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), v from ids where k ~ '^e\d\d$';

-- Prices: one extract call is 3000 × $2/M + 500 × $8/M = $0.01. E01 has one extracted record.
update public.model_price set usd_per_million = 2 where model = 'claude-sonnet-5-5' and unit = 'ai_input_tokens';
update public.model_price set usd_per_million = 8 where model = 'claude-sonnet-5-5' and unit = 'ai_output_tokens';
insert into public.extractor_version (capability, prompt_version, model, parser_version) values ('extract.cr25', '1', 'test', '1');
insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'radar:x', 'website',
        'https://cr25.example/1', now(), 'cr25-1', 'extracted');
insert into ids select 's1', id from public.source_record where content_hash = 'cr25-1';
insert into public.mention (org_id, source_record_id, entity_type, raw_name, entity_id, match_method)
values ((select v from ids where k = 'org'), (select v from ids where k = 's1'), 'company', 'CR E01',
        (select v from ids where k = 'e01'), 'exact');

-- ── Cost per entity ──────────────────────────────────────────────────────────
select is(app.column_cost_per_entity((select v from ids where k = 'sc')), 0.01::numeric,
  '1. an extract column costs one extract call per extracted record (workspace average 1)');
select is(app.column_cost_per_entity((select v from ids where k = 'f')), 0::numeric, '2. a formula column costs 0');
select is(app.remaining_budget_usd((select v from ids where k = 'ws'), null), null, '3. no cap: remaining is null');

-- ── Seeded cell states for site_count ────────────────────────────────────────
-- E01 value, E02 no_data, E03 error, E04 stale, E05 queued, E06–E12 empty.
select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e01'), (select v from ids where k = 'sc'),
  '4'::jsonb, 'four sites', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e04'), (select v from ids where k = 'sc'),
  '9'::jsonb, 'nine sites', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at, inputs_hash, error)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = e),
       (select v from ids where k = 'sc'), 1, st, now() - interval '1 hour',
       case when st = 'queued' then null else now() - interval '1 hour' end,
       case when e = 'e04' then 'an older config' end, case when st = 'error' then 'boom' end
  from (values ('e02', 'no_data'), ('e03', 'error'), ('e04', 'succeeded'), ('e05', 'queued')) v(e, st);
select is((select string_agg(app.cell_state(v, (select v from ids where k = 'sc')), ',' order by k)
             from ids where k in ('e01', 'e02', 'e03', 'e04', 'e05', 'e06')),
  'value,no_data,error,stale,queued,empty', '4. the seeded states');

-- ── Estimates for each mode (a Viewer may estimate) ──────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a3","role":"authenticated"}', true);
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'empty'),
  '{"count": 8, "per_entity_usd": 0.01, "cost_usd": 0.08, "remaining_usd": null, "within_budget": 8, "max_per_request": 1000}'::jsonb,
  '5. empty: empty and no_data cells, not the queued one');
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'stale') ->> 'count',
  '1', '6. stale: the stale cell');
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'errored') ->> 'count',
  '1', '7. errored: the errored cell');
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'selected', null, null,
            array[(select v from ids where k = 'e01'), (select v from ids where k = 'e05'), (select v from ids where k = 'e03')]) ->> 'count',
  '2', '8. selected: the given entities whatever their state, but not one already queued');
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'empty', null,
            '{"field": {"system": "name"}, "op": "contains", "value": "E1"}') ->> 'count',
  '3', '9. a filter narrows the cells (E10–E12)');
select throws_ok(format('select public.estimate_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'sc'), 'everything'),
  '22023', 'mode must be empty, stale, errored or selected', '10. an unknown mode is refused');
select throws_ok(format('select public.estimate_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'sc'), 'selected'),
  '22023', 'selected needs the entity ids to run', '11. selected without ids is refused');

-- ── A Viewer cannot request ──────────────────────────────────────────────────
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'sc'), 'empty'),
  '42501', 'permission denied', '12. a Viewer cannot request a run');

-- ── An Editor requests ───────────────────────────────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'empty',
            (select v from ids where k = 'b')),
  '{"queued": 8, "cost_usd": 0.08}'::jsonb, '13. the request queues the eight empty cells');
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'sc'), 'empty',
            (select v from ids where k = 'b')),
  '{"queued": 0, "cost_usd": 0}'::jsonb, '14. the same request again queues nothing');
reset role;
select is((select string_agg(e.display_name, ',' order by e.display_name)
             from public.predicate_run r join public.entity e on e.id = r.entity_id
            where r.predicate_id = (select v from ids where k = 'sc') and r.status = 'queued'
              and r.entity_id <> (select v from ids where k = 'e05')),
  'CR E02,CR E06,CR E07,CR E08,CR E09,CR E10,CR E11,CR E12', '15. the right entities are queued');
select is((select count(*)::int from public.predicate_run
            where predicate_id = (select v from ids where k = 'sc') and entity_id = (select v from ids where k = 'e05')),
  1, '16. the cell already queued got no second run');
select is((select count(*)::int from public.predicate_run
            where predicate_id = (select v from ids where k = 'sc') and status = 'queued' and entity_id <> (select v from ids where k = 'e05')
              and predicate_version = 1 and brief_version_id = (select current_version_id from public.brief where id = (select v from ids where k = 'b'))),
  8, '17. the runs carry the column version and the Brief''s current version');
select is((select count(*)::int from public.outbox where event = 'column.run_requested'), 1,
  '18. one column.run_requested for the request that queued runs');
select is((select (select array_agg(x::uuid order by x) from jsonb_array_elements_text(payload -> 'run_ids') x)
             from public.outbox where event = 'column.run_requested'),
  (select array_agg(id order by id) from public.predicate_run
    where predicate_id = (select v from ids where k = 'sc') and status = 'queued' and entity_id <> (select v from ids where k = 'e05')),
  '19. the event names the queued runs');
select is((select workspace_id from public.outbox where event = 'column.run_requested'), (select v from ids where k = 'ws'),
  '20. the event is scoped to the workspace');

-- ── Starting and finishing runs ──────────────────────────────────────────────
insert into ids select 'r_sc', id from public.predicate_run
 where predicate_id = (select v from ids where k = 'sc') and entity_id = (select v from ids where k = 'e06');
select is(app.start_run((select v from ids where k = 'r_sc')) - array['inputs', 'name', 'domain', 'brief_id', 'fill_config',
                                                                        'allowed_values', 'run_condition'],
  jsonb_build_object('started', true, 'status', 'running', 'reclaimed', false, 'org_id', (select v from ids where k = 'org'),
                     'workspace_id', (select v from ids where k = 'ws'), 'entity_id', (select v from ids where k = 'e06'),
                     'predicate_id', (select v from ids where k = 'sc'), 'key', 'site_count', 'fill_method', 'extract',
                     'value_type', 'number'),
  '21. start_run starts a queued run and returns its context');
select is((select status || '|' || (started_at is not null)::text || '|' || inputs_hash from public.predicate_run
            where id = (select v from ids where k = 'r_sc')),
  'running|true|' || app.run_inputs_hash((select v from ids where k = 'e06'), (select v from ids where k = 'sc')),
  '22. the run is running, with started_at and the inputs hash');
select is(app.start_run((select v from ids where k = 'r_sc')), '{"started": false, "status": "running"}'::jsonb,
  '23. starting it again does nothing');
select is(app.start_run(gen_random_uuid()), '{"started": false, "status": null}'::jsonb, '24. a missing run does nothing');

-- A run left running for more than 15 minutes (its worker died) is taken back; a fresh one is not.
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, started_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e09'),
        (select v from ids where k = 'dp'), 1, 'running', now() - interval '30 minutes', now() - interval '20 minutes'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e10'),
        (select v from ids where k = 'dp'), 1, 'running', now() - interval '30 minutes', now() - interval '5 minutes');
insert into ids select 'r_stale', id from public.predicate_run where predicate_id = (select v from ids where k = 'dp') and entity_id = (select v from ids where k = 'e09');
insert into ids select 'r_fresh', id from public.predicate_run where predicate_id = (select v from ids where k = 'dp') and entity_id = (select v from ids where k = 'e10');
select is((select (x ->> 'started') || '|' || (x ->> 'reclaimed') from (select app.start_run((select v from ids where k = 'r_stale')) x) s),
  'true|true', '24b. a run running for 20 minutes is taken back');
select is((select started_at from public.predicate_run where id = (select v from ids where k = 'r_stale')), now(),
  '24c. taking it back restarts its clock');
select is(app.start_run((select v from ids where k = 'r_fresh')), '{"started": false, "status": "running"}'::jsonb,
  '24d. a run running for 5 minutes is left to its worker');
select app.finish_run((select v from ids where k = 'r_stale'), 'no_data');
select app.finish_run((select v from ids where k = 'r_fresh'), 'no_data');

-- Spend settled against the run is its cost.
insert into ids select 'res', (app.reserve_budget((select v from ids where k = 'ws'), null, 0.02, 'column.run', '{}') ->> 'reservation_id')::uuid;
select app.settle_reservation((select v from ids where k = 'res'),
  jsonb_build_object('capability', 'extract', 'provider', 'anthropic', 'model', 'm', 'latency_ms', 5, 'status', 'ok',
                     'predicate_run_id', (select v from ids where k = 'r_sc'),
                     'lines', '[{"unit": "ai_input_tokens", "quantity": 3000, "cost_usd": "0.006"},
                                {"unit": "ai_output_tokens", "quantity": 500, "cost_usd": "0.004"}]'::jsonb));
select is((select count(*)::int from public.usage_ledger where predicate_run_id = (select v from ids where k = 'r_sc') and status = 'ok'),
  2, '25. settled lines carry the predicate run');
select is(app.finish_run((select v from ids where k = 'r_sc'), 'no_data'), 'no_data', '26. finish_run ends the run');
select is((select cost_usd || '|' || (finished_at is not null)::text from public.predicate_run where id = (select v from ids where k = 'r_sc')),
  '0.010000|true', '27. the run costs what was settled against it');
select is(app.finish_run((select v from ids where k = 'r_sc'), 'error', null, null, null, 'late'), 'no_data',
  '28. finishing a finished run does nothing');
select throws_ok(format('select app.finish_run(%L::uuid, %L)', (select v from ids where k = 'r_sc'), 'queued'),
  '22023', null, '29. a run cannot finish queued');

-- ── Review Focus 2: a formula run ending no_data clears the older derived value ──
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, queued_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e07'),
        (select v from ids where k = 'f'), 1, now() - interval '10 minutes');
insert into ids select 'rf1', id from public.predicate_run where predicate_id = (select v from ids where k = 'f') and entity_id = (select v from ids where k = 'e07');
select app.start_run((select v from ids where k = 'rf1'));
insert into ids select 'cf1', app.write_formula_claim((select v from ids where k = 'rf1'), 'true');
select is(app.finish_run((select v from ids where k = 'rf1'), 'succeeded', (select v from ids where k = 'cf1'), '[]'), 'succeeded',
  '30. a formula run succeeds with its claim');
select is(app.cell_state((select v from ids where k = 'e07'), (select v from ids where k = 'f')), 'derived', '31. the cell is derived');
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, queued_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e07'),
        (select v from ids where k = 'f'), 1, now() - interval '5 minutes');
insert into ids select 'rf2', id from public.predicate_run where predicate_id = (select v from ids where k = 'f') and entity_id = (select v from ids where k = 'e07') and status = 'queued';
select app.start_run((select v from ids where k = 'rf2'));
select is(app.finish_run((select v from ids where k = 'rf2'), 'no_data', null, '[]'), 'no_data',
  '32. the re-run ends no_data');
select is((select status::text from public.claim where id = (select v from ids where k = 'cf1')), 'retracted',
  '33. the older formula claim is retracted');
select is(app.cell_state((select v from ids where k = 'e07'), (select v from ids where k = 'f')), 'no_data',
  '34. the cell shows no_data, not the older derived value');
select is((select claim_id from public.predicate_run where id = (select v from ids where k = 'rf2')), null,
  '35. the no_data run has no claim');
select throws_ok(format('select app.clear_formula_claims(%L::uuid)', (select v from ids where k = 'rf2')),
  '55000', null, '36. only a running run clears its cell');
select throws_ok(format('select app.clear_formula_claims(%L::uuid)', (select v from ids where k = 'r_sc')),
  '55000', null, '37. a finished extract run cannot clear anything either');

-- A formula's claim and its succeeded status are written together.
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, queued_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e09'),
        (select v from ids where k = 'f'), 1, now() - interval '3 minutes');
insert into ids select 'rf3', id from public.predicate_run where predicate_id = (select v from ids where k = 'f') and entity_id = (select v from ids where k = 'e09');
select app.start_run((select v from ids where k = 'rf3'));
select is(app.complete_formula_run((select v from ids where k = 'rf3'), 'true', '[]'), 'succeeded',
  '37b. complete_formula_run finishes the run');
select is((select r.status || '|' || c.status::text || '|' || (c.value = 'true'::jsonb)::text
             from public.predicate_run r join public.claim c on c.id = r.claim_id where r.id = (select v from ids where k = 'rf3')),
  'succeeded|active|true', '37c. ... with its active claim');
select throws_ok(format('select app.complete_formula_run(%L::uuid, %L::jsonb, %L::jsonb)', (select v from ids where k = 'rf3'), 'false', '[]'),
  '55000', null, '37d. a finished run cannot write another value');
select is((select count(*)::int from public.claim where entity_id = (select v from ids where k = 'e09')
             and predicate_id = (select v from ids where k = 'f')), 1, '37e. ... and no claim was written');

-- ── Run conditions ───────────────────────────────────────────────────────────
select ok(app.entity_matches((select v from ids where k = 'ws'), null, (select v from ids where k = 'e07'),
            '{"field": {"system": "name"}, "op": "contains", "value": "E07"}'), '38. an entity matching the condition');
select ok(not app.entity_matches((select v from ids where k = 'ws'), null, (select v from ids where k = 'e08'),
            '{"field": {"system": "name"}, "op": "contains", "value": "E07"}'), '39. an entity not matching it');
select throws_ok(format('select app.entity_matches(%L::uuid, null, %L::uuid, %L::jsonb)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'e07'), '{"field": {"column": "work_emial"}, "op": "is_empty"}'),
  '22023', 'run_condition.field: unknown column "work_emial"', '40. a bad condition names its path');

-- ── Auto-run ─────────────────────────────────────────────────────────────────
insert into ids select 'cdp', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e08'),
  (select v from ids where k = 'dp'), 'true'::jsonb, 'drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
insert into ids select 'auto', (app.queue_auto_runs((select v from ids where k = 'cdp')))[1];
select is((select predicate_id || '|' || entity_id || '|' || status || '|' || predicate_version from public.predicate_run
            where id = (select v from ids where k = 'auto')),
  (select v from ids where k = 'f') || '|' || (select v from ids where k = 'e08') || '|queued|1',
  '41. a claim of an input queues the formula column on that entity');
select is((select jsonb_build_object('ws', workspace_id, 'payload', payload) from public.outbox
            where event = 'column.run_requested' and aggregate_id = (select v from ids where k = 'cdp')),
  jsonb_build_object('ws', (select v from ids where k = 'ws'),
                     'payload', jsonb_build_object('run_ids', jsonb_build_array((select v from ids where k = 'auto')))),
  '41b. the auto-run is announced as column.run_requested in the same transaction');
select is(app.queue_auto_runs((select v from ids where k = 'cdp')), '{}'::uuid[], '42. never twice while one is queued');
select is((select count(*)::int from public.predicate_run where entity_id = (select v from ids where k = 'e08')
             and predicate_id <> (select v from ids where k = 'sc')), 1,
  '43. only the dependent formula column was queued (E08''s site_count run is from the request)');
insert into ids select 'loose', app.create_entity((select v from ids where k = 'org'), 'company', 'CR Loose', 'tenant');
insert into ids select 'cind', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'loose'),
  (select v from ids where k = 'ind'), '"energy"'::jsonb, 'energy', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
select is(app.queue_auto_runs((select v from ids where k = 'cind')), '{}'::uuid[], '44. a column no formula reads queues nothing');
update public.predicate set auto_run = false where id = (select v from ids where k = 'f');
insert into ids select 'csc', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e10'),
  (select v from ids where k = 'sc'), '3'::jsonb, 'three', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
select is(app.queue_auto_runs((select v from ids where k = 'csc')), '{}'::uuid[], '45. a formula with auto_run off is not queued');
update public.predicate set auto_run = true where id = (select v from ids where k = 'f');
insert into ids select 'cold', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e09'),
  (select v from ids where k = 'dp'), 'false'::jsonb, 'no drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
update public.claim set status = 'retracted' where id = (select v from ids where k = 'cold');
select is(app.queue_auto_runs((select v from ids where k = 'cold')), '{}'::uuid[], '45b. a claim that is no longer active queues nothing');
insert into ids select 'cloose', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'loose'),
  (select v from ids where k = 'dp'), 'true'::jsonb, 'drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
select is(app.queue_auto_runs((select v from ids where k = 'cloose')), '{}'::uuid[],
  '45c. an entity without an account in the column''s workspace is not queued');
select is((select count(*)::int from public.outbox where event = 'column.run_requested'
             and aggregate_id in ((select v from ids where k = 'cold'), (select v from ids where k = 'cloose'))), 0,
  '45d. ... and nothing is announced');

-- ── Review Focus 4: a request larger than the budget ─────────────────────────
insert into public.budget (org_id, workspace_id, brief_id, period, cap_usd)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), null, 'day', 0.05 + 0.01);
-- 0.01 is already settled today (test 25): $0.05 is left.
select is(app.remaining_budget_usd((select v from ids where k = 'ws'), null), 0.05::numeric, '46. $0.05 is left');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'ind'), 'empty'),
  '{"count": 12, "per_entity_usd": 0.01, "cost_usd": 0.12, "remaining_usd": 0.05, "within_budget": 5, "max_per_request": 1000}'::jsonb,
  '47. the estimate is count × cost per entity, and 5 fit');
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'ind'), 'empty'),
  '55000', 'this run needs $0.12 but $0.05 is left in the budget; run the first 5 within budget',
  '48. the request is refused with the exact message and N');
reset role;
select is((select count(*)::int from public.predicate_run where predicate_id = (select v from ids where k = 'ind')), 0,
  '49. the refused request queued nothing');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'ind'), 'empty',
            null, null, null, 5),
  '{"queued": 5, "cost_usd": 0.05}'::jsonb, '50. asking again for the first 5 succeeds');
reset role;
select is((select string_agg(e.display_name, ',' order by e.display_name)
             from public.predicate_run r join public.entity e on e.id = r.entity_id
            where r.predicate_id = (select v from ids where k = 'ind')),
  'CR E01,CR E02,CR E03,CR E04,CR E05', '51. the first 5 in view order were queued');

-- Over the cap: $0 left. A formula is free and is never refused.
insert into public.usage_ledger (org_id, workspace_id, capability, unit, quantity, cost_usd, status)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'extract', 'ai_input_tokens', 1, 0.20, 'ok');
select is(app.remaining_budget_usd((select v from ids where k = 'ws'), null), 0::numeric, '52. nothing is left (never below 0)');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'e11'), (select v from ids where k = 'e12')]),
  '{"count": 2, "per_entity_usd": 0, "cost_usd": 0, "remaining_usd": 0, "within_budget": 2, "max_per_request": 1000}'::jsonb,
  '53. a formula estimate costs 0 and all of it fits');
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'e11'), (select v from ids where k = 'e12')]),
  '{"queued": 2, "cost_usd": 0}'::jsonb, '54. a formula request goes through with $0 left');
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L, null, null, %L::uuid[])', (select v from ids where k = 'ws'),
    (select v from ids where k = 'ind'), 'selected', array[(select v from ids where k = 'e12')]),
  '55000', 'this run needs $0.01 but $0.00 is left in the budget; run the first 0 within budget',
  '55. an extract request with $0 left is refused, naming 0');
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L, null, null, null, 0)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'f'), 'empty'),
  '22023', 'limit must be 1 or more', '56. a limit below 1 is refused');
reset role;

-- ── At most 1000 runs per request ────────────────────────────────────────────
insert into public.account (org_id, workspace_id, entity_id)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'),
       app.create_entity((select v from ids where k = 'org'), 'company', 'CR Bulk ' || lpad(n::text, 4, '0'), 'tenant')
  from generate_series(1, 1001) n;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select throws_like(format('select public.request_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'f'), 'empty'),
  'a run request has at most 1000 cells (this one has 10%); set a limit or narrow the filter',
  '57. more than 1000 runs are refused');
select is((select (x ->> 'count')::int > 1000 and (x ->> 'within_budget')::int = 1000 and (x ->> 'max_per_request')::int = 1000
             from (select public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'empty') x) s),
  true, '57b. the estimate keeps the true count but never suggests more than 1000');
reset role;
select is((select count(*)::int from public.predicate_run where predicate_id = (select v from ids where k = 'f') and status = 'queued'),
  3, '58. the refused request queued nothing (the auto-run and two formula runs remain)');

-- ── The cost estimate caps records per entity at 20 ──────────────────────────
insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'radar:x', 'website',
       'https://cr25.example/b' || n, now(), 'cr25-b' || n, 'extracted'
  from generate_series(1, 60) n;
insert into public.mention (org_id, source_record_id, entity_type, raw_name, entity_id, match_method)
select (select v from ids where k = 'org'), id, 'company', 'CR E02', (select v from ids where k = 'e02'), 'exact'
  from public.source_record where content_hash like 'cr25-b%';
select is(app.column_cost_per_entity((select v from ids where k = 'sc')), 0.2::numeric,
  '59. an average of 30.5 records per entity is capped at 20');

-- ── Across workspaces ──────────────────────────────────────────────────────────
-- Workspace B in the same organisation; L is a Viewer of B only.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000025a4', 'l@cr.test', 'authenticated', 'authenticated');
with w as (insert into public.workspace (org_id, name) values ((select v from ids where k = 'org'), 'CR Beta') returning id)
insert into ids select 'wsb', id from w;
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000025a4', (select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a4', 'l@cr.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000025a4', (select v from ids where k = 'wsb'), 'Viewer');
with p as (insert into public.predicate (key, org_id, workspace_id, label, value_type)
           values ('beta_col', (select v from ids where k = 'org'), (select v from ids where k = 'wsb'), 'Beta', 'text') returning id)
insert into ids select 'pb', id from p;
insert into ids select 'eb', app.create_entity((select v from ids where k = 'org'), 'company', 'CR Beta Co', 'tenant');
insert into public.account (org_id, workspace_id, entity_id)
values ((select v from ids where k = 'org'), (select v from ids where k = 'wsb'), (select v from ids where k = 'eb'));
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a4","role":"authenticated"}', true);
select throws_ok(format('select public.estimate_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'f'), 'empty'),
  '42501', 'permission denied', '59b. a Viewer of workspace B cannot estimate on A');
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'f'), 'empty'),
  '42501', 'permission denied', '59c. ... nor request on A');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select throws_ok(format('select public.estimate_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'pb'), 'empty'),
  '22023', 'column not found in this workspace', '59d. a column of workspace B cannot be estimated in A');
select throws_ok(format('select public.request_column_run(%L::uuid, %L::uuid, %L)', (select v from ids where k = 'ws'),
    (select v from ids where k = 'pb'), 'empty'),
  '22023', 'column not found in this workspace', '59e. ... nor requested in A');
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'eb')]) ->> 'count', '0',
  '59f. a selected entity that is only an account of workspace B is dropped');
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'eb')]), '{"queued": 0, "cost_usd": 0}'::jsonb,
  '59g. ... and is not queued');
reset role;
select is((select count(*)::int from public.predicate_run where entity_id = (select v from ids where k = 'eb')), 0,
  '59h. no run exists for it');

-- ── Abandoned runs (running for more than 15 minutes) never block a cell ─────
insert into ids select 'e_old', app.create_entity((select v from ids where k = 'org'), 'company', 'CR Abandoned', 'tenant');
insert into ids select 'e_new', app.create_entity((select v from ids where k = 'org'), 'company', 'CR Busy', 'tenant');
insert into ids select 'e_auto', app.create_entity((select v from ids where k = 'org'), 'company', 'CR Auto Abandoned', 'tenant');
insert into public.account (org_id, workspace_id, entity_id)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), v from ids where k in ('e_old', 'e_new', 'e_auto');
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, started_at)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = e),
       (select v from ids where k = 'f'), 1, 'running', now() - interval '1 hour', now() - m * interval '1 minute'
  from (values ('e_old', 20), ('e_new', 5), ('e_auto', 20)) x(e, m);
insert into ids select 'r_old', id from public.predicate_run where entity_id = (select v from ids where k = 'e_old');
insert into ids select 'r_new', id from public.predicate_run where entity_id = (select v from ids where k = 'e_new');
insert into ids select 'r_auto', id from public.predicate_run where entity_id = (select v from ids where k = 'e_auto');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000025a2","role":"authenticated"}', true);
select is(public.estimate_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'e_old'), (select v from ids where k = 'e_new')]) ->> 'count', '1',
  '61. a cell whose run has been running for 20 minutes is counted; one running for 5 minutes is not');
select is(public.request_column_run((select v from ids where k = 'ws'), (select v from ids where k = 'f'), 'selected', null, null,
            array[(select v from ids where k = 'e_old'), (select v from ids where k = 'e_new')]),
  '{"queued": 1, "cost_usd": 0}'::jsonb, '62. the request queues the abandoned cell');
reset role;
select is((select status || '|' || error || '|' || (finished_at is not null)::text from public.predicate_run
            where id = (select v from ids where k = 'r_old')),
  'error|abandoned|true', '63. the abandoned run is closed as error, abandoned');
insert into ids select 'r_old2', id from public.predicate_run
 where entity_id = (select v from ids where k = 'e_old') and status = 'queued';
select ok((select v from ids where k = 'r_old2') is not null, '64. a new queued run exists for that cell');
select ok(exists (select 1 from public.outbox where event = 'column.run_requested'
                   and payload -> 'run_ids' @> jsonb_build_array((select v from ids where k = 'r_old2'))),
  '65. a column.run_requested event carries the new run');
select is((select string_agg(status, ',') from public.predicate_run where entity_id = (select v from ids where k = 'e_new')),
  'running', '66. the run running for 5 minutes is untouched and gets no second run');
insert into ids select 'c_auto', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_auto'),
  (select v from ids where k = 'dp'), 'true'::jsonb, 'drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.cr25'), 80::smallint);
select is(cardinality(app.queue_auto_runs((select v from ids where k = 'c_auto'))), 1,
  '67. an auto-run on a cell with an abandoned run queues a new run');
select is((select status || '|' || error from public.predicate_run where id = (select v from ids where k = 'r_auto')),
  'error|abandoned', '68. ... after closing the abandoned one');

-- ── Grants ───────────────────────────────────────────────────────────────────
select ok(not has_function_privilege('authenticated', 'app.queue_auto_runs(uuid)', 'execute')
          and not has_function_privilege('authenticated', 'app.finish_run(uuid, text, uuid, jsonb, jsonb, text)', 'execute')
          and has_function_privilege('service_role', 'app.queue_auto_runs(uuid)', 'execute'),
  '60. the executor helpers are for the service role only');

select * from finish();
rollback;
