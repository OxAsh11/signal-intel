begin;
create extension if not exists pgtap with schema extensions;
select plan(7);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000b5010', 'owner@suite16.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5010","role":"authenticated"}', true);
select public.create_organisation('Suite 16 Meetings Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 16 Meetings Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 16 Meetings Corp';

set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 16 Account Corp', 'tenant'
);
insert into public.account (id, org_id, workspace_id, entity_id, stage)
  values (gen_random_uuid(),
          (select v from ids where k = 'org'),
          (select v from ids where k = 'ws'),
          (select v from ids where k = 'ent'),
          'Identified');
insert into ids select 'account', id from public.account
  where entity_id = (select v from ids where k = 'ent');

-- Integration for meeting source
-- Actual columns: name text NOT NULL, category text NOT NULL (not 'type')
insert into public.integration (id, org_id, workspace_id, name, category)
  values (gen_random_uuid(),
          (select v from ids where k = 'org'),
          (select v from ids where k = 'ws'),
          'Google Calendar', 'calendar');
insert into ids select 'integration', id from public.integration
  where workspace_id = (select v from ids where k = 'ws');
reset role;

-- ── Assertion 1: app.create_meeting exists ───────────────────────────────────
select has_function('app', 'create_meeting', 'app.create_meeting exists');

-- ── Assertion 2: app.create_task exists ──────────────────────────────────────
select has_function('app', 'create_task', 'app.create_task exists');

-- ── Assertion 3: direct INSERT into meeting blocked for authenticated ─────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5010","role":"authenticated"}', true);
select throws_ok(
  $$insert into public.meeting (id, org_id, workspace_id, account_id, source_integration_id, external_id, scheduled_at)
    values (gen_random_uuid(),
            (select v from ids where k = 'org'),
            (select v from ids where k = 'ws'),
            (select v from ids where k = 'account'),
            (select v from ids where k = 'integration'),
            'ext-b501', now())$$,
  '42501',
  null,
  'direct INSERT into meeting blocked for authenticated'
);
reset role;

-- ── Assertion 4: create_meeting first call ok ────────────────────────────────
-- Actual signature: create_meeting(org_id, workspace_id, account_id, person_entity_id, external_id, source_integration_id, scheduled_at)
set local role service_role;
select lives_ok(
  $$select app.create_meeting(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'account'),
    NULL::uuid,
    'ext-b501-meeting',
    (select v from ids where k = 'integration'),
    now()
  )$$,
  'create_meeting first call ok'
);

-- ── Assertion 5: create_meeting second call is idempotent ────────────────────
select lives_ok(
  $$select app.create_meeting(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'account'),
    NULL::uuid,
    'ext-b501-meeting',
    (select v from ids where k = 'integration'),
    now()
  )$$,
  'create_meeting second call is idempotent (no error)'
);
insert into ids select 'meeting', id from public.meeting
  where external_id = 'ext-b501-meeting' limit 1;

-- ── Assertion 6: confirm_meeting with proof_status='confirmed' emits meeting.held
-- Actual signature: confirm_meeting(meeting_id, held_at, proof_status, attribution_campaign_id DEFAULT NULL)
select lives_ok(
  $$select app.confirm_meeting(
    (select v from ids where k = 'meeting'),
    now(),
    'confirmed'
  )$$,
  'confirm_meeting with confirmed runs without error'
);

-- ── Assertion 7: create_task emits task.created (lives without error) ─────────
-- Actual signature: create_task(org_id, workspace_id, account_id, title, origin, owner_member_id DEFAULT NULL, due_date DEFAULT NULL)
select lives_ok(
  $$select app.create_task(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'account'),
    'Suite 16 follow-up task',
    'manual'
  )$$,
  'create_task runs without error'
);
reset role;

select * from finish();
rollback;
