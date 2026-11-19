begin;
create extension if not exists pgtap with schema extensions;
select plan(115);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A = owner (Admin), J = Editor, K = Viewer, L = a second Viewer, X = signed in but not a member.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000ce01', 'a@vf.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ce02', 'j@vf.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ce03', 'k@vf.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ce04', 'l@vf.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ce05', 'x@vf.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce01","role":"authenticated"}', true);
select public.create_organisation('VF Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'VF Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'VF Alpha';

insert into public.member (id, org_id, user_id, email)
select ('00000000-0000-4000-8000-00000000ce0' || n)::uuid, (select v from ids where k = 'org'),
       ('00000000-0000-4000-8000-00000000ce0' || n)::uuid, 'm' || n || '@vf.test'
  from generate_series(2, 4) n;
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ce02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ce03', (select v from ids where k = 'ws'), 'Viewer'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ce04', (select v from ids where k = 'ws'), 'Viewer');

-- Columns.
insert into public.predicate (key, org_id, workspace_id, label, value_type, allowed_values)
select k, (select v from ids where k = 'org'), (select v from ids where k = 'ws'), k, t, a::jsonb
  from (values ('drone', 'boolean', null), ('sites', 'number', null),
               ('industry', 'enum', '["energy", "mining", "utilities"]'),
               ('contact_name', 'text', null), ('founded_on', 'date', null)) v(k, t, a);
insert into ids select 'p_' || key, id from public.predicate where workspace_id = (select v from ids where k = 'ws');

-- Briefs: B1 Active, B2 Pilot, B3 Draft (no version).
insert into ids values ('b1', gen_random_uuid()), ('b2', gen_random_uuid()), ('b3', gen_random_uuid()),
                       ('bv1', gen_random_uuid()), ('bv2', gen_random_uuid());
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
select (select v from ids where k = b), (select v from ids where k = 'org'), (select v from ids where k = 'ws'), n, 'Draft', 'done'
  from (values ('b1', 'VF One'), ('b2', 'VF Two'), ('b3', 'VF Three')) v(b, n);
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
select (select v from ids where k = bv), (select v from ids where k = b), (select v from ids where k = 'org'),
       (select v from ids where k = 'ws'), 1, '{"offer":"views suite"}', '{}', '{}'
  from (values ('bv1', 'b1'), ('bv2', 'b2')) v(bv, b);
update public.brief set state = 'Active', current_version_id = (select v from ids where k = 'bv1') where id = (select v from ids where k = 'b1');
update public.brief set state = 'Pilot', current_version_id = (select v from ids where k = 'bv2') where id = (select v from ids where k = 'b2');

-- Four accounts: A, B, C, D (nm holds the short names the assertions use).
create temp table nm (id uuid primary key, name text) on commit drop;
grant all on nm to authenticated;
insert into ids select 'e_' || s, app.create_entity((select v from ids where k = 'org'), 'company', n, 'tenant')
  from (values ('a', 'Alpha Energy Ltd'), ('b', 'Beta Mining'), ('c', 'Gamma Utilities'), ('d', 'Delta Co')) v(s, n);
insert into nm select v, upper(substr(k, 3)) from ids where k like 'e\_%';
select set_config('app.writer', 'derivation', true);
insert into public.account (org_id, workspace_id, entity_id, stage, created_at)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_' || s), st::public.stage,
       now() - make_interval(days => d)
  from (values ('a', 'Engaged', 1), ('b', 'Untouched', 0), ('c', 'Contacted', 40), ('d', 'Untouched', 400)) v(s, st, d);
insert into ids select 'acc_' || lower(n.name), a.id from public.account a join nm n on n.id = a.entity_id;

-- Verdicts: under B1 A Confirmed, B Likely, C Unverified; under B2 B Confirmed, D Contradicted.
insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = bv),
       (select v from ids where k = 'e_' || s), val::public.verdict_value,
       case when val = 'Confirmed' then array[gen_random_uuid()] else '{}' end
  from (values ('bv1', 'a', 'Confirmed'), ('bv1', 'b', 'Likely'), ('bv1', 'c', 'Unverified'),
               ('bv2', 'b', 'Confirmed'), ('bv2', 'd', 'Contradicted')) v(bv, s, val);
-- Scores: under B1 A High 90, B Medium 60, C Low 30; under B2 D Excluded 5.
insert into public.score (org_id, workspace_id, account_id, brief_version_id, priority, band)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'acc_' || s),
       (select v from ids where k = bv), p, b::public.band
  from (values ('bv1', 'a', 90, 'High'), ('bv1', 'b', 60, 'Medium'), ('bv1', 'c', 30, 'Low'),
               ('bv2', 'd', 5, 'Excluded')) v(bv, s, p, b);
-- Labels.
insert into public.label (org_id, workspace_id, account_id, brief_version_id, kind, value)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'acc_' || s),
       (select v from ids where k = 'bv1'), kind, val
  from (values ('a', 'radar', 'news'), ('b', 'radar', 'jobs'), ('c', 'vertical', 'utilities')) v(s, kind, val);
select set_config('app.writer', '', true);

-- Source, domains and claims.
insert into public.extractor_version (capability, prompt_version, model, parser_version) values ('extract.vf24', '1', 'test', '1');
insert into public.source_record (org_id, workspace_id, connector_id, source_type, fetched_at, content_hash, extraction_status)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'radar:x', 'news', now(), 'vf24-1', 'extracted');
insert into ids select 'src', id from public.source_record where content_hash = 'vf24-1';
insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
select (select v from ids where k = 'org'), (select v from ids where k = 'e_' || s), 'domain', d, 90, (select v from ids where k = 'src')
  from (values ('a', 'alpha.example'), ('b', 'beta.example')) v(s, d);

select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e_' || s),
                        (select v from ids where k = 'p_' || p), val::jsonb, 'q', null, null,
                        (select v from ids where k = 'src'), now() - make_interval(days => age),
                        (select id from public.extractor_version where capability = 'extract.vf24'), 80::smallint)
  from (values ('a', 'drone', 'true', 0), ('a', 'sites', '12', 0), ('a', 'industry', '"energy"', 0),
               ('a', 'contact_name', '"O''Brien"', 0), ('a', 'founded_on', '"2020-01-15"', 0),
               ('b', 'drone', 'false', 0), ('b', 'sites', '30', 5), ('b', 'sites', '3', 1),
               ('b', 'industry', '"mining"', 0),
               ('b', 'contact_name', '"x''); drop table claim; --"', 0), ('b', 'founded_on', '"2015-06-01"', 0),
               ('c', 'industry', '"utilities"', 0), ('c', 'contact_name', '"Obrien plain"', 0)) v(s, p, val, age);
create temp table snap (k text primary key, n bigint) on commit drop;
grant all on snap to authenticated;
insert into snap select 'claims', count(*) from public.claim;

-- Runs: A industry errored; C industry errored (over an active claim); B industry and B sites stale.
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at, inputs_hash, error)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e_' || s),
       (select v from ids where k = 'p_' || p), 1, st, now() - interval '1 hour', now() - interval '1 hour', h,
       case when st = 'error' then 'boom' end
  from (values ('a', 'industry', 'error', null), ('c', 'industry', 'error', null),
               ('b', 'industry', 'succeeded', 'old-hash'), ('b', 'sites', 'succeeded', 'old-hash')) v(s, p, st, h);

-- m(filter, brief): the short names a filter matches, sorted. pg(sort, limit, offset, brief): a page in order.
create function pg_temp.m(f jsonb, b uuid default null) returns text language sql as $$
  select coalesce(string_agg(n.name, ',' order by n.name), '')
    from public.view_entities((select v from ids where k = 'ws'), b, f, null, 500, 0) v join nm n on n.id = v.entity_id $$;
create function pg_temp.pg(s jsonb, l int default 500, o int default 0, b uuid default null) returns text language sql as $$
  select coalesce(string_agg(n.name, ',' order by x.ord), '')
    from public.view_entities((select v from ids where k = 'ws'), b, '{}', s, l, o) with ordinality x(entity_id, total, ord)
    join nm n on n.id = x.entity_id $$;
create function pg_temp.c(key text, op text, val jsonb default null) returns jsonb language sql as $$
  select jsonb_build_object('field', jsonb_build_object('column', key), 'op', op)
         || case when val is null then '{}'::jsonb else jsonb_build_object('value', val) end $$;
create function pg_temp.s(f text, op text, val jsonb default null) returns jsonb language sql as $$
  select jsonb_build_object('field', jsonb_build_object('system', f), 'op', op)
         || case when val is null then '{}'::jsonb else jsonb_build_object('value', val) end $$;

-- Everything below runs as the Viewer K unless it says otherwise.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce03","role":"authenticated"}', true);

-- ── Column ops ───────────────────────────────────────────────────────────────
select is(pg_temp.m(pg_temp.c('drone', 'is_true')), 'A', '1. boolean is_true');
select is(pg_temp.m(pg_temp.c('drone', 'is_false')), 'B', '2. boolean is_false');
select is(pg_temp.m(pg_temp.c('drone', 'eq', 'true')), 'A', '3. boolean eq');
select is(pg_temp.m(pg_temp.c('drone', 'neq', 'true')), 'B', '4. boolean neq needs a claim that differs');
select is(pg_temp.m(pg_temp.c('drone', 'one_of', '[false]')), 'B', '5. boolean one_of');
select is(pg_temp.m(pg_temp.c('sites', 'gt', '20')), 'B', '6. number gt: any claim matches (B has 30 and 3)');
select is(pg_temp.m(pg_temp.c('sites', 'lt', '5')), 'B', '7. number lt: any claim matches (B has 30 and 3)');
select is(pg_temp.m(pg_temp.c('sites', 'gte', '12')), 'A,B', '8. number gte');
select is(pg_temp.m(pg_temp.c('sites', 'lte', '12')), 'A,B', '9. number lte');
select is(pg_temp.m(pg_temp.c('sites', 'eq', '3')), 'B', '10. number eq');
select is(pg_temp.m(pg_temp.c('sites', 'neq', '12')), 'B', '11. number neq');
select is(pg_temp.m(pg_temp.c('sites', 'one_of', '[12, 99]')), 'A', '12. number one_of');
select is(pg_temp.m(pg_temp.c('industry', 'eq', '"energy"')), 'A', '13. enum eq');
select is(pg_temp.m(pg_temp.c('industry', 'neq', '"energy"')), 'B,C', '14. enum neq');
select is(pg_temp.m(pg_temp.c('industry', 'one_of', '["mining", "utilities"]')), 'B,C', '15. enum one_of');
select is(pg_temp.m(pg_temp.c('industry', 'contains', '"UTIL"')), 'C', '16. enum contains is case-insensitive');
select is(pg_temp.m(pg_temp.c('contact_name', 'contains', '"brien"')), 'A,C', '17. text contains');
select is(pg_temp.m(pg_temp.c('contact_name', 'eq', '"Obrien plain"')), 'C', '18. text eq');
select is(pg_temp.m(pg_temp.c('contact_name', 'one_of', '["Obrien plain", "nobody"]')), 'C', '19. text one_of');
select is(pg_temp.m(pg_temp.c('founded_on', 'gt', '"2018-01-01"')), 'A', '20. date gt');
select is(pg_temp.m(pg_temp.c('founded_on', 'lte', '"2015-06-01"')), 'B', '21. date lte');
select is(pg_temp.m(pg_temp.c('founded_on', 'eq', '"2020-01-15"')), 'A', '22. date eq');
select is(pg_temp.m(pg_temp.c('founded_on', 'lt', '"2030-01-01"')), 'A,B', '23. date lt');
select is(pg_temp.m(pg_temp.c('drone', 'is_empty')), 'C,D', '24. is_empty holds when no active claim exists');
select is(pg_temp.m(pg_temp.c('drone', 'is_not_empty')), 'A,B', '25. is_not_empty');
select is(pg_temp.m(pg_temp.c('founded_on', 'is_empty')), 'C,D', '26. date is_empty');

-- ── Verdict and band ─────────────────────────────────────────────────────────
select is(pg_temp.m(pg_temp.s('verdict', 'one_of', '["Confirmed"]'), (select v from ids where k = 'b1')), 'A',
  '27. verdict under a given Brief');
select is(pg_temp.m(pg_temp.s('verdict', 'one_of', '["Confirmed"]')), 'A,B',
  '28. verdict with no Brief matches under any Pilot or Active Brief');
select is(pg_temp.m(pg_temp.s('verdict', 'one_of', '["Contradicted"]'), (select v from ids where k = 'b1')), '',
  '29. a verdict under another Brief does not match');
select is(pg_temp.m(pg_temp.s('verdict', 'one_of', '["Contradicted", "Unverified"]')), 'C,D', '30. verdict one_of, no Brief');
select is(pg_temp.m(pg_temp.s('verdict', 'one_of', '["Confirmed"]'), (select v from ids where k = 'b3')), '',
  '31. a Brief with no version matches no verdict');
select is(pg_temp.m(pg_temp.s('band', 'one_of', '["High", "Medium"]'), (select v from ids where k = 'b1')), 'A,B',
  '32. band under a given Brief');
select is(pg_temp.m(pg_temp.s('band', 'one_of', '["Excluded"]')), 'D', '33. band with no Brief');
select is(pg_temp.m(pg_temp.s('band', 'one_of', '["Excluded"]'), (select v from ids where k = 'b1')), '',
  '34. band under another Brief does not match');

-- ── Other system fields ──────────────────────────────────────────────────────
select is(pg_temp.m(pg_temp.s('stage', 'one_of', '["Engaged", "Contacted"]')), 'A,C', '35. stage one_of');
select is(pg_temp.m('{"field":{"system":"label","kind":"radar"},"op":"one_of","value":["news"]}'), 'A', '36. label kind and value');
select is(pg_temp.m('{"field":{"system":"label","kind":"radar"},"op":"one_of","value":["news","jobs"]}'), 'A,B', '37. label one_of');
select is(pg_temp.m('{"field":{"system":"label","kind":"vertical"},"op":"one_of","value":["news"]}'), '', '38. label kind must match');
select is(pg_temp.m(pg_temp.s('name', 'contains', '"MINING"')), 'B', '39. name contains, case-insensitive');
select is(pg_temp.m(pg_temp.s('name', 'eq', '"delta co"')), 'D', '40. name eq, case-insensitive');
select is(pg_temp.m(pg_temp.s('name', 'is_not_empty')), 'A,B,C,D', '41. name is_not_empty');
select is(pg_temp.m(pg_temp.s('domain', 'eq', '"alpha.example"')), 'A', '42. domain eq');
select is(pg_temp.m(pg_temp.s('domain', 'contains', '".example"')), 'A,B', '43. domain contains');
select is(pg_temp.m(pg_temp.s('domain', 'is_empty')), 'C,D', '44. domain is_empty');
select is(pg_temp.m(pg_temp.s('domain', 'is_not_empty')), 'A,B', '45. domain is_not_empty');
select is(pg_temp.m(pg_temp.s('created_at', 'within_days', '30')), 'A,B', '46. created_at within_days');
select is(pg_temp.m(pg_temp.s('created_at', 'older_than_days', '30')), 'C,D', '47. created_at older_than_days');

-- ── Run state ────────────────────────────────────────────────────────────────
select is(pg_temp.m('{"field":{"run":"industry"},"op":"one_of","value":["error","stale"]}'), 'A,B,C',
  '48. run one_of [error, stale] matches exactly the seeded cells');
select is(pg_temp.m('{"field":{"run":"sites"},"op":"one_of","value":["error","stale"]}'), 'B', '49. run state per column');
select is(pg_temp.m('{"field":{"run":"sites"},"op":"one_of","value":["empty"]}'), 'C,D', '50. run state empty');
select is(pg_temp.m('{"field":{"run":"drone"},"op":"one_of","value":["value"]}'), 'A,B', '51. run state value');

-- ── Groups ───────────────────────────────────────────────────────────────────
select is(pg_temp.m('{}'), 'A,B,C,D', '52. {} matches all four accounts');
select is(pg_temp.m(null), 'A,B,C,D', '53. a null filter matches all four accounts');
select is(pg_temp.m(jsonb_build_object('all', jsonb_build_array(pg_temp.c('drone', 'is_true'),
                                                                pg_temp.s('stage', 'one_of', '["Engaged"]')))),
  'A', '54. all');
select is(pg_temp.m(jsonb_build_object('any', jsonb_build_array(pg_temp.c('drone', 'is_true'),
                                                                pg_temp.c('industry', 'eq', '"mining"')))),
  'A,B', '55. any');
select is(pg_temp.m(jsonb_build_object('not', pg_temp.c('drone', 'is_empty'))), 'A,B', '56. not');
select is(pg_temp.m(jsonb_build_object('any', jsonb_build_array(
            jsonb_build_object('all', jsonb_build_array(pg_temp.c('sites', 'gte', '3'),
                                                        jsonb_build_object('not', pg_temp.c('industry', 'eq', '"mining"')))),
            pg_temp.s('name', 'contains', '"delta"')))),
  'A,D', '57. nested any / all / not');
select is(pg_temp.m(jsonb_build_object('not', pg_temp.c('sites', 'neq', '12'))), 'A,C,D',
  '58. not over a column condition includes accounts with no claim');
select is(pg_temp.m('{"all":[]}'), 'A,B,C,D', '59. an empty all matches everything');
select is(pg_temp.m('{"any":[]}'), '', '60. an empty any matches nothing');

-- ── Review Focus 3: quotes and SQL in values ─────────────────────────────────
select is(pg_temp.m(pg_temp.c('contact_name', 'eq', '"O''Brien"')), 'A', '61. O''Brien matches literally');
select is(pg_temp.m(pg_temp.c('contact_name', 'eq', '"x''); drop table claim; --"')), 'B',
  '62. a value holding SQL matches literally');
select is(pg_temp.m(pg_temp.s('name', 'contains', '"x''); drop table claim; --"')), '',
  '63. SQL in a name filter matches nothing and breaks nothing');
select is(pg_temp.m('{"field":{"system":"label","kind":"r''); drop table claim; --"},"op":"one_of","value":["n''--"]}'), '',
  '64. SQL in a label kind and value matches nothing');
select is(pg_temp.m(pg_temp.s('domain', 'contains', '"%"')), '', '65. % in contains is literal, not a wildcard');
select is(pg_temp.m(pg_temp.s('name', 'contains', to_jsonb('$1 %s $$ \'' -- '::text))), '',
  '65b. parameter markers, format codes, dollar quotes and backslashes in a value are literal');
reset role;
select has_table('public', 'claim', '66. the claim table is still there');
select is((select count(*) from public.claim), (select n from snap where k = 'claims'), '67. no claim was touched');
select ok(strpos(app.filter_where((select v from ids where k = 'ws'), null, pg_temp.c('contact_name', 'eq', '"O''Brien"')),
                 '''"O''''Brien"''') > 0,
  '68. the compiled SQL carries the value as a quoted literal');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce03","role":"authenticated"}', true);

-- ── Errors: 22023 with the JSON path ─────────────────────────────────────────
select throws_ok(format('select pg_temp.m(%L::jsonb)',
    '{"all":[{"field":{"system":"name"},"op":"is_not_empty"},{"field":{"column":"work_emial"},"op":"is_empty"}]}'),
  '22023', 'filter.all[1].field: unknown column "work_emial"', '69. an unknown column names its path');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('sites', 'between', '1')),
  '22023', 'filter.op: unknown op "between"', '70. an unknown op');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('sites', 'is_true')),
  '22023', 'filter.op: is_true does not apply to number column "sites"', '71. an op that does not fit the column type');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('industry', 'one_of', '"energy"')),
  '22023', 'filter.value: one_of needs a list of values', '72. one_of with a non-list');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('industry', 'eq', '"farming"')),
  '22023', 'filter.value: "farming" is not a value of column "industry"', '73. an enum value outside the allowed values');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('sites', 'gt', '"many"')),
  '22023', 'filter.value: gt needs a number', '74. a number op with a non-number');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.s('verdict', 'one_of', '["Maybe"]')),
  '22023', 'filter.value[0]: "Maybe" is not a verdict', '75. an unknown verdict');
select throws_ok(format('select pg_temp.m(%L::jsonb)', '{"field":{"run":"sites"},"op":"one_of","value":["broken"]}'),
  '22023', 'filter.value[0]: "broken" is not a cell state', '76. an unknown cell state');
select throws_ok(format('select pg_temp.m(%L::jsonb)', '{"field":{"run":"nope"},"op":"one_of","value":["error"]}'),
  '22023', 'filter.field: unknown column "nope"', '77. a run filter on an unknown column');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.s('size', 'eq', '1')),
  '22023', 'filter.field: unknown system field "size"', '78. an unknown system field');
select throws_ok(format('select pg_temp.m(%L::jsonb)', '{"not":{"any":[{"bogus":1}]}}'),
  '22023', 'filter.not.any[0]: unknown key "bogus"', '79. an unknown key names its path');
select throws_ok(format('select pg_temp.m(%L::jsonb)', pg_temp.c('drone', 'is_empty', '"x"')),
  '22023', 'filter.value: is_empty takes no value', '80. a value on an op that takes none');
select throws_ok(format('select pg_temp.m(%L::jsonb)',
    jsonb_build_object('all', (select jsonb_agg(pg_temp.c('drone', 'is_true')) from generate_series(1, 51)))),
  '22023', 'filter.all[50]: a filter has at most 50 conditions', '81. 51 conditions are refused');
select lives_ok(format('select pg_temp.m(%L::jsonb)',
    jsonb_build_object('all', (select jsonb_agg(pg_temp.c('drone', 'is_true')) from generate_series(1, 50)))),
  '82. 50 conditions are allowed');
select throws_ok(format('select pg_temp.m(%L::jsonb)',
    '{"not":{"not":{"not":{"not":{"not":{"not":{"not":{"not":{"field":{"column":"drone"},"op":"is_true"}}}}}}}}}'),
  '22023', 'filter.not.not.not.not.not.not.not.not: a filter nests at most 8 levels deep', '83. depth 9 is refused');
select is(pg_temp.m('{"not":{"not":{"not":{"not":{"not":{"not":{"not":{"field":{"column":"drone"},"op":"is_true"}}}}}}}}'),
  'B,C,D', '84. depth 8 is allowed');

-- ── Paging and sort ──────────────────────────────────────────────────────────
select is(pg_temp.pg('[{"system":"name","dir":"asc"}]', 2, 2), 'D,C', '85. limit 2 offset 2 returns the third and fourth by name');
select is((select array_agg(distinct total) from public.view_entities((select v from ids where k = 'ws'), null, '{}', null, 2, 2)),
  array[4::bigint], '86. total is 4 on every row of the page');
select is(pg_temp.pg(null), 'A,B,D,C', '87. the default sort is name asc');
select is(pg_temp.pg('[{"system":"name","dir":"desc"}]'), 'C,D,B,A', '88. name desc');
select is(pg_temp.pg('[{"system":"created_at","dir":"asc"}]'), 'D,C,A,B', '89. created_at asc');
select is(pg_temp.pg('[{"system":"created_at","dir":"desc"}]'), 'B,A,C,D', '90. created_at desc');
select is(pg_temp.pg('[{"system":"priority","dir":"desc"}]', 500, 0, (select v from ids where k = 'b1')), 'A,B,C,D',
  '91. priority desc under a Brief, no score last');
select is(pg_temp.pg('[{"system":"priority","dir":"asc"}]', 500, 0, (select v from ids where k = 'b1')), 'C,B,A,D',
  '92. priority asc under a Brief, no score last');
select is(pg_temp.pg('[{"system":"verdict","dir":"asc"}]', 500, 0, (select v from ids where k = 'b1')), 'A,B,C,D',
  '93. verdict asc: Confirmed first, no verdict last');
select is(pg_temp.pg('[{"system":"verdict","dir":"desc"}]', 500, 0, (select v from ids where k = 'b1')), 'C,B,A,D',
  '94. verdict desc, no verdict last');
select is(pg_temp.pg('[{"column":"sites","dir":"desc"}]'), 'A,B,D,C', '95. column desc by the latest claim (B latest is 3)');
select is(pg_temp.pg('[{"column":"sites","dir":"asc"}]'), 'B,A,D,C', '96. column asc, empty last, then name');
select is(pg_temp.pg('[{"column":"industry","dir":"desc"},{"system":"name","dir":"desc"}]'), 'C,B,A,D', '97. column desc then name');
select throws_ok(format('select pg_temp.pg(%L::jsonb)', '[{"system":"size","dir":"asc"}]'),
  '22023', 'sort[0].system: unknown sort "size"', '98. an unknown system sort');
select throws_ok(format('select pg_temp.pg(%L::jsonb)', '[{"column":"sites","dir":"up"}]'),
  '22023', 'sort[0].dir: must be asc or desc', '99. an unknown direction');
select throws_ok(format('select * from public.view_entities(%L::uuid, null, %L::jsonb, null, 501, 0)',
    (select v from ids where k = 'ws'), '{}'),
  '22023', 'a page has at most 500 rows', '100. a page larger than 500 is refused');

-- ── Person views ─────────────────────────────────────────────────────────────
select throws_ok(format('select * from public.view_entities(%L::uuid, null, %L::jsonb, null, 10, 0, %L)',
    (select v from ids where k = 'ws'), '{}', 'person'),
  '22023', 'person views come with People (P6)', '101. a person view returns the P6 message');

-- ── Access ───────────────────────────────────────────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce05","role":"authenticated"}', true);
select throws_ok(format('select * from public.view_entities(%L::uuid, null, %L::jsonb, null, 10, 0)',
    (select v from ids where k = 'ws'), '{}'),
  '42501', null, '102. a non-member cannot view');

-- ── Saved views ──────────────────────────────────────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce03","role":"authenticated"}', true);
select throws_ok(format($$select public.save_view(p_ws => %L::uuid, p_name => 'Shared', p_visibility => 'workspace')$$,
    (select v from ids where k = 'ws')),
  '42501', null, '103. a Viewer cannot save a workspace view');
insert into ids select 'v_personal', public.save_view(p_ws => (select v from ids where k = 'ws'), p_name => 'Mine',
  p_columns => '[{"kind":"predicate","ref":"sites","width":120,"pinned":true},{"kind":"system","ref":"name"}]',
  p_filter => pg_temp.c('drone', 'is_true'), p_sort => '[{"column":"sites","dir":"desc"}]', p_visibility => 'personal');
select throws_ok(format($$select public.save_view(p_ws => %L::uuid, p_name => 'Bad', p_visibility => 'personal', p_filter => %L::jsonb)$$,
    (select v from ids where k = 'ws'), '{"all":[{"field":{"column":"work_emial"},"op":"is_empty"}]}'),
  '22023', 'filter.all[0].field: unknown column "work_emial"', '104. save_view validates the filter by compiling it');
select throws_ok(format($$select public.save_view(p_ws => %L::uuid, p_name => 'Bad', p_visibility => 'personal', p_sort => %L::jsonb)$$,
    (select v from ids where k = 'ws'), '[{"system":"size"}]'),
  '22023', 'sort[0].system: unknown sort "size"', '105. save_view validates the sort');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce02","role":"authenticated"}', true);
insert into ids select 'v_shared', public.save_view(p_ws => (select v from ids where k = 'ws'), p_name => 'Team board',
  p_brief => (select v from ids where k = 'b1'), p_layout => 'board', p_visibility => 'workspace');
insert into ids select 'v_people', public.save_view(p_ws => (select v from ids where k = 'ws'), p_name => 'People',
  p_visibility => 'workspace', p_entity_type => 'person');
select is((select entity_type::text from public.saved_view where id = (select v from ids where k = 'v_people')), 'person',
  '106. a person view is stored');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce04","role":"authenticated"}', true);
select is((select count(*)::int from public.saved_view where id = (select v from ids where k = 'v_personal')), 0,
  '107. a personal view is invisible to another member');
select is((select count(*)::int from public.list_views((select v from ids where k = 'ws')) where id = (select v from ids where k = 'v_personal')), 0,
  '108. list_views hides another member''s personal view');
select is((select string_agg(preset, ',' order by ordinality) from public.list_views((select v from ids where k = 'ws')) with ordinality
            where ordinality <= 5),
  'ready_to_contact,needs_evidence,stale,errored,by_verdict', '109. list_views returns the five presets first, in order');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce03","role":"authenticated"}', true);
select is((select string_agg(coalesce(preset, name), ',' order by ordinality) from public.list_views((select v from ids where k = 'ws')) with ordinality),
  'ready_to_contact,needs_evidence,stale,errored,by_verdict,Mine,People,Team board',
  '110. list_views: presets, then the saved views the caller can see');
select is((select string_agg(n.name, ',' order by n.name)
             from public.view_entities((select v from ids where k = 'ws'), (select v from ids where k = 'b1'),
                    (select filter from public.list_views((select v from ids where k = 'ws'), (select v from ids where k = 'b1')) where preset = 'ready_to_contact'),
                    null, 500, 0) v join nm n on n.id = v.entity_id)
          || '|' ||
          (select string_agg(n.name, ',' order by n.name)
             from public.view_entities((select v from ids where k = 'ws'), null,
                    (select filter from public.list_views((select v from ids where k = 'ws')) where preset = 'errored'),
                    null, 500, 0) v join nm n on n.id = v.entity_id)
          || '|' ||
          (select string_agg(n.name, ',' order by n.name)
             from public.view_entities((select v from ids where k = 'ws'), null,
                    (select filter from public.list_views((select v from ids where k = 'ws')) where preset = 'stale'),
                    null, 500, 0) v join nm n on n.id = v.entity_id),
  'A,B|A,C|B', '111. presets ready_to_contact (B1), errored and stale match the seeded accounts');

-- delete_view: not by another non-Admin; by the creator; by an Admin.
select throws_ok(format('select public.delete_view(%L::uuid)', (select v from ids where k = 'v_shared')),
  '42501', null, '112a. a member who did not create a view cannot delete it');
select public.delete_view((select v from ids where k = 'v_personal'));
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ce01","role":"authenticated"}', true);
select public.delete_view((select v from ids where k = 'v_shared'));
select throws_ok($$insert into public.saved_view (org_id, workspace_id, name) values (gen_random_uuid(), gen_random_uuid(), 'x')$$,
  '42501', null, '112b. clients cannot write saved_view directly');
reset role;
select is((select string_agg(name, ',' order by name) from public.saved_view), 'People',
  '113. the creator and an Admin deleted their views');

select * from finish();
rollback;
