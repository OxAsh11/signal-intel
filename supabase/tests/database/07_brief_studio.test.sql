begin;
create extension if not exists pgtap with schema extensions;
select plan(20);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000aa01', 'a@bs.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000aa02', 'j@bs.test',  'authenticated', 'authenticated'),  -- Editor J
  ('00000000-0000-4000-8000-00000000aa03', 'k@bs.test',  'authenticated', 'authenticated'),  -- Viewer K
  ('00000000-0000-4000-8000-00000000aa04', 'o@bs.test',  'authenticated', 'authenticated'),  -- Operator O
  ('00000000-0000-4000-8000-00000000bb01', 'b2@bs.test', 'authenticated', 'authenticated');  -- Beta owner

-- Create Alpha and Beta orgs.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa01","role":"authenticated"}', true);
select public.create_organisation('BS Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000bb01","role":"authenticated"}', true);
select public.create_organisation('BS Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org',   id from public.organisation where name = 'BS Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'BS Beta';
insert into ids select 'ws',    w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'BS Alpha';
insert into ids select 'ws_b',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'BS Beta';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000aa02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa02', 'j@bs.test'),
  ('00000000-0000-4000-8000-00000000aa03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa03', 'k@bs.test'),
  ('00000000-0000-4000-8000-00000000aa04', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa04', 'o@bs.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa03', (select v from ids where k = 'ws'), 'Viewer'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000aa04', (select v from ids where k = 'ws'), 'Operator');

-- Insert a test entity for the entity-type exclusion test (8a).
with ne as (
  insert into public.entity (org_id, entity_type, display_name)
  values ((select v from ids where k = 'org'), 'company', 'Excl Target Corp')
  returning id
)
insert into ids select 'ent', id from ne;

-- ── 1. create_brief creates a draft brief with intake_status='pending' ────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'brief_a', public.create_brief((select v from ids where k = 'ws'), 'AI Brief A');
reset role;

select is(
  (select intake_status from public.brief where id = (select v from ids where k = 'brief_a')),
  'pending',
  '1. create_brief sets intake_status=pending'
);

-- ── 2. publish_brief with missing offer raises 42501 ─────────────────────────
-- draft_config is NULL at this point; offer validation must reject immediately.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
select throws_ok(
  format($q$select public.publish_brief('%s'::uuid)$q$, (select v from ids where k = 'brief_a')),
  '42501', null,
  '2. publish_brief with null draft_config raises 42501'
);
reset role;

-- ── 3. save_brief_draft sets draft_config and intake_status='done' ────────────
-- Called at postgres level (service_role context — no JWT required by this function).
select app.save_brief_draft(
  (select v from ids where k = 'brief_a'),
  '{"offer":"CAD Software Firms","hypothesis":"Firms using SolidWorks that are actively hiring",
    "examples":{"positive":["Dassault Systemes","PTC Inc","Siemens NX"],"negative":["Microsoft","Google"]},
    "sources":{"radar_types":["job_postings"]}}'::jsonb
);

select is(
  (select intake_status from public.brief where id = (select v from ids where k = 'brief_a')),
  'done',
  '3. save_brief_draft sets intake_status=done'
);

-- ── 4a. publish_brief with all required fields returns a version id ───────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'vid', public.publish_brief((select v from ids where k = 'brief_a'));
reset role;

select ok(
  (select v from ids where k = 'vid') is not null,
  '4a. publish_brief returns a non-null version id'
);

-- ── 4b. publish_brief transitions state to Active ────────────────────────────
select is(
  (select state from public.brief where id = (select v from ids where k = 'brief_a')),
  'Active'::public.brief_state,
  '4b. publish_brief transitions state to Active'
);

-- ── 5. brief_version is immutable ────────────────────────────────────────────
select throws_ok(
  format($q$update public.brief_version set config = '{}'::jsonb where id = '%s'::uuid$q$,
         (select v from ids where k = 'vid')),
  '42501', null,
  '5. brief_version is immutable after insert'
);

-- ── 6a. update_brief_state Active→Paused ok ──────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
select lives_ok(
  format($q$select public.update_brief_state('%s'::uuid, 'Paused'::public.brief_state)$q$,
         (select v from ids where k = 'brief_a')),
  '6a. update_brief_state Active→Paused succeeds'
);
reset role;

-- ── 6b. update_brief_state Draft→Paused raises ────────────────────────────────
-- Create a second brief that stays in Draft state.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'brief_b', public.create_brief((select v from ids where k = 'ws'), 'AI Brief B');
select throws_ok(
  format($q$select public.update_brief_state('%s'::uuid, 'Paused'::public.brief_state)$q$,
         (select v from ids where k = 'brief_b')),
  '42501', null,
  '6b. update_brief_state Draft→Paused raises 42501'
);
reset role;

-- ── 7. create_workspace_predicate creates predicate with correct workspace_id ─
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'pred',
  public.create_workspace_predicate(
    (select v from ids where k = 'ws'), 'bs.test_signal', 'Test Signal', 'boolean');
reset role;

select ok(
  (select workspace_id from public.predicate where id = (select v from ids where k = 'pred'))
    = (select v from ids where k = 'ws'),
  '7. create_workspace_predicate stores the correct workspace_id'
);

-- ── 8a. add_exclusion with entity_id ok ──────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'excl_e',
  public.add_exclusion(
    (select v from ids where k = 'brief_a'),
    'entity',
    (select v from ids where k = 'ent'),
    null,
    'Direct competitor');
reset role;

select ok(
  (select v from ids where k = 'excl_e') is not null,
  '8a. add_exclusion with entity_id ok'
);

-- ── 8b. add_exclusion with pattern type ok ───────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'excl_p',
  public.add_exclusion(
    (select v from ids where k = 'brief_a'),
    'name',
    null,
    'Rival Corp',
    'too big');
reset role;

select ok(
  (select v from ids where k = 'excl_p') is not null,
  '8b. add_exclusion with pattern type ok'
);

-- ── 8c. add_exclusion with neither entity_id nor pattern raises ───────────────
-- value_type='name' but pattern=null and entity_id=null → check violation 23514.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
select throws_ok(
  format($q$select public.add_exclusion('%s'::uuid, 'name', null, null, null)$q$,
         (select v from ids where k = 'brief_a')),
  '23514', null,
  '8c. add_exclusion with neither entity_id nor pattern raises check violation'
);
reset role;

-- ── 9a. remove_exclusion deletes the row ─────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
select lives_ok(
  format($q$select public.remove_exclusion('%s'::uuid)$q$,
         (select v from ids where k = 'excl_p')),
  '9a. remove_exclusion succeeds for Editor'
);
reset role;

-- ── 9b. Viewer cannot remove an exclusion ────────────────────────────────────
-- Add a fresh exclusion at postgres level for the Viewer to attempt to remove.
with nx as (
  insert into public.exclusion_list (org_id, workspace_id, brief_id, value_type, pattern)
  values ((select v from ids where k = 'org'),
          (select v from ids where k = 'ws'),
          (select v from ids where k = 'brief_a'),
          'domain', 'rival.example.com')
  returning id
)
insert into ids select 'excl_v', id from nx;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa03","role":"authenticated"}', true);
select throws_ok(
  format($q$select public.remove_exclusion('%s'::uuid)$q$,
         (select v from ids where k = 'excl_v')),
  '42501', null,
  '9b. Viewer cannot remove_exclusion'
);
reset role;

-- ── 10a. list_briefs returns rows for workspace Viewer ────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa03","role":"authenticated"}', true);
select ok(
  (select count(*)::int from public.list_briefs((select v from ids where k = 'ws'))) > 0,
  '10a. list_briefs returns rows for workspace Viewer'
);

-- ── 10b. list_briefs raises 42501 for non-member workspace ───────────────────
select throws_ok(
  format($q$select * from public.list_briefs('%s'::uuid)$q$,
         (select v from ids where k = 'ws_b')),
  '42501', null,
  '10b. list_briefs raises 42501 for non-member workspace'
);
reset role;

-- ── 11. Operator cannot publish ──────────────────────────────────────────────
-- Insert brief_c directly (postgres level) with a valid draft_config.
with nb as (
  insert into public.brief (org_id, workspace_id, name, state, intake_status, draft_config)
  values (
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Op Brief', 'Draft', 'done',
    '{"offer":"Op offer","hypothesis":"Op hyp",
      "examples":{"positive":["A","B","C"],"negative":["X","Y"]},
      "sources":{"radar_types":["news"]}}'::jsonb
  )
  returning id
)
insert into ids select 'brief_c', id from nb;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa04","role":"authenticated"}', true);
select throws_ok(
  format($q$select public.publish_brief('%s'::uuid)$q$,
         (select v from ids where k = 'brief_c')),
  '42501', null,
  '11. Operator cannot publish a brief'
);
reset role;

-- ── 12. event_type enum includes 'brief.intake_requested' ────────────────────
select ok(
  exists (
    select 1 from pg_enum e
      join pg_type t on t.oid = e.enumtypid
      join pg_namespace n on n.oid = t.typnamespace
     where n.nspname = 'public'
       and t.typname  = 'event_type'
       and e.enumlabel = 'brief.intake_requested'
  ),
  '12. event_type enum includes brief.intake_requested'
);

-- ── 13. publish_brief on Active brief with draft_config creates version 2 ──────
-- Save a new draft_config to the Active brief_a via save_brief_draft_ui (UI path, should not flip intake_status).
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
select public.save_brief_draft_ui(
  (select v from ids where k = 'brief_a'),
  '{"offer":"CAD Software Firms v2","hypothesis":"Firms using SolidWorks that are actively hiring v2",
    "examples":{"positive":["Dassault Systemes","PTC Inc","Siemens NX"],"negative":["Microsoft","Google"]},
    "sources":{"radar_types":["job_postings"]}}'::jsonb
);
-- Migration 0032: a published Brief needs a ready preview of the exact draft before publishing.
insert into ids select 'prev2', public.request_brief_preview((select v from ids where k = 'brief_a'));
reset role;
select app.complete_brief_preview((select v from ids where k = 'prev2'), 'Redefine', true, '{}', 0, 0, false);
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000aa02","role":"authenticated"}', true);
insert into ids select 'vid2', public.publish_brief((select v from ids where k = 'brief_a'));
reset role;

select is(
  (select bv.version from public.brief_version bv where bv.id = (select v from ids where k = 'vid2')),
  2,
  '13. re-publish on Active brief creates version 2'
);

select is(
  (select current_version_id from public.brief where id = (select v from ids where k = 'brief_a')),
  (select v from ids where k = 'vid2'),
  '14. current_version_id points to new version after re-publish'
);

select * from finish();
rollback;
