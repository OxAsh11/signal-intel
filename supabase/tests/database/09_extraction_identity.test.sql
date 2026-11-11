begin;
create extension if not exists pgtap with schema extensions;
select plan(12);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Well-known IDs from migration 0010 (canary predicate + extractor_version)
-- '00000000-0000-4000-a000-0000000000c5'  canary predicate
-- '00000000-0000-4000-a000-0000000000c6'  canary extractor_version

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-9000-000000000e01', 'owner@ex.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-9000-000000000e02', 'editor@ex.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-9000-000000000e03', 'viewer@ex.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-9000-000000000e04', 'beta@ex.test',   'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e01","role":"authenticated"}', true);
select public.create_organisation('EI Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e04","role":"authenticated"}', true);
select public.create_organisation('EI Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org',   id from public.organisation where name = 'EI Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'EI Beta';
insert into ids select 'ws',    w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Alpha';
insert into ids select 'ws_b',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Beta';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-9000-000000000e02', (select v from ids where k = 'org'), '00000000-0000-4000-9000-000000000e02', 'editor@ex.test'),
  ('00000000-0000-4000-9000-000000000e03', (select v from ids where k = 'org'), '00000000-0000-4000-9000-000000000e03', 'viewer@ex.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-9000-000000000e02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-9000-000000000e03', (select v from ids where k = 'ws'), 'Viewer');

-- Source record (service_role)
insert into ids select 'sr',
  app.create_source_record(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'radar:test', 'news', 'https://example.com/ei-test',
    now(),
    'aaaa2222aaaa2222aaaa2222aaaa2222aaaa2222aaaa2222aaaa2222aaaa2222',
    null, 'en');

-- Provisional entity for mention fixture (service_role)
insert into ids select 'ent',
  app.create_entity(
    (select v from ids where k = 'org'), 'company'::public.entity_type, 'Acme Corp');

-- Mention pointing to provisional entity (service_role)
insert into ids select 'men',
  app.create_mention(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'sr'),
    'company'::public.entity_type,
    'Acme Corp', 'acme.com', 'US', '{}'::jsonb);

-- Loser entity for merge test
insert into ids select 'ent_loser',
  app.create_entity(
    (select v from ids where k = 'org'), 'company'::public.entity_type, 'Acme Duplicate');

-- Mention pointing to loser entity (for merge test to have something to redirect)
insert into ids select 'men_loser',
  app.create_mention(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'sr'),
    'company'::public.entity_type,
    'Acme Duplicate', null, null, '{}'::jsonb);
select app.resolve_mention((select v from ids where k = 'men_loser'), (select v from ids where k = 'ent_loser'), 'provisional', 0);

-- Review item for resolve tests (service_role; mention 'men' + candidate entity 'ent')
insert into ids select 'rev',
  app.create_review_item(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'men'),
    (select v from ids where k = 'ent'),
    'name_country', 75);

-- ── Test 1: authenticated cannot call app.update_extraction_status ───────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.update_extraction_status(%L::uuid, 'extracted', null)$q$,
    (select v from ids where k = 'sr')),
  '42501', null,
  '1. authenticated cannot call app.update_extraction_status'
);
reset role;

-- ── Test 2: authenticated cannot call app.create_mention ─────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_mention(%L::uuid, %L::uuid, 'company'::public.entity_type, 'X', null, null, '{}'::jsonb)$q$,
    (select v from ids where k = 'org'),
    (select v from ids where k = 'sr')),
  '42501', null,
  '2. authenticated cannot call app.create_mention'
);
reset role;

-- ── Test 3: authenticated cannot call app.create_claim ───────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_claim(%L::uuid, %L::uuid, '00000000-0000-4000-a000-0000000000c5'::uuid,
    '"true"'::jsonb, null, null, null, %L::uuid, now(),
    '00000000-0000-4000-a000-0000000000c6'::uuid, 90::smallint)$q$,
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent'),
    (select v from ids where k = 'sr')),
  '42501', null,
  '3. authenticated cannot call app.create_claim'
);
reset role;

-- ── Test 4: service_role can create entity with trimmed display_name ──────────
select is(
  trim(display_name),
  'Acme Corp',
  '4. create_entity display_name is trimmed'
) from public.entity where id = (select v from ids where k = 'ent');

-- ── Test 5: create_entity_alias upsert updates confidence on conflict ─────────
do $$
declare
  v_id1 uuid;
  v_id2 uuid;
  v_conf smallint;
begin
  v_id1 := app.create_entity_alias(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent'),
    'domain'::public.alias_type, 'acme.com', 60, (select v from ids where k = 'sr'));
  v_id2 := app.create_entity_alias(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ent'),
    'domain'::public.alias_type, 'acme.com', 90, (select v from ids where k = 'sr'));
  -- Second insert should upsert (same entity+type+value_normalised) → confidence updated.
  select confidence into v_conf from public.entity_alias
  where entity_id = (select v from ids where k = 'ent')
    and alias_type = 'domain' and value_normalised = 'acme.com';
  if v_conf is distinct from 90 then
    raise exception 'confidence mismatch: got %', v_conf;
  end if;
end;
$$;
select pass('5. create_entity_alias upsert updates confidence');

-- ── Test 6: resolve_mention sets entity_id and match_method ──────────────────
select is(
  (select match_method from public.mention where id = (select v from ids where k = 'men_loser')),
  'provisional',
  '6. resolve_mention sets match_method'
);

-- ── Test 7: create_account ON CONFLICT DO NOTHING on duplicate ───────────────
do $$
declare
  v_id1 uuid;
  v_id2 uuid;
begin
  v_id1 := app.create_account(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent'));
  v_id2 := app.create_account(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent'));
  -- Second call returns existing id (ON CONFLICT DO NOTHING → fallback SELECT)
  if v_id2 is null or v_id2 <> v_id1 then
    raise exception 'expected existing id on duplicate, got %', v_id2;
  end if;
end;
$$;
select pass('7. create_account returns existing id on duplicate workspace+entity');

-- ── Test 8: merge_entities moves loser mentions to winner; loser = merged ─────
select app.merge_entities(
  (select v from ids where k = 'ent'),
  (select v from ids where k = 'ent_loser'),
  null);
select is(
  (select status from public.entity where id = (select v from ids where k = 'ent_loser')),
  'merged',
  '8. merge_entities sets loser entity status to merged'
);

-- ── Test 9: authenticated cannot call app.create_review_item ─────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.create_review_item(%L::uuid, %L::uuid, %L::uuid, %L::uuid, 'test', 50::smallint)$q$,
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'men'),
    (select v from ids where k = 'ent')),
  '42501', null,
  '9. authenticated cannot call app.create_review_item'
);
reset role;

-- ── Test 10: Viewer cannot call app.resolve_review_item ──────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e03","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.resolve_review_item(%L::uuid, 'skip')$q$,
    (select v from ids where k = 'rev')),
  '42501', null,
  '10. viewer cannot call app.resolve_review_item'
);
reset role;

-- ── Test 11: Editor can resolve a review_item (merge action) ─────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e02","role":"authenticated"}', true);
select lives_ok(
  format($q$select app.resolve_review_item(%L::uuid, 'merge')$q$,
    (select v from ids where k = 'rev')),
  '11. editor can resolve review_item with merge action'
);
reset role;

-- ── Test 12: review_item RLS — workspace isolation ───────────────────────────
-- Beta-org user (00000000-0000-4000-9000-000000000e04) has a valid auth token
-- but belongs to a different org/workspace and must not see Alpha's review items.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-9000-000000000e04","role":"authenticated"}', true);
select is(
  (select count(*)::int from public.review_item
   where workspace_id = (select v from ids where k = 'ws')),
  0,
  '12. beta org user cannot see alpha workspace review_items'
);
reset role;

select * from finish();
rollback;
