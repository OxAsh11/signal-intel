begin;
create extension if not exists pgtap with schema extensions;
select plan(22);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-000000000d01', 'owner@ei.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000d02', 'editor@ei.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000d03', 'viewer@ei.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000d05', 'ob@ei.test',     'authenticated', 'authenticated');

-- Create EI Alpha and EI Beta organisations.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d01","role":"authenticated"}', true);
select public.create_organisation('EI Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d05","role":"authenticated"}', true);
select public.create_organisation('EI Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org',   id from public.organisation where name = 'EI Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'EI Beta';
insert into ids select 'ws',    w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Alpha';
insert into ids select 'ws_b',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Beta';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-000000000d02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000d02', 'editor@ei.test'),
  ('00000000-0000-4000-8000-000000000d03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000d03', 'viewer@ei.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000d02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000d03', (select v from ids where k = 'ws'), 'Viewer');

-- Source records (service_role context — no JWT, no set role).
insert into ids select 'src', app.create_source_record(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  'test-connector', 'news', 'https://test.example.com/article1',
  now(), 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01', null, 'en'
);
-- Source record for org_b (for RLS isolation test in group 11).
insert into ids select 'src_b', app.create_source_record(
  (select v from ids where k = 'org_b'),
  (select v from ids where k = 'ws_b'),
  'test-connector', 'news', 'https://test.example.com/article2',
  now(), 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb02', null, 'en'
);

-- Extractor version (direct insert at service_role).
insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('extract.test', '0.1.0', 'claude-test', '0.1.0');
insert into ids select 'extver', id from public.extractor_version
  where capability = 'extract.test' and prompt_version = '0.1.0' and model = 'claude-test';

-- Predicate: global (org_id = null, workspace_id = null).
insert into public.predicate (key, label, value_type)
  values ('employee.count', 'Employee Count', 'number');
insert into ids select 'pred', id from public.predicate where key = 'employee.count';

-- Entities (service_role via app.create_entity).
insert into ids select 'ent_a', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Acme Corp', 'tenant');
insert into ids select 'ent_winner', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Winner Co', 'tenant');
insert into ids select 'ent_loser', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Loser Co', 'tenant');
insert into ids select 'ent_cand', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Candidate Co', 'tenant');
-- Entity for org_b (used in RLS isolation fixture).
insert into ids select 'ent_b', app.create_entity(
  (select v from ids where k = 'org_b'), 'company', 'Beta Corp', 'tenant');

-- Mention for group 6 (resolve_mention test): created unresolved.
insert into ids select 'mention_resolve', app.create_mention(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'src'),
  'company', 'ResolveCo', 'resolveco.com', 'US', '{}'::jsonb
);

-- Mention for group 8 (merge test): pre-resolved to ent_loser.
insert into ids select 'mention_merge', app.create_mention(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'src'),
  'company', 'LoseCo', 'loseco.com', 'UK', '{}'::jsonb
);
select app.resolve_mention(
  (select v from ids where k = 'mention_merge'),
  (select v from ids where k = 'ent_loser'),
  'alias_match', 90
);

-- Entity alias for group 5 fixture (first call, confidence=70).
insert into ids select 'alias_a', app.create_entity_alias(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ent_a'),
  'domain', 'acmecorp.com', 70::smallint,
  (select v from ids where k = 'src')
);

-- Account for group 7 fixture (first call).
insert into ids select 'acct_a', app.create_account(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'ent_a')
);

-- Mention for review_items (groups 9 and 10).
insert into ids select 'mention_ri', app.create_mention(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'src'),
  'company', 'ReviewCo', 'reviewco.com', 'DE', '{}'::jsonb
);
-- Separate mention for group 9 ri_check.
insert into ids select 'mention_ri2', app.create_mention(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'src'),
  'company', 'CheckCo', null, null, '{}'::jsonb
);

-- Review item for group 10 (Editor merges it).
insert into ids select 'ri_merge', app.create_review_item(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'mention_ri'),
  (select v from ids where k = 'ent_cand'),
  'alias_match', 85
);

-- Review item for group 9 assertion (status check).
insert into ids select 'ri_check', app.create_review_item(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'mention_ri2'),
  (select v from ids where k = 'ent_cand'),
  'fuzzy_name', 60
);

-- Review item in ws_b for group 11 RLS isolation test.
insert into ids select 'mention_b', app.create_mention(
  (select v from ids where k = 'org_b'),
  (select v from ids where k = 'src_b'),
  'company', 'BetaCo', null, null, '{}'::jsonb
);
insert into ids select 'ri_b', app.create_review_item(
  (select v from ids where k = 'org_b'),
  (select v from ids where k = 'ws_b'),
  (select v from ids where k = 'mention_b'),
  (select v from ids where k = 'ent_b'),
  'fuzzy_name', 70
);

-- ── 1. update_extraction_status ──────────────────────────────────────────────
-- 1a. service_role can call it.
select lives_ok(
  format($q$select app.update_extraction_status(%L::uuid, 'extracted'::public.extraction_status)$q$,
    (select v from ids where k = 'src')),
  '1a. service_role can call update_extraction_status'
);
-- 1b. authenticated role is denied.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.update_extraction_status(%L::uuid, 'extracted'::public.extraction_status)$q$,
    (select v from ids where k = 'src')),
  '42501', null,
  '1b. authenticated cannot call update_extraction_status'
);
reset role;

-- ── 2. create_mention ────────────────────────────────────────────────────────
-- 2a. service_role can create_mention and returns uuid.
select ok(
  app.create_mention(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'src'),
    'company', 'NewCo', 'newco.io', 'CA', '{}'::jsonb
  ) is not null,
  '2a. service_role create_mention returns uuid'
);
-- 2b. authenticated role is denied.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_mention(%L::uuid, %L::uuid, 'company', 'X', null, null, '{}'::jsonb)$q$,
    (select v from ids where k = 'org'), (select v from ids where k = 'src')),
  '42501', null,
  '2b. authenticated cannot call create_mention'
);
reset role;

-- ── 3. create_claim ──────────────────────────────────────────────────────────
-- 3a. service_role can create_claim and returns uuid.
select ok(
  app.create_claim(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent_a'),
    (select v from ids where k = 'pred'),
    '100'::jsonb,
    'The company has 100 employees.',
    null,
    null,
    (select v from ids where k = 'src'),
    now(),
    (select v from ids where k = 'extver'),
    90::smallint
  ) is not null,
  '3a. service_role create_claim returns uuid'
);
-- 3b. authenticated role is denied.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_claim(%L::uuid, %L::uuid, %L::uuid, '100'::jsonb, null, null, null, %L::uuid, now(), %L::uuid, 90::smallint)$q$,
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent_a'),
    (select v from ids where k = 'pred'),
    (select v from ids where k = 'src'),
    (select v from ids where k = 'extver')),
  '42501', null,
  '3b. authenticated cannot call create_claim'
);
reset role;

-- ── 4. create_entity ─────────────────────────────────────────────────────────
-- 4a. service_role can create entity and returns uuid.
select ok(
  app.create_entity(
    (select v from ids where k = 'org'),
    'company', 'Test Entity G4', 'tenant'
  ) is not null,
  '4a. service_role create_entity returns uuid'
);
-- 4b. authenticated role is denied.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_entity(%L::uuid, 'company', 'Denied Co', 'tenant')$q$,
    (select v from ids where k = 'org')),
  '42501', null,
  '4b. authenticated cannot call create_entity'
);
reset role;

-- ── 5. create_entity_alias ───────────────────────────────────────────────────
-- 5a. First call returned uuid (alias_a created in fixture).
select ok(
  (select v from ids where k = 'alias_a') is not null,
  '5a. create_entity_alias returns uuid'
);
-- 5b. Second call with same (org, entity, alias_type, value) but higher confidence
--     upserts and returns the same existing uuid.
select is(
  app.create_entity_alias(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent_a'),
    'domain', 'acmecorp.com', 80::smallint,
    (select v from ids where k = 'src')
  ),
  (select v from ids where k = 'alias_a'),
  '5b. create_entity_alias upsert returns existing id'
);

-- ── 6. resolve_mention ───────────────────────────────────────────────────────
-- Call resolve_mention at service_role (non-assertion), then verify entity_id is set.
select app.resolve_mention(
  (select v from ids where k = 'mention_resolve'),
  (select v from ids where k = 'ent_a'),
  'alias_match', 85
);
-- 6a. entity_id is now set on the mention.
select is(
  (select entity_id from public.mention where id = (select v from ids where k = 'mention_resolve')),
  (select v from ids where k = 'ent_a'),
  '6a. resolve_mention sets entity_id on mention'
);
-- 6b. authenticated role is denied.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.resolve_mention(%L::uuid, %L::uuid, 'alias_match', 85::smallint)$q$,
    (select v from ids where k = 'mention_resolve'),
    (select v from ids where k = 'ent_a')),
  '42501', null,
  '6b. authenticated cannot call resolve_mention'
);
reset role;

-- ── 7. create_account ────────────────────────────────────────────────────────
-- 7a. First call returned uuid (acct_a created in fixture).
select ok(
  (select v from ids where k = 'acct_a') is not null,
  '7a. create_account returns uuid'
);
-- 7b. Second call with same workspace+entity returns the existing account id.
select is(
  app.create_account(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent_a')
  ),
  (select v from ids where k = 'acct_a'),
  '7b. create_account upsert returns existing id'
);

-- ── 8. merge_entities ────────────────────────────────────────────────────────
-- Call merge_entities at service_role (non-assertion).
select app.merge_entities(
  (select v from ids where k = 'ent_winner'),
  (select v from ids where k = 'ent_loser'),
  '00000000-0000-4000-8000-000000000d01'::uuid
);
-- 8a. Loser entity status is now 'merged'.
select is(
  (select status from public.entity where id = (select v from ids where k = 'ent_loser')),
  'merged',
  '8a. merge_entities sets loser.status = merged'
);
-- 8b. Loser mention is re-pointed to the winner.
select is(
  (select entity_id from public.mention where id = (select v from ids where k = 'mention_merge')),
  (select v from ids where k = 'ent_winner'),
  '8b. merged mention re-pointed to winner entity'
);

-- ── 9. create_review_item ────────────────────────────────────────────────────
-- 9a. ri_check was created in fixture and has a uuid.
select ok(
  (select v from ids where k = 'ri_check') is not null,
  '9a. create_review_item returns uuid'
);
-- 9b. Status defaults to pending.
select is(
  (select status from public.review_item where id = (select v from ids where k = 'ri_check')),
  'pending',
  '9b. new review_item status is pending'
);

-- ── 10. resolve_review_item ──────────────────────────────────────────────────
-- 10a. Editor can resolve a pending review_item with 'merge' action.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d02","role":"authenticated"}', true);
select lives_ok(
  format($q$select app.resolve_review_item(%L::uuid, 'merge')$q$,
    (select v from ids where k = 'ri_merge')),
  '10a. Editor can resolve_review_item (merge action)'
);
reset role;
-- 10b. Viewer is denied (Editor role check fires before status check).
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d03","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.resolve_review_item(%L::uuid, 'skip')$q$,
    (select v from ids where k = 'ri_merge')),
  '42501', null,
  '10b. Viewer cannot call resolve_review_item'
);
reset role;

-- ── 11. review_item RLS — workspace isolation ─────────────────────────────────
-- Viewer d03 is a Viewer in ws (EI Alpha) only; must not see ws_b review_items.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d03","role":"authenticated"}', true);
select is(
  (select count(*)::int from public.review_item where id = (select v from ids where k = 'ri_b')),
  0,
  '11. Viewer in ws-Alpha cannot see ws-Beta review_items'
);
reset role;

-- ── 12. source_record SELECT — Viewer can read own-org records ────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000d03","role":"authenticated"}', true);
select ok(
  (select count(*)::int from public.source_record
   where id = (select v from ids where k = 'src')) = 1,
  '12. authenticated Viewer can SELECT source_record from own org'
);
reset role;

select * from finish();
rollback;
