begin;
create extension if not exists pgtap with schema extensions;
select plan(14);

-- Fixtures: one organisation with an Owner.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000b', 'b@beta.test',  'authenticated', 'authenticated');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select public.create_organisation('Alpha Engineering');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
select public.create_organisation('Beta Cold Storage');
reset role;

create temp table fx as
select (select id from public.organisation where name = 'Alpha Engineering') as org_a,
       (select w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Alpha Engineering') as ws_a;
insert into public.extractor_version (id, capability, prompt_version, model, parser_version)
  values ('00000000-0000-4000-9000-000000000001', 'extract', 'v1', 'test', 'v1');
insert into public.source_record (id, org_id, connector_id, source_type, fetched_at, content_hash)
  select '00000000-0000-4000-9000-000000000002', org_a, 'test', 'job_post', now(), 'hash-1' from fx;
insert into public.entity (id, org_id, entity_type, display_name)
  select '00000000-0000-4000-9000-000000000003', org_a, 'company', 'Van Dijk Engineering' from fx;
insert into public.predicate (id, key, label, value_type, allowed_values)
  values ('00000000-0000-4000-9000-000000000004', 'test.uses_tool', 'Uses tool', 'enum', '["Revit","ArchiCAD"]');

-- Canary
select is((select app.run_canary() ->> 'status'), 'ok', 'canary passes end to end');
select is((select app.run_canary() ->> 'new_entity'), 'false', 'canary second run reuses the resolved entity');

-- The canary is a derivation writer for the rest of its transaction (migration 0009). Clear the
-- flag so the guards below are tested from a transaction that is not one.
select set_config('app.writer', '', true);

-- Typed predicates (F3)
select lives_ok($$ insert into public.claim (org_id, entity_id, predicate_id, value, source_record_id, observed_at, extractor_version_id, confidence)
  select org_a, '00000000-0000-4000-9000-000000000003', '00000000-0000-4000-9000-000000000004', '"Revit"',
         '00000000-0000-4000-9000-000000000002', now(), '00000000-0000-4000-9000-000000000001', 80 from fx $$,
  'claim accepts a registered value');
select throws_ok($$ insert into public.claim (org_id, entity_id, predicate_id, value, source_record_id, observed_at, extractor_version_id, confidence)
  select org_a, '00000000-0000-4000-9000-000000000003', '00000000-0000-4000-9000-000000000004', '"AutoCAD"',
         '00000000-0000-4000-9000-000000000002', now(), '00000000-0000-4000-9000-000000000001', 80 from fx $$,
  '23514', null, 'claim rejects an out-of-registry value');

-- People are always tenant (F1)
select throws_ok($$ insert into public.entity (org_id, entity_type, display_name, scope) select org_a, 'person', 'Jane Doe', 'shared' from fx $$,
  '23514', null, 'person entity cannot be shared');

-- Provenance (F3, M2)
select throws_ok($$ insert into public.source_record (org_id, connector_id, source_type, fetched_at, content_hash)
  select org_a, 'test', 'job_post', now(), 'hash-1' from fx $$, '23505', null, 'source_record dedupes on content hash');
select throws_ok($$ update public.source_record set url = 'https://x.test' where id = '00000000-0000-4000-9000-000000000002' $$,
  '42501', null, 'source_record payload is immutable');
select throws_ok($$ delete from public.entity where id = '00000000-0000-4000-9000-000000000003' $$,
  '42501', null, 'entities are never deleted');

-- Derived tables (F7)
insert into public.brief (id, org_id, workspace_id, name) select '00000000-0000-4000-9000-000000000005', org_a, ws_a, 'CAD adopters NL' from fx;
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  select '00000000-0000-4000-9000-000000000006', '00000000-0000-4000-9000-000000000005', org_a, ws_a, 1, '{}', '{}', '{}' from fx;
select throws_ok($$ insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value)
  select org_a, ws_a, '00000000-0000-4000-9000-000000000006', '00000000-0000-4000-9000-000000000003', 'Likely' from fx $$,
  '42501', null, 'derived tables reject direct writes');
select throws_ok($$ update public.brief_version set config = '{"x":1}' where id = '00000000-0000-4000-9000-000000000006' $$,
  '42501', null, 'brief versions are immutable');
insert into public.account (id, org_id, workspace_id, entity_id) select '00000000-0000-4000-9000-000000000007', org_a, ws_a, '00000000-0000-4000-9000-000000000003' from fx;
select throws_ok($$ update public.account set stage = 'Engaged' where id = '00000000-0000-4000-9000-000000000007' $$,
  '42501', null, 'account.stage cannot be set by hand');

-- Isolation across the new tables
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
select is((select count(*)::int from public.entity), 0, 'B sees no entities of A or the canary');
select is((select count(*)::int from public.claim), 0, 'B sees no claims of A');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select is((select count(*)::int from public.claim), 1, 'A sees its own claim');

select * from finish();
rollback;
