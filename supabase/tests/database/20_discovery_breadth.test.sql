begin;
create extension if not exists pgtap with schema extensions;
select plan(42);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A = owner, J = Editor, K = Viewer.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000da01', 'a@db.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000da02', 'j@db.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000da03', 'k@db.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da01","role":"authenticated"}', true);
select public.create_organisation('DB Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'DB Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'DB Alpha';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000da02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000da02', 'j@db.test'),
  ('00000000-0000-4000-8000-00000000da03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000da03', 'k@db.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000da02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000da03', (select v from ids where k = 'ws'), 'Viewer');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
insert into ids select 'b', public.create_brief((select v from ids where k = 'ws'), 'Discovery B');
reset role;
select app.save_brief_draft((select v from ids where k = 'b'), '{"offer":"CAD","hypothesis":"Hotels opening",
  "examples":{"positive":["P1","P2","P3"],"negative":["N1","N2"]},
  "sources":{"radar_types":["news_publications","tenders_procurement","company_websites"]}}');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
insert into ids select 'v1', public.start_pilot((select v from ids where k = 'b'));
insert into ids select 'r', public.create_radar((select v from ids where k = 'ws'), (select v from ids where k = 'b'), 'directory', 'Dir', '{}');
reset role;

-- ── Schema ───────────────────────────────────────────────────────────────────
select has_column('public', 'radar', 'lexicon', '1. radar.lexicon');
select has_column('public', 'radar', 'lexicon_edited', '2. radar.lexicon_edited');
select has_column('public', 'radar', 'prefiltered_count', '3. radar.prefiltered_count');
select has_column('public', 'radar_query', 'yield_count', '4. radar_query.yield_count');
select has_column('public', 'radar_query', 'record_count', '5. radar_query.record_count');
select has_column('public', 'source_record', 'radar_query_id', '6. source_record.radar_query_id');
select has_column('public', 'source_record', 'content_kind', '7. source_record.content_kind');
select has_table('public', 'targeted_search', '8. targeted_search table');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
select lives_ok(format($$select public.create_radar(%L::uuid, %L::uuid, 'tender', 'T', '{"sites":["ted.europa.eu"]}')$$,
  (select v from ids where k = 'ws'), (select v from ids where k = 'b')), '9. tender is a radar type with valid sites');
reset role;
select throws_ok(format($$insert into public.radar (org_id, workspace_id, brief_id, connector_type, name)
  values (%L, %L, %L, 'foo', 'F')$$, (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'b')),
  '23514', null, '10. unknown connector types are rejected');
-- clean the tender radar made by test 9 so provisioning starts empty for tenders
delete from public.radar where connector_type = 'tender';

-- ── create_radar validates sites ─────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
select throws_ok(format($$select public.create_radar(%L::uuid, %L::uuid, 'event', 'E', '{"sites":["not a host"]}')$$,
  (select v from ids where k = 'ws'), (select v from ids where k = 'b')), '22023', null, '11. bad site hosts are rejected');
select throws_ok(format($$select public.create_radar(%L::uuid, %L::uuid, 'event', 'E', '{"sites":"x.com"}')$$,
  (select v from ids where k = 'ws'), (select v from ids where k = 'b')), '22023', null, '12. sites must be an array');
reset role;

-- ── Query upsert keeps history ───────────────────────────────────────────────
insert into public.radar_query (org_id, radar_id, query_text, source)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'r'), 'q1', 'hypothesis'),
         ((select v from ids where k = 'org'), (select v from ids where k = 'r'), 'u1', 'user'),
         ((select v from ids where k = 'org'), (select v from ids where k = 'r'), 'old', 'icp');
insert into ids select 'q1', id from public.radar_query where query_text = 'q1';
update public.radar_query set record_count = 3 where query_text = 'q1';
select app.upsert_radar_queries((select v from ids where k = 'r'),
  '[{"query_text":"q1"},{"query_text":"q2","source":"icp"}]', '{a,b}');
select is((select record_count from public.radar_query where id = (select v from ids where k = 'q1')), 3,
  '13. an unchanged query keeps its id and counts');
select ok(exists (select 1 from public.radar_query where radar_id = (select v from ids where k = 'r') and query_text = 'q2'),
  '14. a new query is added');
select ok(exists (select 1 from public.radar_query where radar_id = (select v from ids where k = 'r') and query_text = 'u1'),
  '15. user queries are kept');
select ok(not exists (select 1 from public.radar_query where radar_id = (select v from ids where k = 'r') and query_text = 'old'),
  '16. a dropped generated query is removed');
select is((select lexicon from public.radar where id = (select v from ids where k = 'r')), '{a,b}'::text[],
  '17. the lexicon is stored');
update public.radar set lexicon_edited = true where id = (select v from ids where k = 'r');
select app.upsert_radar_queries((select v from ids where k = 'r'), '[{"query_text":"q1"}]', '{c}');
select is((select lexicon from public.radar where id = (select v from ids where k = 'r')), '{a,b}'::text[],
  '18. a user-edited lexicon is not overwritten');
update public.radar set lexicon_edited = false where id = (select v from ids where k = 'r');
select app.upsert_radar_queries((select v from ids where k = 'r'),
  '[{"query_text":"q1"},{"query_text":"q2"}]', '{a,b}');

-- ── Editor RPCs ──────────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da03","role":"authenticated"}', true);
select throws_ok(format('select public.add_radar_query(%L::uuid, %L)', (select v from ids where k = 'r'), 'x'),
  '42501', null, '19. a Viewer cannot add a query');
select throws_ok(format('select public.update_radar_lexicon(%L::uuid, %L::text[])', (select v from ids where k = 'r'), '{x}'),
  '42501', null, '20. a Viewer cannot edit the lexicon');
select throws_ok(format('select public.remove_radar_query(%L::uuid)', (select v from ids where k = 'q1')),
  '42501', null, '21. a Viewer cannot remove a query');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
insert into ids select 'uq', public.add_radar_query((select v from ids where k = 'r'), '  hotel tender  ');
select public.update_radar_lexicon((select v from ids where k = 'r'), '{" x ", "y", "x", ""}');
reset role;
select is((select source || ':' || query_text from public.radar_query where id = (select v from ids where k = 'uq')),
  'user:hotel tender', '22. add_radar_query stores a trimmed user query');
select is((select lexicon from public.radar where id = (select v from ids where k = 'r')), '{x,y}'::text[],
  '23. the lexicon is trimmed and deduplicated');
select ok((select lexicon_edited from public.radar where id = (select v from ids where k = 'r')),
  '24. editing marks the lexicon as user-edited');
select ok(exists (select 1 from public.audit_log where action = 'radar.lexicon_updated'
  and target_id = (select v from ids where k = 'r')), '25. lexicon edits are audited');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
select public.remove_radar_query((select v from ids where k = 'uq'));
reset role;
select ok(not exists (select 1 from public.radar_query where id = (select v from ids where k = 'uq')),
  '26. remove_radar_query deletes the query');

-- ── Mapping and provisioning ─────────────────────────────────────────────────
select is(app.brief_source_connector('tenders_procurement'), 'tender', '27. tenders map to tender');
select is(app.brief_source_connector('public_social'), 'social_post', '28. public social maps to social_post');
select is(app.brief_source_connector('company_websites'), 'website', '29. websites map to website');
select is(app.brief_source_connector('nope'), null, '30. unknown sources map to nothing');
select is(app.provision_radars((select v from ids where k = 'b')), 2, '31. news and tender radars are created; websites need setup');
select is(app.provision_radars((select v from ids where k = 'b')), 0, '32. provisioning twice creates nothing');
update public.radar set status = 'paused' where brief_id = (select v from ids where k = 'b') and connector_type = 'tender';
select is(app.provision_radars((select v from ids where k = 'b')), 0, '33. a removed radar is not recreated');

-- ── Attribution and yield ────────────────────────────────────────────────────
insert into public.predicate (key, label, value_type) values ('opening.d20', 'Opening D20', 'text');
insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('extract.d20test', '0.1.0', 'claude-test', '0.1.0');
insert into ids select 'e', app.create_entity((select v from ids where k = 'org'), 'company', 'Yield Co', 'tenant');
insert into ids select 's1', app.create_source_record((select v from ids where k = 'org'), (select v from ids where k = 'ws'),
  'radar:x', 'directory', 'https://d20.example.com/1', now(), 'd20-hash-1', null, 'en', 'text one',
  (select v from ids where k = 'r'), (select v from ids where k = 'q1'), 'snippet');
insert into ids select 's2', app.create_source_record((select v from ids where k = 'org'), (select v from ids where k = 'ws'),
  'radar:x', 'directory', 'https://d20.example.com/2', now(), 'd20-hash-2', null, 'en', 'text two',
  (select v from ids where k = 'r'), (select v from ids where k = 'q1'));
select is((select content_kind || ':' || radar_query_id::text from public.source_record where id = (select v from ids where k = 's1')),
  'snippet:' || (select v from ids where k = 'q1')::text, '34. records carry their query and content kind');
select throws_ok(format($$select app.create_source_record(%L::uuid, %L::uuid, 'radar:x', 'directory', 'u', now(), 'd20-hash-3', null, 'en', 't', null, null, 'bogus')$$,
  (select v from ids where k = 'org'), (select v from ids where k = 'ws')), '23514', null, '35. content_kind is checked');
update public.source_record set extraction_status = 'irrelevant', status_detail = 'prefilter' where id = (select v from ids where k = 's2');
select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e'),
  (select id from public.predicate where key = 'opening.d20'), '"2027"'::jsonb, null, null, null,
  (select v from ids where k = 's1'), now(), (select id from public.extractor_version where capability = 'extract.d20test'), 80::smallint);
select app.upsert_verdict((select v from ids where k = 'v1'), (select v from ids where k = 'e'), 'Confirmed',
  array[(select id from public.claim where source_record_id = (select v from ids where k = 's1'))], '[]', '{}');
select app.update_radar_cursor((select v from ids where k = 'r'), '{}', 5, 5);
select ok(app.compute_radar_yield() >= 1, '36. yield is computed');
select is((select record_count || '/' || prefiltered_count || '/' || yield_count from public.radar where id = (select v from ids where k = 'r')),
  '2/1/1', '37. radar records, pre-filtered and yield');
select is((select yield_count from public.radar_query where id = (select v from ids where k = 'q1')), 1, '38. per-query yield');

-- ── Targeted search throttle ─────────────────────────────────────────────────
select ok(app.claim_targeted_search((select v from ids where k = 'b'), (select v from ids where k = 'e'))
  and not app.claim_targeted_search((select v from ids where k = 'b'), (select v from ids where k = 'e')),
  '39. one targeted search per brief and entity');
update public.targeted_search set searched_at = now() - interval '31 days';
select ok(app.claim_targeted_search((select v from ids where k = 'b'), (select v from ids where k = 'e')),
  '40. allowed again after 30 days');

select is((select count(*)::int from public.job_type_config where job_type in ('source.fetch_registry','source.fetch_tender',
  'source.fetch_event','source.fetch_directory','source.fetch_social_post','source.targeted_search','radar.yield_compute')),
  7, '41. every new job type has a kill switch');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000da02","role":"authenticated"}', true);
select throws_ok('select app.compute_radar_yield()', '42501', null, '42. clients cannot run service functions');
reset role;

select * from finish();
rollback;
