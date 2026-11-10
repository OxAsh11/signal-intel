begin;
create extension if not exists pgtap with schema extensions;
select plan(6);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Users for auth
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000b400', 'owner@card.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000b401', 'viewer@card.test', 'authenticated', 'authenticated');

-- Create Suite 14 organisation
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b400","role":"authenticated"}', true);
select public.create_organisation('Suite 14 Card Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 14 Card Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 14 Card Corp';

-- Members and roles
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000b401', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b401', 'viewer@card.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b401', (select v from ids where k = 'ws'), 'Viewer');

-- Brief + brief_version
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Suite 14 Brief', 'Draft', 'done'
  );
insert into ids select 'bv', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv'),
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer":"card suite"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Entity + account
set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 14 Alpha Corp', 'tenant'
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

-- ── 1. app.upsert_account_card exists ────────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'upsert_account_card'),
  'app.upsert_account_card exists'
);

-- ── 2. Direct INSERT into public.account_card blocked (42501) ────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b401","role":"authenticated"}', true);
select throws_ok(
  $q$
    INSERT INTO public.account_card
      (org_id, workspace_id, account_id, brief_version_id, content, inputs)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'acct'),
      (SELECT v FROM ids WHERE k = 'bv'),
      '{"headline":"blocked"}'::jsonb,
      '{}'::jsonb
    )
  $q$,
  '42501', null,
  'direct INSERT into account_card blocked by guard'
);
reset role;

-- ── 3. upsert_account_card with valid args returns a uuid ────────────────────
set local role service_role;
select ok(
  app.upsert_account_card(
    (select v from ids where k = 'acct'),
    (select v from ids where k = 'bv'),
    '{"headline":"Suite 14 test card","body":"content goes here"}'::jsonb,
    '{}'::jsonb
  ) is not null,
  'upsert_account_card returns a uuid'
);
reset role;

-- ── 4. Second upsert_account_card same (account_id, brief_version_id) → same uuid
set local role service_role;
select is(
  app.upsert_account_card(
    (select v from ids where k = 'acct'),
    (select v from ids where k = 'bv'),
    '{"headline":"Suite 14 test card updated","body":"new content"}'::jsonb,
    '{}'::jsonb
  ),
  (select id from public.account_card
   where account_id      = (select v from ids where k = 'acct')
     and brief_version_id = (select v from ids where k = 'bv')),
  'second upsert_account_card ON CONFLICT returns same uuid'
);
reset role;

-- ── 5. Viewer can SELECT from public.account_card after upsert ───────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b401","role":"authenticated"}', true);
select ok(
  (select count(*)::int from public.account_card
   where workspace_id = (select v from ids where k = 'ws')) = 1,
  'viewer can SELECT from account_card in own workspace'
);
reset role;

-- ── 6. account_card.content is jsonb ─────────────────────────────────────────
select ok(
  pg_typeof(
    (select content from public.account_card
     where account_id      = (select v from ids where k = 'acct')
       and brief_version_id = (select v from ids where k = 'bv'))
  )::text = 'jsonb',
  'account_card.content is of type jsonb'
);

select * from finish();
rollback;
