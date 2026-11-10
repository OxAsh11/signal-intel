begin;
create extension if not exists pgtap with schema extensions;
select plan(10);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Users for auth
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000b100', 'owner@score.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000b101', 'viewer@score.test', 'authenticated', 'authenticated');

-- Create Suite 11 organisation
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b100","role":"authenticated"}', true);
select public.create_organisation('Suite 11 Scoring Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 11 Scoring Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 11 Scoring Corp';

-- Members and roles
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000b101', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b101', 'viewer@score.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b101', (select v from ids where k = 'ws'), 'Viewer');

-- Brief + brief_version
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Suite 11 Brief', 'Draft', 'done'
  );
insert into ids select 'bv', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv'),
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer":"scoring suite"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Entity + account for main tests (assertions 5, 6, 8, 9, 10)
set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 11 Alpha Corp', 'tenant'
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

-- Entity + account for band-gate test (assertion 7)
insert into ids select 'ent2', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 11 Beta Corp', 'tenant'
);
insert into public.account (id, org_id, workspace_id, entity_id, stage)
  values (
    gen_random_uuid(),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent2'),
    'Untouched'
  );
insert into ids select 'acct2', id from public.account where entity_id = (select v from ids where k = 'ent2');

-- Verdict = Unverified for ent2 + bv (gate will cap High → Low in assertion 7)
perform app.upsert_verdict(
  (select v from ids where k = 'bv'),
  (select v from ids where k = 'ent2'),
  'Unverified'::public.verdict_value,
  '{}',
  '[]'::jsonb,
  '{}'::jsonb
);
reset role;

-- ── 1. app.upsert_score exists ───────────────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'upsert_score'),
  'app.upsert_score exists'
);

-- ── 2. app.upsert_label exists ───────────────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'upsert_label'),
  'app.upsert_label exists'
);

-- ── 3. Direct INSERT into public.score blocked (42501) ───────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b101","role":"authenticated"}', true);
select throws_ok(
  $q$
    INSERT INTO public.score
      (org_id, workspace_id, account_id, brief_version_id,
       fit, evidence, scale, timing, reachability, priority, readiness, band, gates, explanation, inputs)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'acct'),
      (SELECT v FROM ids WHERE k = 'bv'),
      50::smallint, 50::smallint, 50::smallint, 50::smallint, 50::smallint,
      50.0::numeric, 50::smallint,
      'Low'::public.band,
      '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
    )
  $q$,
  '42501', null,
  'direct INSERT into score blocked'
);
reset role;

-- ── 4. Direct INSERT into public.label blocked (42501) ───────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b101","role":"authenticated"}', true);
select throws_ok(
  $q$
    INSERT INTO public.label
      (org_id, workspace_id, account_id, brief_version_id, kind, value, inputs)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'acct'),
      (SELECT v FROM ids WHERE k = 'bv'),
      'vertical', 'SaaS', '{}'::jsonb
    )
  $q$,
  '42501', null,
  'direct INSERT into label blocked'
);
reset role;

-- ── 5. app.upsert_score with valid args returns a uuid ───────────────────────
set local role service_role;
select ok(
  app.upsert_score(
    (select v from ids where k = 'acct'),
    (select v from ids where k = 'bv'),
    80::smallint, 70::smallint, 60::smallint, 50::smallint, 40::smallint,
    75.00::numeric, 45::smallint,
    'Low'::public.band,
    '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
  ) is not null,
  'upsert_score returns a uuid'
);
reset role;

-- ── 6. Second upsert_score same (account_id, brief_version_id) returns same uuid
set local role service_role;
select is(
  app.upsert_score(
    (select v from ids where k = 'acct'),
    (select v from ids where k = 'bv'),
    80::smallint, 70::smallint, 60::smallint, 50::smallint, 40::smallint,
    75.00::numeric, 45::smallint,
    'Low'::public.band,
    '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
  ),
  (select id from public.score
   where account_id      = (select v from ids where k = 'acct')
     and brief_version_id = (select v from ids where k = 'bv')),
  'second upsert_score ON CONFLICT returns same uuid'
);
reset role;

-- ── 7. Band gate: upsert_score with High band + Unverified verdict → stored Low
-- SQL gate checks latest verdict and forces High/Medium → Low when Unverified.
set local role service_role;
perform app.upsert_score(
  (select v from ids where k = 'acct2'),
  (select v from ids where k = 'bv'),
  80::smallint, 70::smallint, 60::smallint, 50::smallint, 40::smallint,
  75.00::numeric, 45::smallint,
  'High'::public.band,
  '[]'::jsonb, '{}'::jsonb, '{}'::jsonb
);
reset role;
select is(
  (select band from public.score
   where account_id      = (select v from ids where k = 'acct2')
     and brief_version_id = (select v from ids where k = 'bv')),
  'Low'::public.band,
  'SQL gate forces High band to Low when verdict is Unverified'
);

-- ── 8. app.upsert_label with valid args returns a uuid ───────────────────────
set local role service_role;
select ok(
  app.upsert_label(
    (select v from ids where k = 'acct'),
    (select v from ids where k = 'bv'),
    'vertical',
    'SaaS',
    '{}'::jsonb
  ) is not null,
  'upsert_label returns a uuid'
);
reset role;

-- ── 9. score.band_changed outbox event emitted on band change ────────────────
-- Assertion 5 inserted a new score (NULL → Low) for acct, triggering the event.
select ok(
  exists(
    select 1 from public.outbox
    where event          = 'score.band_changed'::public.event_type
      and aggregate_type = 'score'
      and payload->>'account_id' = (select v from ids where k = 'acct')::text
  ),
  'score.band_changed emitted after first upsert_score'
);

-- ── 10. No second score.band_changed when band unchanged ─────────────────────
-- Assertion 6 re-upserted with the same band (Low → Low): no additional event.
select is(
  (select count(*)::int from public.outbox
   where event          = 'score.band_changed'::public.event_type
     and aggregate_type = 'score'
     and payload->>'account_id' = (select v from ids where k = 'acct')::text),
  1,
  'exactly one score.band_changed event for acct (no duplicate on same band)'
);

select * from finish();
rollback;
