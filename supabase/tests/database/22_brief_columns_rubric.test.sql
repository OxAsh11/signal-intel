begin;
create extension if not exists pgtap with schema extensions;
select plan(53);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A = owner, J = Editor, K = Viewer.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000cc01', 'a@cr.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cc02', 'j@cr.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cc03', 'k@cr.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc01","role":"authenticated"}', true);
select public.create_organisation('CR Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'CR Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'CR Alpha';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000cc02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cc02', 'j@cr.test'),
  ('00000000-0000-4000-8000-00000000cc03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cc03', 'k@cr.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cc02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000cc03', (select v from ids where k = 'ws'), 'Viewer');

create temp table cfg (k text primary key, v jsonb) on commit drop;
grant all on cfg to authenticated;
-- The FlytBase sample (copy in packages/signal_core/tests/fixtures/flytbase_columns.json).
insert into cfg values ('cols', '{"predicates": [
   {"key": "drone_programme", "label": "Runs or plans a drone programme", "value_type": "boolean",
    "allowed_values": null, "extraction_hint": "Announced, tendered or hiring for drone operations",
    "decay_half_life_days": 365, "role": "signal"},
   {"key": "industry", "label": "Industry", "value_type": "enum",
    "allowed_values": ["energy", "utilities", "public safety", "mining", "drone vendor"],
    "extraction_hint": "", "decay_half_life_days": null, "role": "fit.vertical"},
   {"key": "site_count", "label": "Industrial sites", "value_type": "number", "allowed_values": null,
    "extraction_hint": "Plants, substations or sites operated", "decay_half_life_days": 730, "role": "scale"}],
 "evidence_rules": [
   {"predicate": "drone_programme", "kind": "requires", "op": "is_true", "value": null,
    "min_sources": 1, "max_age_days": 730},
   {"predicate": "industry", "kind": "disqualifies", "op": "one_of", "value": ["drone vendor"],
    "min_sources": 1, "max_age_days": null}]}');
insert into cfg values ('base', '{"offer":"Drone docks","hypothesis":"Runs a drone programme",
  "examples":{"positive":["P1","P2","P3"],"negative":["N1","N2"]},
  "sources":{"radar_types":["news_publications"]}}');
insert into cfg select 'full', (select v from cfg where k = 'base') || (select v from cfg where k = 'cols');

-- ── validate_brief_columns ───────────────────────────────────────────────────
select lives_ok(format('select app.validate_brief_columns(%L::jsonb)', (select v from cfg where k = 'cols')),
  '1. the FlytBase sample is valid');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,0,key}', '"Drone"')),
  '22023', null, '2. a key with capitals is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates}',
      (select v -> 'predicates' || jsonb_build_array(v -> 'predicates' -> 2) from cfg where k = 'cols'))),
  '22023', null, '3. a duplicate key is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,1,allowed_values}', '["energy"]')),
  '22023', 'column "industry": enum needs 2–12 allowed values', '4. an enum with one value is refused, naming the column');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{predicates,0,role}', '"fit.bogus"')),
  '22023', null, '5. an unknown role is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_build_object('predicates', (select jsonb_agg(jsonb_build_object(
      'key', 'col_' || n, 'label', 'Column ' || n, 'value_type', 'text', 'allowed_values', null,
      'extraction_hint', '', 'decay_half_life_days', null, 'role', 'info')) from generate_series(1, 13) n))),
  '22023', null, '6. 13 columns are refused');
select lives_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_build_object('predicates', (select jsonb_agg(jsonb_build_object(
      'key', 'col_' || n, 'label', 'Column ' || n, 'value_type', 'text', 'allowed_values', null,
      'extraction_hint', '', 'decay_half_life_days', null, 'role', 'info')) from generate_series(1, 12) n))),
  '7. 12 columns are allowed');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{evidence_rules,0,predicate}', '"nope"')),
  '22023', null, '8. a rule on an unknown key is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{evidence_rules,0}',
      '{"predicate":"drone_programme","kind":"requires","op":"at_least","value":3,"min_sources":1,"max_age_days":null}')),
  '22023', null, '9. at_least on a boolean column is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{evidence_rules,1,value}', '["aerospace"]')),
  '22023', null, '10. one_of with a value outside allowed_values is refused');
select throws_ok(format('select app.validate_brief_columns(%L::jsonb)',
    jsonb_set((select v from cfg where k = 'cols'), '{evidence_rules,0,min_sources}', '6')),
  '22023', null, '11. min_sources 6 is refused');
select lives_ok($$select app.validate_brief_columns('{"offer":"x","hypothesis":"y"}'::jsonb)$$,
  '12. a config without predicates or evidence_rules is valid');
select lives_ok($$select app.validate_brief_columns('{"predicates":["sites","vertical"]}'::jsonb)$$,
  '13. legacy plain key strings are valid');

-- ── save_brief_draft_ui validates ────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'a', public.create_brief((select v from ids where k = 'ws'), 'Brief A');
insert into ids select 'b', public.create_brief((select v from ids where k = 'ws'), 'Brief B');
insert into ids select 'c', public.create_brief((select v from ids where k = 'ws'), 'Brief C');
select public.save_brief_draft_ui((select v from ids where k = 'a'), (select v from cfg where k = 'full'));
select throws_ok(format('select public.save_brief_draft_ui(%L::uuid, %L::jsonb)', (select v from ids where k = 'a'),
    jsonb_set((select v from cfg where k = 'full'), '{evidence_rules,0,predicate}', '"nope"')),
  '22023', null, '14. save_brief_draft_ui refuses an invalid rule');
reset role;
select is((select draft_config from public.brief where id = (select v from ids where k = 'a')),
  (select v from cfg where k = 'full'), '15. the refused save leaves the draft unchanged');

-- ── start_pilot syncs predicates ─────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'a1', public.start_pilot((select v from ids where k = 'a'));
reset role;

select is((select count(*)::int from public.predicate where workspace_id = (select v from ids where k = 'ws')),
  3, '16. start_pilot creates one predicate per column');
select is((select jsonb_build_object('label', label, 'value_type', value_type, 'allowed_values', allowed_values,
                                     'decay', decay_half_life_days, 'hint', extraction_hint)
             from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'drone_programme'),
  '{"label":"Runs or plans a drone programme","value_type":"boolean","allowed_values":null,"decay":365,
    "hint":"Announced, tendered or hiring for drone operations"}'::jsonb,
  '17. a boolean column becomes a predicate with its spec');
select is((select jsonb_build_object('label', label, 'value_type', value_type, 'allowed_values', allowed_values,
                                     'decay', decay_half_life_days, 'hint', extraction_hint)
             from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'industry'),
  '{"label":"Industry","value_type":"enum","allowed_values":["energy","utilities","public safety","mining","drone vendor"],
    "decay":null,"hint":""}'::jsonb,
  '18. an enum column keeps its allowed values');
select is((select jsonb_build_object('label', label, 'value_type', value_type, 'decay', decay_half_life_days,
                                     'hint', extraction_hint)
             from public.predicate where workspace_id = (select v from ids where k = 'ws') and key = 'site_count'),
  '{"label":"Industrial sites","value_type":"number","decay":730,"hint":"Plants, substations or sites operated"}'::jsonb,
  '19. a number column keeps its decay and hint');
select is((select count(*)::int from public.audit_log where action = 'predicate.created'
             and workspace_id = (select v from ids where k = 'ws')),
  3, '20. each new predicate is audited');
select is((select count(*)::int from public.outbox where event = 'predicate.backfill_requested'
             and payload ->> 'brief_id' = (select v::text from ids where k = 'a')),
  1, '21. start_pilot emits one backfill event');
select is((select payload from public.outbox where event = 'predicate.backfill_requested'
             and payload ->> 'brief_id' = (select v::text from ids where k = 'a')),
  jsonb_build_object('brief_id', (select v from ids where k = 'a'), 'brief_version_id', (select v from ids where k = 'a1'),
                     'org_id', (select v from ids where k = 'org'), 'workspace_id', (select v from ids where k = 'ws'),
                     'predicate_keys', '["drone_programme","industry","site_count"]'::jsonb),
  '22. the backfill event carries all three keys');

-- ── Version 2 adds a column, version 3 adds none ─────────────────────────────
insert into cfg select 'full4', jsonb_set(v, '{predicates}', v -> 'predicates' || '[{"key":"hq_country","label":"HQ country",
  "value_type":"text","allowed_values":null,"extraction_hint":"","decay_half_life_days":null,"role":"info"}]'::jsonb)
  from cfg where k = 'full';

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'), (select v from cfg where k = 'full4'));
insert into ids select 'p2', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p2'), 'Narrow', true, '{}', 0, 0, false);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'a2', public.publish_brief((select v from ids where k = 'a'));
select public.save_brief_draft_ui((select v from ids where k = 'a'),
  (select v || '{"hypothesis":"Runs or tenders a drone programme"}' from cfg where k = 'full4'));
insert into ids select 'p3', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p3'), 'Redefine', true, '{}', 0, 0, false);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'a3', public.publish_brief((select v from ids where k = 'a'));
reset role;

select is((select payload -> 'predicate_keys' from public.outbox where event = 'predicate.backfill_requested'
             and payload ->> 'brief_version_id' = (select v::text from ids where k = 'a2')),
  '["hq_country"]'::jsonb, '23. version 2 backfills only the added column');
select is((select count(*)::int from public.outbox where event = 'predicate.backfill_requested'
             and payload ->> 'brief_version_id' = (select v::text from ids where k = 'a3')),
  0, '24. version 3 with the same columns emits no backfill');
select is((select count(*)::int from public.outbox where event = 'predicate.backfill_requested'
             and payload ->> 'brief_id' = (select v::text from ids where k = 'a')),
  2, '25. Brief A has two backfill events in all');
select is((select count(*)::int from public.audit_log where action = 'predicate.updated'
             and workspace_id = (select v from ids where k = 'ws')),
  0, '25b. republishing unchanged column specs updates no predicate');

-- ── A key that exists with another type ──────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'size', public.create_workspace_predicate((select v from ids where k = 'ws'), 'size', 'Size', 'text');
-- industry sorts before size, so its union would be written first and must roll back too.
insert into cfg select 'clash', (select v from cfg where k = 'base') || '{"predicates":[
  {"key":"size","label":"Employees","value_type":"number","allowed_values":null,"extraction_hint":"",
   "decay_half_life_days":null,"role":"fit.size"},
  {"key":"industry","label":"Industry","value_type":"enum","allowed_values":["energy","aerospace"],
   "extraction_hint":"","decay_half_life_days":null,"role":"fit.vertical"}]}'::jsonb;
select throws_ok(format('select public.save_brief_draft_ui(%L::uuid, %L::jsonb)', (select v from ids where k = 'b'),
    (select v from cfg where k = 'clash')),
  '23514', 'column "size" already exists in this workspace as text; give it another key',
  '25c. save_brief_draft_ui refuses a key the workspace holds as another type');
reset role;
select ok((select draft_config is null from public.brief where id = (select v from ids where k = 'b')),
  '25d. the refused save leaves the draft as it was (empty)');
-- A draft written before the check (or by another path) is still refused at publish.
select app.update_brief_draft((select v from ids where k = 'b'), (select v from cfg where k = 'clash'));

create temp table snap (k text primary key, v text) on commit drop;
grant all on snap to authenticated;
insert into snap select 'pred', md5(string_agg(p::text, ',' order by p.key))
  from public.predicate p where p.workspace_id = (select v from ids where k = 'ws');
insert into snap select 'outbox', count(*)::text from public.outbox;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
select throws_ok(format('select public.start_pilot(%L::uuid)', (select v from ids where k = 'b')),
  '23514', 'column "size" already exists in this workspace as text; give it another key',
  '26. a column whose key exists with another type is refused');
reset role;
select is((select count(*)::int from public.brief_version where brief_id = (select v from ids where k = 'b')),
  0, '27. the refused publish leaves no version row');
select is((select md5(string_agg(p::text, ',' order by p.key)) from public.predicate p
            where p.workspace_id = (select v from ids where k = 'ws')),
  (select v from snap where k = 'pred'), '28. the refused publish changes no predicate');
select is((select count(*)::text from public.outbox), (select v from snap where k = 'outbox'),
  '29. the refused publish leaves no outbox row');
select is((select state from public.brief where id = (select v from ids where k = 'b')),
  'Draft'::public.brief_state, '30. the refused Brief stays a Draft');

-- ── Same key, same type: enum values union, row kept ─────────────────────────
insert into ids select 'industry', id from public.predicate
 where workspace_id = (select v from ids where k = 'ws') and key = 'industry';
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
-- Its label, hint and decay differ (last publish wins); 365.0 and 730.0 are whole numbers too.
select public.save_brief_draft_ui((select v from ids where k = 'c'), (select v from cfg where k = 'base') || '{"predicates":[
  {"key":"industry","label":"Sector","value_type":"enum","allowed_values":["energy","aerospace"],
   "extraction_hint":"Sector of the operator","decay_half_life_days":365.0,"role":"fit.vertical"},
  {"key":"fleet_size","label":"Drones in the fleet","value_type":"number","allowed_values":null,
   "extraction_hint":"","decay_half_life_days":730.0,"role":"scale"}]}'::jsonb);
insert into ids select 'c1', public.start_pilot((select v from ids where k = 'c'));
reset role;
select is((select allowed_values from public.predicate where id = (select v from ids where k = 'industry')),
  '["energy","utilities","public safety","mining","drone vendor","aerospace"]'::jsonb,
  '31. an existing enum gains the new values');
select is((select count(*)::int from public.predicate
            where workspace_id = (select v from ids where k = 'ws') and key = 'industry'),
  1, '32. the existing predicate row is reused');
select is((select jsonb_build_object('label', label, 'hint', extraction_hint, 'decay', decay_half_life_days)
             from public.predicate where id = (select v from ids where k = 'industry')),
  '{"label":"Sector","hint":"Sector of the operator","decay":365}'::jsonb,
  '32b. the existing predicate takes the newer label, hint and decay');
select is((select jsonb_build_object('before', before, 'after', after - 'brief_version_id',
                                     'version', after ->> 'brief_version_id')
             from public.audit_log where action = 'predicate.updated'
              and target_id = (select v from ids where k = 'industry')),
  jsonb_build_object('before', '{"label":"Industry","extraction_hint":"","decay_half_life_days":null}'::jsonb,
                     'after', '{"label":"Sector","extraction_hint":"Sector of the operator","decay_half_life_days":365}'::jsonb,
                     'version', (select v::text from ids where k = 'c1')),
  '32c. the change is audited as predicate.updated');
select is((select decay_half_life_days from public.predicate
            where workspace_id = (select v from ids where k = 'ws') and key = 'fleet_size'),
  730, '32d. a new predicate takes a decay written as 730.0');

-- ── AI-named Briefs ──────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
insert into ids select 'n1', public.create_brief((select v from ids where k = 'ws'), 'New Brief 1', 'We sell drone docks', true);
insert into ids select 'n2', public.create_brief((select v from ids where k = 'ws'), 'New Brief 2', null, true);
insert into ids select 'n3', public.create_brief((select v from ids where k = 'ws'), 'New Brief 3', null, true);
reset role;
select ok((select name_auto from public.brief where id = (select v from ids where k = 'n1')),
  '33. create_brief stores name_auto');
select ok(not (select name_auto from public.brief where id = (select v from ids where k = 'a')),
  '34. name_auto defaults to false');
select is((select payload ->> 'description' from public.outbox where event = 'brief.intake_requested'
             and payload ->> 'brief_id' = (select v::text from ids where k = 'n1')),
  'We sell drone docks', '35. create_brief still requests the intake');

select is(app.apply_drafted_name((select v from ids where k = 'n1'), 'Drone programmes'),
  'Drone programmes', '36. apply_drafted_name returns the drafted name');
select is((select name || '|' || name_auto::text from public.brief where id = (select v from ids where k = 'n1')),
  'Drone programmes|false', '37. apply_drafted_name renames and clears the flag');
select is(app.apply_drafted_name((select v from ids where k = 'n2'), 'Drone programmes'),
  'Drone programmes 2', '38. a taken name gets " 2"');
select is(app.apply_drafted_name((select v from ids where k = 'a'), 'Something else'),
  'Brief A', '39. a Brief named by hand keeps its name');
select is((select name from public.brief where id = (select v from ids where k = 'a')),
  'Brief A', '40. the hand-named Brief is unchanged');

-- ── rename_brief ─────────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc03","role":"authenticated"}', true);
select throws_ok(format('select public.rename_brief(%L::uuid, %L)', (select v from ids where k = 'n3'), 'Viewer name'),
  '42501', null, '41. a Viewer cannot rename a Brief');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cc02","role":"authenticated"}', true);
select throws_ok(format('select public.rename_brief(%L::uuid, %L)', (select v from ids where k = 'n3'), '   '),
  '22023', null, '42. a blank name is refused');
select lives_ok(format('select public.rename_brief(%L::uuid, %L)', (select v from ids where k = 'n3'), 'Utility drone buyers'),
  '43. an Editor renames a Brief');
reset role;
select is((select name from public.brief where id = (select v from ids where k = 'n3')),
  'Utility drone buyers', '44. rename_brief sets the name');
select ok(not (select name_auto from public.brief where id = (select v from ids where k = 'n3')),
  '45. rename_brief clears name_auto');
select is((select count(*)::int from public.audit_log where action = 'brief.renamed'
             and target_id = (select v from ids where k = 'n3')),
  1, '46. rename_brief writes a brief.renamed audit row');
select is(app.apply_drafted_name((select v from ids where k = 'n3'), 'Late AI name'),
  'Utility drone buyers', '47. a late AI name does not overwrite a manual rename');

select * from finish();
rollback;
