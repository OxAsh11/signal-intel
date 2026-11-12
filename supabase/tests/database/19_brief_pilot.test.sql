begin;
create extension if not exists pgtap with schema extensions;
select plan(31);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- J = Editor, K = Viewer, B = owner of a second organisation.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000ca01', 'a@pi.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ca02', 'j@pi.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000ca03', 'k@pi.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000cb01', 'b@pi.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca01","role":"authenticated"}', true);
select public.create_organisation('PI Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cb01","role":"authenticated"}', true);
select public.create_organisation('PI Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'PI Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'PI Alpha';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000ca02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ca02', 'j@pi.test'),
  ('00000000-0000-4000-8000-00000000ca03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ca03', 'k@pi.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ca02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000ca03', (select v from ids where k = 'ws'), 'Viewer');

update public.workspace_settings set pilot_sample_size = 2 where workspace_id = (select v from ids where k = 'ws');

insert into ids select 'e' || n, app.create_entity((select v from ids where k = 'org'), 'company', 'Pilot Co ' || n)
  from generate_series(1, 3) n;

-- Brief A: complete draft. Brief Bad: missing examples. Brief C: second pilot.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
insert into ids select 'a',   public.create_brief((select v from ids where k = 'ws'), 'Pilot A');
insert into ids select 'bad', public.create_brief((select v from ids where k = 'ws'), 'Pilot Bad');
insert into ids select 'c',   public.create_brief((select v from ids where k = 'ws'), 'Pilot C');
insert into ids select 'd',   public.create_brief((select v from ids where k = 'ws'), 'Pilot D');
reset role;

create temp table cfg (k text primary key, v jsonb) on commit drop;
grant all on cfg to authenticated;
insert into cfg values ('full', '{"offer":"CAD","hypothesis":"Hiring CAD engineers",
  "examples":{"positive":["P1","P2","P3"],"negative":["N1","N2"]},
  "sources":{"radar_types":["job_postings"]}}');
select app.save_brief_draft((select v from ids where k = 'a'),   (select v from cfg where k = 'full'));
select app.save_brief_draft((select v from ids where k = 'c'),   (select v from cfg where k = 'full'));
select app.save_brief_draft((select v from ids where k = 'd'),   (select v from cfg where k = 'full'));
select app.save_brief_draft((select v from ids where k = 'bad'), '{"offer":"x","hypothesis":"y","sources":{"radar_types":["news_publications"]}}');

-- ── Start pilot ──────────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca03","role":"authenticated"}', true);
select throws_ok(format('select public.start_pilot(%L::uuid)', (select v from ids where k = 'a')),
  '42501', null, '1. a Viewer cannot start a pilot');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select throws_ok(format('select public.start_pilot(%L::uuid)', (select v from ids where k = 'bad')),
  '42501', null, '2. start_pilot refuses a draft without the required examples');
insert into ids select 'v1', public.start_pilot((select v from ids where k = 'a'));
insert into ids select 'c1', public.start_pilot((select v from ids where k = 'c'));
insert into ids select 'd1', public.start_pilot((select v from ids where k = 'd'));
reset role;

select is((select state from public.brief where id = (select v from ids where k = 'a')),
  'Pilot'::public.brief_state, '3. start_pilot moves the Brief to Pilot');
select is((select version from public.brief_version where id = (select v from ids where k = 'v1')),
  1, '4. start_pilot freezes version 1');
select ok((select draft_config is null from public.brief where id = (select v from ids where k = 'a')),
  '5. start_pilot clears the draft');

-- ── Pilot sample ─────────────────────────────────────────────────────────────
select ok(app.claim_pilot_slot((select v from ids where k = 'v1'), (select v from ids where k = 'e1')), '6. slot 1 is free');
select ok(app.claim_pilot_slot((select v from ids where k = 'v1'), (select v from ids where k = 'e2')), '7. slot 2 is free');
select ok(app.claim_pilot_slot((select v from ids where k = 'v1'), (select v from ids where k = 'e1')), '8. an entity keeps its slot');
select ok(not app.claim_pilot_slot((select v from ids where k = 'v1'), (select v from ids where k = 'e3')), '9. no slot past the sample size');
select is((select count(*)::int from public.pilot_sample where brief_version_id = (select v from ids where k = 'v1')),
  2, '10. the sample holds exactly pilot_sample_size entities');

select app.upsert_verdict((select v from ids where k = 'v1'), (select v from ids where k = 'e1'), 'Likely', '{}', '[]', '{}');

-- ── Publish needs a matching preview ─────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'),
  (select v || '{"icp":{"geographies":["Germany"]}}' from cfg where k = 'full'));
select throws_ok(format('select public.publish_brief(%L::uuid)', (select v from ids where k = 'a')),
  '55000', null, '11. publish without a preview is refused');
insert into ids select 'p1', public.request_brief_preview((select v from ids where k = 'a'));
reset role;

select is((select status from public.brief_preview where id = (select v from ids where k = 'p1')),
  'pending', '12. request_brief_preview stores a pending preview');
select is((select count(*)::int from public.outbox where event = 'brief.preview_requested'
             and payload ->> 'preview_id' = (select v::text from ids where k = 'p1')),
  1, '13. request_brief_preview emits brief.preview_requested');

select app.complete_brief_preview((select v from ids where k = 'p1'), 'Narrow', false, '{}', 0, 0, false);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'),
  (select v || '{"icp":{"geographies":["France"]}}' from cfg where k = 'full'));
select throws_ok(format('select public.publish_brief(%L::uuid)', (select v from ids where k = 'a')),
  '55000', null, '14. a preview of an older draft does not allow publishing');
insert into ids select 'p2', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p2'), 'Narrow', false, '{}', 0, 0, false);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
insert into ids select 'v2', public.publish_brief((select v from ids where k = 'a'));
reset role;

select is((select edit_tier from public.brief_version where id = (select v from ids where k = 'v2')),
  'Narrow'::public.edit_tier, '15. the new version records the preview tier');
select is((select count(*)::int from public.verdict where brief_version_id = (select v from ids where k = 'v2')),
  1, '16. verdicts are copied when the edit needs no re-judging');
select is((select state from public.brief where id = (select v from ids where k = 'a')),
  'Pilot'::public.brief_state, '17. publishing during a pilot keeps the Brief in Pilot');

-- ── Over budget needs an approval ────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select public.save_brief_draft_ui((select v from ids where k = 'a'),
  (select v || '{"sources":{"radar_types":["job_postings","news_publications"]}}' from cfg where k = 'full'));
insert into ids select 'p3', public.request_brief_preview((select v from ids where k = 'a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'p3'), 'Expand', false, '{}', 50, 120, true);

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select throws_ok(format('select public.publish_brief(%L::uuid)', (select v from ids where k = 'a')),
  '55000', null, '18. an over-budget edit cannot publish without approval');
insert into ids select 'ap', public.request_publish_approval((select v from ids where k = 'p3'));
reset role;

select is((select job_type from public.budget_approval where id = (select v from ids where k = 'ap')),
  'brief.publish', '19. request_publish_approval raises a brief.publish approval');
select throws_ok(format('select app.publish_brief_approved(%L::uuid, %L::uuid)',
    (select v from ids where k = 'a'), (select v from ids where k = 'p3')),
  '55000', null, '20. a pending approval does not publish');
update public.budget_approval set status = 'approved', decided_at = now() where id = (select v from ids where k = 'ap');
insert into ids select 'v3', app.publish_brief_approved((select v from ids where k = 'a'), (select v from ids where k = 'p3'));
select is((select version from public.brief_version where id = (select v from ids where k = 'v3')),
  3, '21. an approved over-budget edit publishes');

-- ── Calibrate ────────────────────────────────────────────────────────────────
insert into ids select 'ver', id from public.verdict
  where brief_version_id = (select v from ids where k = 'v3') and entity_id = (select v from ids where k = 'e1');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca03","role":"authenticated"}', true);
select throws_ok(format('select public.mark_pilot_verdict(%L::uuid, %L)', (select v from ids where k = 'ver'), 'confirm'),
  '42501', null, '22. a Viewer cannot mark verdicts');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select lives_ok(format('select public.mark_pilot_verdict(%L::uuid, %L, %L::public.verdict_value, true)',
    (select v from ids where k = 'ver'), 'correct', 'Contradicted'),
  '23. an Editor marks a verdict wrong');
select lives_ok(format('select public.mark_pilot_verdict(%L::uuid, %L)', (select v from ids where k = 'ver'), 'confirm'),
  '24. marking the same verdict again works');
reset role;

select is((select count(*)::int from public.feedback where target_id = (select v from ids where k = 'ver')),
  2, '25. each mark is stored as feedback');
select ok((select (inputs ->> 'human_override')::boolean and value = 'Contradicted' from public.verdict
            where id = (select v from ids where k = 'ver')),
  '26. the corrected verdict is held by the human mark');
select ok((select draft_config -> 'examples' -> 'negative' ? 'Pilot Co 1' from public.brief
            where id = (select v from ids where k = 'a')),
  '27. a wrong verdict can add the account to the negative examples');

-- ── Go live ──────────────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ca02","role":"authenticated"}', true);
select throws_ok(format('select public.go_live(%L::uuid)', (select v from ids where k = 'a')),
  '55000', null, '28. go_live is refused while the draft has unpublished changes');
select public.go_live((select v from ids where k = 'c'));
select public.update_brief_state((select v from ids where k = 'd'), 'Archived');
reset role;
select is((select state from public.brief where id = (select v from ids where k = 'c')),
  'Active'::public.brief_state, '29. go_live moves a pilot Brief to Active');
select is((select state from public.brief where id = (select v from ids where k = 'd')),
  'Archived'::public.brief_state, '30. a pilot Brief can be archived');

-- ── Isolation ────────────────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000cb01","role":"authenticated"}', true);
select is((select count(*)::int from public.brief_preview), 0, '31. another organisation cannot read previews');
reset role;

select * from finish();
rollback;
