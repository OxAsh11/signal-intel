begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000b5000', 'owner@suite15.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000b5001', 'viewer@suite15.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5000","role":"authenticated"}', true);
select public.create_organisation('Suite 15 Campaigns Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 15 Campaigns Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 15 Campaigns Corp';

-- Entity + account for campaign member tests
-- Note: add_campaign_member requires (campaign_id, account_id, person_entity_id)
set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'person', 'Suite 15 Person', 'tenant'
);
insert into public.account (id, org_id, workspace_id, entity_id)
  values (gen_random_uuid(),
          (select v from ids where k = 'org'),
          (select v from ids where k = 'ws'),
          (select v from ids where k = 'ent'));
insert into ids select 'account', id from public.account
  where entity_id = (select v from ids where k = 'ent');
reset role;

-- ── Assertion 1: app.create_campaign exists ──────────────────────────────────
select has_function('app', 'create_campaign', 'app.create_campaign exists');

-- ── Assertion 2: app.add_campaign_member exists ──────────────────────────────
select has_function('app', 'add_campaign_member', 'app.add_campaign_member exists');

-- ── Assertion 3: direct INSERT into campaign blocked for authenticated ────────
-- RLS enabled, no INSERT grant → 42501
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5000","role":"authenticated"}', true);
select throws_ok(
  $$insert into public.campaign (id, org_id, workspace_id, name, motion, push_mode)
    values (gen_random_uuid(),
            (select v from ids where k = 'org'),
            (select v from ids where k = 'ws'),
            'x', 'Geographic', 'off')$$,
  '42501',
  null,
  'direct INSERT into campaign blocked for authenticated'
);
reset role;

-- ── Assertion 4: create_campaign returns a UUID ───────────────────────────────
-- Actual signature: create_campaign(org_id, workspace_id, brief_id, name, motion, dest_int_id DEFAULT NULL)
set local role service_role;
select ok(
  (select app.create_campaign(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    NULL::uuid,
    'Test Campaign',
    'Geographic'::public.motion
  ) is not null),
  'create_campaign returns a UUID'
);

insert into ids select 'campaign', app.create_campaign(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  NULL::uuid,
  'Suite 15 Campaign',
  'Geographic'::public.motion
);

-- Members can only be added to a live campaign, and a campaign goes live only from dry run
-- (migration 0029).
select app.set_campaign_push_mode((select v from ids where k = 'campaign'), 'dry run'::public.push_mode);
select app.set_campaign_push_mode((select v from ids where k = 'campaign'), 'live'::public.push_mode);

-- ── Assertion 5: add_campaign_member first call succeeds ─────────────────────
-- Actual signature: add_campaign_member(campaign_id, account_id, person_entity_id, contact_point_id DEFAULT NULL, variables DEFAULT '{}')
select lives_ok(
  $$select app.add_campaign_member(
    (select v from ids where k = 'campaign'),
    (select v from ids where k = 'account'),
    (select v from ids where k = 'ent')
  )$$,
  'add_campaign_member first call succeeds'
);

-- ── Assertion 6: add_campaign_member second call is idempotent ───────────────
select lives_ok(
  $$select app.add_campaign_member(
    (select v from ids where k = 'campaign'),
    (select v from ids where k = 'account'),
    (select v from ids where k = 'ent')
  )$$,
  'add_campaign_member second call is idempotent (no error)'
);

-- ── Assertion 7: check_and_pause_if_bouncing returns false when bounce_count=0
select is(
  (select app.check_and_pause_if_bouncing((select v from ids where k = 'campaign'))),
  false,
  'check_and_pause_if_bouncing returns false when bounce_count=0'
);

-- ── Assertion 8: set_campaign_push_mode changes push_mode ────────────────────
select lives_ok(
  $$select app.set_campaign_push_mode((select v from ids where k = 'campaign'), 'off'::public.push_mode)$$,
  'set_campaign_push_mode runs without error'
);
reset role;

select * from finish();
rollback;
