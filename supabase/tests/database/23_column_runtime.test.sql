begin;
create extension if not exists pgtap with schema extensions;
select plan(74);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A = owner, J = Editor, K = Viewer, X = signed in but not a member.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000cd01', 'a@rt.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cd02', 'j@rt.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cd03', 'k@rt.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cd04', 'x@rt.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd01","role":"authenticated"}', true);
select public.create_organisation('RT Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'RT Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'RT Alpha';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000cd02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cd02', 'j@rt.test'),
  ('00000000-0000-4000-8000-00000000cd03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cd03', 'k@rt.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cd02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cd03', (select v from ids where k = 'ws'), 'Viewer');

create temp table cfg (k text primary key, v jsonb) on commit drop;
grant all on cfg to authenticated;
-- The FlytBase sample (as suite 22) plus one formula column.
insert into cfg values ('cols', '{"predicates": [
   {"key": "drone_programme", "label": "Runs or plans a drone programme", "value_type": "boolean",
    "allowed_values": null, "extraction_hint": "Announced, tendered or hiring for drone operations",
    "decay_half_life_days": 365, "role": "signal"},
   {"key": "industry", "label": "Industry", "value_type": "enum",
    "allowed_values": ["energy", "utilities", "public safety", "mining", "drone vendor"],
    "extraction_hint": "", "decay_half_life_days": null, "role": "fit.vertical"},
   {"key": "site_count", "label": "Industrial sites", "value_type": "number", "allowed_values": null,
    "extraction_hint": "Plants, substations or sites operated", "decay_half_life_days": 730, "role": "scale"},
   {"key": "has_programme_and_sites", "label": "Programme and sites", "value_type": "boolean",
    "fill_method": "formula", "formula": "drone_programme and site_count > 0",
    "inputs": ["drone_programme", "site_count"], "role": "info"}],
 "evidence_rules": [
   {"predicate": "drone_programme", "kind": "requires", "op": "is_true", "value": null,
    "min_sources": 1, "max_age_days": 730},
   {"predicate": "industry", "kind": "disqualifies", "op": "one_of", "value": ["drone vendor"],
    "min_sources": 1, "max_age_days": null}]}');
insert into cfg values ('base', '{"offer":"Drone docks","hypothesis":"Runs a drone programme",
  "examples":{"positive":["P1","P2","P3"],"negative":["N1","N2"]},
  "sources":{"radar_types":["news_publications"]}}');
insert into cfg select 'full', (select v from cfg where k = 'base') || (select v from cfg where k = 'cols');

-- ── Formula spec validation ──────────────────────────────────────────────────
select lives_ok(format('select app.validate_brief_columns(%L::jsonb)', (select v from cfg where k = 'cols')),
  '1. a formula column with two inputs is valid');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    (select v from cfg where k = 'cols') #- '{predicates,3,formula}'),
  '22023', 'column "has_programme_and_sites": a formula column needs a formula of 1–500 characters',
  '2. a formula column without a formula is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,3,inputs}', '[]')),
  '22023', 'column "has_programme_and_sites": a formula reads 1–10 other columns',
  '3. a formula column with no inputs is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,3,inputs}', '["drone_programme", "fleet_size"]')),
  '22023', 'column "has_programme_and_sites": input "fleet_size" is not a column of this Brief',
  '4. an input that is not in the Brief is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,3,inputs}', '["drone_programme", "has_programme_and_sites"]')),
  '22023', 'column "has_programme_and_sites": a formula cannot read its own column',
  '5. a formula reading its own key is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{evidence_rules,0}',
      '{"predicate":"has_programme_and_sites","kind":"requires","op":"is_true","value":null,"min_sources":1,"max_age_days":null}')),
  '22023', 'column "has_programme_and_sites": an evidence rule cannot point at a formula column',
  '6. a rule pointing at a formula column is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_build_object('predicates', (select jsonb_agg(jsonb_build_object(
      'key', 'col_' || n, 'label', 'Column ' || n, 'value_type', 'text', 'allowed_values', null,
      'extraction_hint', '', 'decay_half_life_days', null, 'role', 'info')) from generate_series(1, 11) n)
      || jsonb_build_array(jsonb_build_object('key', 'joined', 'label', 'Joined', 'value_type', 'text',
           'role', 'info', 'fill_method', 'formula', 'formula', 'concat(col_1)',
           'inputs', (select jsonb_agg('col_' || n) from generate_series(1, 11) n))))),
  '22023', 'column "joined": a formula reads 1–10 other columns',
  '7. a formula with 11 inputs is refused');
select lives_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_build_object('predicates', (select jsonb_agg(jsonb_build_object(
      'key', 'col_' || n, 'label', 'Column ' || n, 'value_type', 'text', 'allowed_values', null,
      'extraction_hint', '', 'decay_half_life_days', null, 'role', 'info')) from generate_series(1, 10) n)
      || jsonb_build_array(jsonb_build_object('key', 'joined', 'label', 'Joined', 'value_type', 'text',
           'role', 'info', 'fill_method', 'formula', 'formula', 'concat(col_1)',
           'inputs', (select jsonb_agg('col_' || n) from generate_series(1, 10) n))))),
  '8. a formula with 10 inputs is allowed');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,0,fill_method}', '"research"')),
  '22023', 'column "drone_programme": fill_method must be extract or formula',
  '9. a fill method other than extract or formula is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,0,formula}', '"1 + 1"')),
  '22023', 'column "drone_programme": only a formula column has a formula',
  '10. an extract column with a formula is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,3,inputs}', '["drone_programme", "drone_programme"]')),
  '22023', 'column "has_programme_and_sites": a formula reads 1–10 other columns',
  '11. a repeated input is refused');

-- ── A cycle is refused at save, naming it; nothing is saved ──────────────────
insert into cfg select 'cycle', (select v from cfg where k = 'base') || '{"predicates":[
  {"key":"col_a","label":"A","value_type":"number","role":"info","fill_method":"formula","formula":"col_b + 1","inputs":["col_b"]},
  {"key":"col_b","label":"B","value_type":"number","role":"info","fill_method":"formula","formula":"col_a + 1","inputs":["col_a"]}]}'::jsonb;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
insert into ids select 'a', public.create_brief((select v from ids where k = 'ws'), 'Brief A');
insert into ids select 'b', public.create_brief((select v from ids where k = 'ws'), 'Brief B');
select throws_ok(format('select public.save_brief_draft_ui(%L::uuid, %L::jsonb)', (select v from ids where k = 'a'),
    (select v from cfg where k = 'cycle')),
  '22023', 'formula columns form a cycle: col_a → col_b → col_a',
  '12. a cycle a → b → a is refused at save, naming both keys');
reset role;
select ok((select draft_config is null from public.brief where id = (select v from ids where k = 'a')),
  '13. the refused save leaves nothing saved');

select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    '{"predicates":["fleet_size",
      {"key":"big_fleet","label":"Big fleet","value_type":"boolean","role":"info","fill_method":"formula",
       "formula":"fleet_size > 10","inputs":["fleet_size"]}]}'),
  '22023', 'column "big_fleet": formula input "fleet_size" has no column spec; give it one first',
  '13b. an input naming a legacy key gets a clear message');

-- A dense cyclic graph (12 formulas, each reading 10 others) is refused quickly, naming a shortest cycle.
create temp table clock (k text primary key, v timestamptz) on commit drop;
insert into clock values ('dense', clock_timestamp());
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_build_object('predicates', (select jsonb_agg(jsonb_build_object(
      'key', 'col_' || lpad(n::text, 2, '0'), 'label', 'Column ' || n, 'value_type', 'number', 'role', 'info',
      'fill_method', 'formula', 'formula', '1',
      'inputs', (select jsonb_agg('col_' || lpad((((n - 1 + j) % 12) + 1)::text, 2, '0')) from generate_series(1, 10) j)))
      from generate_series(1, 12) n))),
  '22023', 'formula columns form a cycle: col_01 → col_03 → col_01',
  '13c. 12 formulas each reading 10 others are refused with a shortest cycle');
select ok(clock_timestamp() - (select v from clock where k = 'dense') < interval '2 seconds',
  '13d. the dense cycle check finishes in under 2 seconds');

-- ── Publish syncs the formula column ─────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'), (select v from cfg where k = 'full'));
insert into ids select 'a1', public.start_pilot((select v from ids where k = 'a'));
reset role;

insert into ids select 'dp', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'drone_programme';
insert into ids select 'ind', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'industry';
insert into ids select 'sc', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'site_count';
insert into ids select 'f', id from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'has_programme_and_sites';

select is((select count(*)::int from public.predicate where workspace_id = (select v from ids where k = 'ws')),
  4, '14. publish creates four predicates');
select is((select fill_method from public.predicate where id = (select v from ids where k = 'f')),
  'formula', '15. the formula column has fill_method formula');
select is((select fill_config from public.predicate where id = (select v from ids where k = 'f')),
  '{"formula": "drone_programme and site_count > 0"}'::jsonb, '16. fill_config holds the formula');
select is((select input_predicate_ids from public.predicate where id = (select v from ids where k = 'f')),
  array[(select v from ids where k = 'dp'), (select v from ids where k = 'sc')],
  '17. input_predicate_ids are the two input predicates');
select ok((select auto_run from public.predicate where id = (select v from ids where k = 'f')),
  '18. a formula column auto-runs');
select is((select jsonb_build_object('fill_method', fill_method, 'fill_config', fill_config, 'inputs', input_predicate_ids,
                                     'auto_run', auto_run, 'version', version)
             from public.predicate where id = (select v from ids where k = 'sc')),
  '{"fill_method":"extract","fill_config":{},"inputs":[],"auto_run":false,"version":1}'::jsonb,
  '19. an extract column keeps the defaults');
select is((select count(*)::int from public.predicate where config_hash is null), 0,
  '20. every predicate has a config_hash');
select is((select config_hash from public.predicate where id = (select v from ids where k = 'f')),
  md5(jsonb_build_object('fill_method', 'formula',
                         'fill_config', '{"formula": "drone_programme and site_count > 0"}'::jsonb,
                         'inputs', '["drone_programme", "site_count"]'::jsonb,
                         'value_type', 'boolean', 'allowed_values', null)::text),
  '21. config_hash is the md5 of the canonical config');
select is((select version from public.predicate where id = (select v from ids where k = 'f')), 1,
  '22. a new formula column starts at version 1');

-- ── Republish: same spec keeps the version; a new formula bumps it ───────────
create temp table snap (k text primary key, v text) on commit drop;
insert into snap select 'hash1', config_hash from public.predicate where id = (select v from ids where k = 'f');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'),
  (select v || '{"hypothesis":"Runs or tenders a drone programme"}' from cfg where k = 'full'));
insert into ids select 'p2', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p2'), 'Narrow', true, '{}', 0, 0, false);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
insert into ids select 'a2', public.publish_brief((select v from ids where k = 'a'));
reset role;
select is((select version from public.predicate where id = (select v from ids where k = 'f')), 1,
  '23. republishing the same spec keeps version 1');
select is((select config_hash from public.predicate where id = (select v from ids where k = 'f')),
  (select v from snap where k = 'hash1'), '24. republishing the same spec keeps the config_hash');

-- The same inputs in another order are the same config.
insert into cfg select 'full3', jsonb_set(jsonb_set(v, '{predicates,3,formula}', '"drone_programme and site_count > 2"'),
                                          '{predicates,3,inputs}', '["site_count", "drone_programme"]')
  from cfg where k = 'full';
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'), (select v from cfg where k = 'full3'));
insert into ids select 'p3', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p3'), 'Narrow', true, '{}', 0, 0, false);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
insert into ids select 'a3', public.publish_brief((select v from ids where k = 'a'));
reset role;
select is((select version from public.predicate where id = (select v from ids where k = 'f')), 2,
  '25. a changed formula moves the version to 2');
select isnt((select config_hash from public.predicate where id = (select v from ids where k = 'f')),
  (select v from snap where k = 'hash1'), '26. a changed formula changes the config_hash');
select is((select fill_config ->> 'formula' from public.predicate where id = (select v from ids where k = 'f')),
  'drone_programme and site_count > 2', '27. the new formula is stored');
select is((select count(*)::int from public.audit_log where action = 'predicate.fill_changed'
             and target_id = (select v from ids where k = 'f')),
  1, '28. the formula change is audited once');
select is((select version from public.predicate where id = (select v from ids where k = 'sc')), 1,
  '29. an unchanged extract column keeps version 1');

-- ── Fill-method clash ────────────────────────────────────────────────────────
insert into cfg select 'clash', (select v from cfg where k = 'base') || '{"predicates":[
  {"key":"drone_programme","label":"Runs or plans a drone programme","value_type":"boolean","role":"signal"},
  {"key":"site_count","label":"Sites","value_type":"number","role":"scale","fill_method":"formula",
   "formula":"if(drone_programme, 1, 0)","inputs":["drone_programme"]}]}'::jsonb;
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
select throws_ok(format('select public.save_brief_draft_ui(%L::uuid, %L::jsonb)', (select v from ids where k = 'b'),
    (select v from cfg where k = 'clash')),
  '23514', 'column "site_count" already exists in this workspace filled by extract; give it another key',
  '30. save refuses an extract key republished as formula');
reset role;
select app.update_brief_draft((select v from ids where k = 'b'), (select v from cfg where k = 'clash'));
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd02","role":"authenticated"}', true);
select throws_ok(format('select public.start_pilot(%L::uuid)', (select v from ids where k = 'b')),
  '23514', 'column "site_count" already exists in this workspace filled by extract; give it another key',
  '31. publish refuses an extract key republished as formula');
reset role;
select is((select fill_method from public.predicate where id = (select v from ids where k = 'sc')),
  'extract', '32. the refused publish leaves the predicate an extract column');

-- ── The derived source record ────────────────────────────────────────────────
insert into ids select 'dsr', app.derived_source_record((select v from ids where k = 'ws'), 'formula');
select is(app.derived_source_record((select v from ids where k = 'ws'), 'formula'), (select v from ids where k = 'dsr'),
  '33. derived_source_record returns the same record each time');
select is((select jsonb_build_object('connector_id', connector_id, 'source_type', source_type,
                                     'content_hash', content_hash, 'workspace_id', workspace_id)
             from public.source_record where id = (select v from ids where k = 'dsr')),
  jsonb_build_object('connector_id', 'derived:formula', 'source_type', 'formula',
                     'content_hash', 'derived:formula:' || (select v from ids where k = 'ws'),
                     'workspace_id', (select v from ids where k = 'ws')),
  '34. the derived record is named derived:formula for its workspace');
select ok((select extraction_status <> 'pending' and extraction_status <> 'extracted'
             from public.source_record where id = (select v from ids where k = 'dsr')),
  '35. the derived record is never pending or extracted, so extraction never picks it up');
select is((select count(*)::int from public.extractor_version
            where capability = 'formula' and prompt_version = 'formula-v1' and model = 'none' and parser_version = '1'),
  1, '36. one formula extractor_version exists');
select throws_ok(format('select app.derived_source_record(%L::uuid, %L)', (select v from ids where k = 'ws'), 'research'),
  '22023', null, '37. an unknown derived kind is refused');
select is((select count(*)::int from public.outbox where aggregate_id = (select v from ids where k = 'dsr')), 0,
  '37b. the derived record emits no source_record.created');

-- ── Formula claims ───────────────────────────────────────────────────────────
insert into ids select 'e1', app.create_entity((select v from ids where k = 'org'), 'company', 'RT Derived Co', 'tenant');
with r as (
  insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'),
          (select v from ids where k = 'f'), 2, 'running', now() - interval '2 hours')
  returning id)
insert into ids select 'r1', id from r;
insert into ids select 'c1', app.write_formula_claim((select v from ids where k = 'r1'), 'true');
select is(app.write_formula_claim((select v from ids where k = 'r1'), 'true'), (select v from ids where k = 'c1'),
  '38. the same value twice returns the same claim');
select is((select count(*)::int from public.claim where entity_id = (select v from ids where k = 'e1')), 1,
  '39. the same value twice gives one claim');
select is((select count(*)::int from public.outbox where event = 'claim.created'
             and payload ->> 'entity_id' = (select v::text from ids where k = 'e1')),
  1, '40. the same value twice emits one claim.created');
select is((select jsonb_build_object('confidence', confidence, 'quote', quote, 'source', source_record_id)
             from public.claim where id = (select v from ids where k = 'c1')),
  jsonb_build_object('confidence', 100, 'quote', null, 'source', (select v from ids where k = 'dsr')),
  '41. a formula claim has confidence 100, no quote, and the derived source record');
select is((select claim_id from public.predicate_run where id = (select v from ids where k = 'r1')),
  (select v from ids where k = 'c1'), '42. the run is linked to its claim');

update public.predicate_run set status = 'succeeded', finished_at = now() - interval '2 hours',
       inputs_hash = app.run_inputs_hash(entity_id, predicate_id)
 where id = (select v from ids where k = 'r1');
with r as (
  insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'),
          (select v from ids where k = 'f'), 2, 'running', now() - interval '1 hour')
  returning id)
insert into ids select 'r2', id from r;
insert into ids select 'c2', app.write_formula_claim((select v from ids where k = 'r2'), 'false');
select isnt((select v from ids where k = 'c2'), (select v from ids where k = 'c1'), '43. a new value creates a new claim');
select is((select status::text || '|' || superseded_by::text from public.claim where id = (select v from ids where k = 'c1')),
  'superseded|' || (select v::text from ids where k = 'c2'), '44. the old claim is superseded by the new one');
select is((select count(*)::int from public.claim where entity_id = (select v from ids where k = 'e1') and status = 'active'),
  1, '45. one formula claim stays active');
select is((select count(*)::int from public.outbox where event = 'claim.created'
             and payload ->> 'entity_id' = (select v::text from ids where k = 'e1')),
  2, '46. the new value emits one more claim.created');
update public.predicate_run set status = 'succeeded', finished_at = now() - interval '1 hour',
       inputs_hash = app.run_inputs_hash(entity_id, predicate_id)
 where id = (select v from ids where k = 'r2');
select throws_ok(format('select app.write_formula_claim(%L::uuid, %L::jsonb)', (select v from ids where k = 'r2'), 'true'),
  '55000', null, '46b. a finished run cannot write a claim');
with r as (
  insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'),
          (select v from ids where k = 'f'), 2, 'cancelled', now() - interval '90 minutes', now() - interval '90 minutes')
  returning id)
insert into ids select 'rc', id from r;
select throws_ok(format('select app.write_formula_claim(%L::uuid, %L::jsonb)', (select v from ids where k = 'rc'), 'true'),
  '55000', null, '46c. a cancelled run cannot write a claim');
select is((select count(*)::int from public.claim where entity_id = (select v from ids where k = 'e1')), 2,
  '46d. the refused writes add no claim');

-- ── Inputs hash ──────────────────────────────────────────────────────────────
insert into ids select 'e_val', app.create_entity((select v from ids where k = 'org'), 'company', 'RT Value Co', 'tenant');
insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('extract.rt23', '1', 'test', '1');
insert into ids select 's1', app.create_source_record((select v from ids where k = 'org'), (select v from ids where k = 'ws'),
  'radar:x', 'news', 'https://rt23.example.com/1', now(), 'rt23-hash-1', null, 'en', 'text one');
select is(app.run_inputs_hash((select v from ids where k = 'e_val'), (select v from ids where k = 'sc')),
  md5((select config_hash from public.predicate where id = (select v from ids where k = 'sc')) || '|'),
  '47. an extract column hashes its config only');
insert into ids select 'ci1', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_val'),
  (select v from ids where k = 'sc'), '4'::jsonb, 'four sites', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.rt23'), 80::smallint);
insert into ids select 'ci2', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_val'),
  (select v from ids where k = 'dp'), 'true'::jsonb, 'drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.rt23'), 80::smallint);
select is(app.run_inputs_hash((select v from ids where k = 'e_val'), (select v from ids where k = 'f')),
  md5((select config_hash from public.predicate where id = (select v from ids where k = 'f')) || '|' ||
      (select string_agg(x::text, ',' order by x::text collate "C")
         from unnest(array[(select v from ids where k = 'ci1'), (select v from ids where k = 'ci2')]) x)),
  '48. a formula column hashes its config and the sorted input claim ids');

-- ── The nine cell states ─────────────────────────────────────────────────────
insert into ids select 'e_' || s, app.create_entity((select v from ids where k = 'org'), 'company', 'RT ' || s, 'tenant')
  from unnest(array['queued', 'running', 'error', 'skipped', 'stale', 'nodata', 'empty']) s;
-- An older succeeded run under each newer one: only the latest run counts.
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_' || s),
       (select v from ids where k = 'ind'), 1, 'succeeded', now() - interval '3 hours', now() - interval '3 hours'
  from unnest(array['queued', 'running', 'error', 'skipped', 'nodata']) s;
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, error)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_' || s),
       (select v from ids where k = 'ind'), 1, st, now() - interval '1 hour', case when st = 'error' then 'boom' end
  from (values ('queued', 'queued'), ('running', 'running'), ('error', 'error'),
               ('skipped', 'condition_not_met'), ('nodata', 'no_data')) v(s, st);

-- stale: a succeeded formula run, then a new claim on one of its inputs.
with r as (
  insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at, inputs_hash)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_stale'),
          (select v from ids where k = 'f'), 2, 'running', now() - interval '1 hour', null,
          app.run_inputs_hash((select v from ids where k = 'e_stale'), (select v from ids where k = 'f')))
  returning id)
insert into ids select 'rs', id from r;
select app.write_formula_claim((select v from ids where k = 'rs'), 'false');
update public.predicate_run set status = 'succeeded', finished_at = now() - interval '1 hour'
 where id = (select v from ids where k = 'rs');
select is(app.cell_state((select v from ids where k = 'e_stale'), (select v from ids where k = 'f')), 'derived',
  '49. a formula claim with unchanged inputs is derived');
select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_stale'),
  (select v from ids where k = 'dp'), 'true'::jsonb, 'drones', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.rt23'), 80::smallint);
-- value: an extract claim and no run.
select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_val'),
  (select v from ids where k = 'ind'), '"energy"'::jsonb, 'an energy firm', null, null, (select v from ids where k = 's1'), now(),
  (select id from public.extractor_version where capability = 'extract.rt23'), 80::smallint);

select is(app.cell_state((select v from ids where k = 'e_queued'), (select v from ids where k = 'ind')), 'queued', '50. queued');
select is(app.cell_state((select v from ids where k = 'e_running'), (select v from ids where k = 'ind')), 'running', '51. running');
select is(app.cell_state((select v from ids where k = 'e_error'), (select v from ids where k = 'ind')), 'error', '52. error');
select is(app.cell_state((select v from ids where k = 'e_skipped'), (select v from ids where k = 'ind')), 'skipped', '53. skipped');
select is(app.cell_state((select v from ids where k = 'e_stale'), (select v from ids where k = 'f')), 'stale',
  '54. stale: an input claim arrived after the run');
select is(app.cell_state((select v from ids where k = 'e1'), (select v from ids where k = 'f')), 'derived', '55. derived');
select is(app.cell_state((select v from ids where k = 'e_val'), (select v from ids where k = 'ind')), 'value', '56. value');
select is(app.cell_state((select v from ids where k = 'e_nodata'), (select v from ids where k = 'ind')), 'no_data', '57. no_data');
select is(app.cell_state((select v from ids where k = 'e_empty'), (select v from ids where k = 'ind')), 'empty', '58. empty');

-- ── Run uniqueness ───────────────────────────────────────────────────────────
select throws_ok(format($$insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status)
                          values (%L, %L, %L, %L, 1, 'queued')$$,
    (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_queued'),
    (select v from ids where k = 'ind')),
  '23505', null, '59. a second open run for the same cell is refused');

-- ── cells and access ─────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd03","role":"authenticated"}', true);
select is((select jsonb_agg(jsonb_build_object('state', state, 'value', value, 'confidence', confidence, 'claim', claim_id,
                                               'run', run_id, 'run_status', run_status))
             from public.cells((select v from ids where k = 'ws'), array[(select v from ids where k = 'e1')],
                               array[(select v from ids where k = 'f')])),
  jsonb_build_array(jsonb_build_object('state', 'derived', 'value', false, 'confidence', 100,
                                       'claim', (select v from ids where k = 'c2'),
                                       'run', (select v from ids where k = 'r2'), 'run_status', 'succeeded')),
  '60. a Viewer reads a cell with its value, claim and latest run');
select is((select string_agg(state, ',' order by state)
             from public.cells((select v from ids where k = 'ws'),
                               (select array_agg(v) from ids where k like 'e\_%'),
                               array[(select v from ids where k = 'ind')])),
  'empty,empty,error,no_data,queued,running,skipped,value',
  '61. cells returns the state of every requested cell');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd04","role":"authenticated"}', true);
select throws_ok(format('select * from public.cells(%L::uuid, %L::uuid[], %L::uuid[])', (select v from ids where k = 'ws'),
    array[(select v from ids where k = 'e1')], array[(select v from ids where k = 'f')]),
  '42501', null, '62. cells refuses a non-member');
select is((select count(*)::int from public.predicate_run), 0, '62b. a non-member reads no runs');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cd03","role":"authenticated"}', true);
select throws_ok(format('select * from public.cells(%L::uuid, %L::uuid[], %L::uuid[])', (select v from ids where k = 'ws'),
    (select array_agg(gen_random_uuid()) from generate_series(1, 501)), array[(select v from ids where k = 'f')]),
  '22023', null, '63. cells refuses more than 500 entities');
select ok((select count(*) from public.predicate_run) > 0, '64. a Viewer reads the workspace runs');
select throws_ok(format($$insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status)
                          values (%L, %L, %L, %L, 1, 'queued')$$,
    (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_empty'),
    (select v from ids where k = 'ind')),
  '42501', null, '65. clients cannot write runs directly');
reset role;
select has_column('public', 'usage_ledger', 'predicate_run_id', '66. usage_ledger records the run it paid for');

select * from finish();
rollback;
