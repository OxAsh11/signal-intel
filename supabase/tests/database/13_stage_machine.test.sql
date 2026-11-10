begin;
create extension if not exists pgtap with schema extensions;
select plan(10);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Users for auth
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000b300', 'owner@stage.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000b301', 'viewer@stage.test', 'authenticated', 'authenticated');

-- Create Suite 13 organisation
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b300","role":"authenticated"}', true);
select public.create_organisation('Suite 13 Stage Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 13 Stage Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 13 Stage Corp';

-- Members and roles
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000b301', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b301', 'viewer@stage.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b301', (select v from ids where k = 'ws'), 'Viewer');

-- Brief + brief_version
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Suite 13 Brief', 'Draft', 'done'
  );
insert into ids select 'bv', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv'),
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer":"stage suite"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Entity + account
set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 13 Alpha Corp', 'tenant'
);
insert into public.account (id, org_id, workspace_id, entity_id, stage)
  values (
    gen_random_uuid(),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent'),
    'Untouched'
  );
insert into ids select 'acct', id from public.account where entity_id = (select v from ids where k = 'ent');
reset role;

-- Pre-generate activity UUIDs for idempotency and progression tests
insert into ids select 'act1', gen_random_uuid();
insert into ids select 'act2', gen_random_uuid();
insert into ids select 'act3', gen_random_uuid();
insert into ids select 'act4', gen_random_uuid();

-- ── 1. app.upsert_account_stage exists ───────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'upsert_account_stage'),
  'app.upsert_account_stage exists'
);

-- ── 2. Direct INSERT into public.account_stage_event blocked (42501) ─────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b301","role":"authenticated"}', true);
select throws_ok(
  $q$
    INSERT INTO public.account_stage_event
      (org_id, workspace_id, account_id, from_stage, to_stage, occurred_at)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'acct'),
      'Untouched'::public.stage,
      'Queued'::public.stage,
      now()
    )
  $q$,
  '42501', null,
  'direct INSERT into account_stage_event blocked by guard'
);
reset role;

-- ── 3. upsert_account_stage inserts event and returns a uuid ─────────────────
-- Advance Untouched → Queued with act1.
set local role service_role;
select ok(
  app.upsert_account_stage(
    (select v from ids where k = 'acct'),
    'Queued'::public.stage,
    (select v from ids where k = 'act1'),
    now()
  ) is not null,
  'upsert_account_stage inserts event row and returns uuid'
);
reset role;

-- ── 4. account.stage_changed outbox event emitted ───────────────────────────
select ok(
  exists(
    select 1 from public.outbox
    where event          = 'account.stage_changed'::public.event_type
      and aggregate_type = 'account'
      and payload->>'account_id' = (select v from ids where k = 'acct')::text
  ),
  'account.stage_changed emitted after upsert_account_stage'
);

-- ── 5. Second call with same (account_id, activity_id) returns same uuid ─────
set local role service_role;
select is(
  app.upsert_account_stage(
    (select v from ids where k = 'acct'),
    'Queued'::public.stage,
    (select v from ids where k = 'act1'),
    now()
  ),
  (select id from public.account_stage_event
   where account_id   = (select v from ids where k = 'acct')
     and activity_id  = (select v from ids where k = 'act1')),
  'second upsert_account_stage with same activity_id returns same uuid (idempotent)'
);
reset role;

-- ── 5b. Only one event row exists for act1 after idempotent call ─────────────
select is(
  (select count(*)::int from public.account_stage_event
   where account_id  = (select v from ids where k = 'acct')
     and activity_id = (select v from ids where k = 'act1')),
  1,
  'only one account_stage_event row for act1 after idempotent second call'
);

-- ── 6. Direct UPDATE to account.stage blocked for authenticated (42501) ───────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b301","role":"authenticated"}', true);
select throws_ok(
  $q$
    UPDATE public.account
    SET stage = 'Contacted'::public.stage
    WHERE id = (SELECT v FROM ids WHERE k = 'acct')
  $q$,
  '42501', null,
  'direct UPDATE to account.stage blocked for authenticated'
);
reset role;

-- ── 7. account.stage = Queued after assertion 3 advance ──────────────────────
select is(
  (select stage from public.account where id = (select v from ids where k = 'acct')),
  'Queued'::public.stage,
  'account.stage is Queued after Untouched → Queued transition'
);

-- ── 8. Advance Queued → Contacted; account.stage updated ─────────────────────
set local role service_role;
perform app.upsert_account_stage(
  (select v from ids where k = 'acct'),
  'Contacted'::public.stage,
  (select v from ids where k = 'act2'),
  now()
);
reset role;
select is(
  (select stage from public.account where id = (select v from ids where k = 'acct')),
  'Contacted'::public.stage,
  'account.stage is Contacted after Queued → Contacted transition'
);

-- ── 9. Post-Disqualified upsert accepted by SQL (Python enforces transitions) ─
-- SQL imposes no hard stop after Disqualified; the transition table is enforced
-- by the Python layer. The function must return a uuid without error.
set local role service_role;
perform app.upsert_account_stage(
  (select v from ids where k = 'acct'),
  'Disqualified'::public.stage,
  (select v from ids where k = 'act3'),
  now()
);
select ok(
  app.upsert_account_stage(
    (select v from ids where k = 'acct'),
    'Queued'::public.stage,
    (select v from ids where k = 'act4'),
    now()
  ) is not null,
  'upsert_account_stage after Disqualified returns uuid — SQL accepts; Python enforces transitions'
);
reset role;

select * from finish();
rollback;
