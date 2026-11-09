begin;
create extension if not exists pgtap with schema extensions;
select plan(13);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-000000000c01', 'a@cr.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000c02', 'j@cr.test',  'authenticated', 'authenticated'),  -- Editor J
  ('00000000-0000-4000-8000-000000000c03', 'k@cr.test',  'authenticated', 'authenticated'),  -- Viewer K
  ('00000000-0000-4000-8000-000000000c05', 'b@cr.test',  'authenticated', 'authenticated');  -- Beta owner

-- Create CR Alpha and CR Beta orgs.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c01","role":"authenticated"}', true);
select public.create_organisation('CR Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c05","role":"authenticated"}', true);
select public.create_organisation('CR Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org',   id from public.organisation where name = 'CR Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'CR Beta';
insert into ids select 'ws',    w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'CR Alpha';
insert into ids select 'ws_b',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'CR Beta';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-000000000c02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000c02', 'j@cr.test'),
  ('00000000-0000-4000-8000-000000000c03', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000c03', 'k@cr.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000c02', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000c03', (select v from ids where k = 'ws'), 'Viewer');

-- Set up brief via Editor J
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c02","role":"authenticated"}', true);
insert into ids select 'brief', public.create_brief((select v from ids where k = 'ws'), 'CR Brief B');
reset role;

-- ── 1. Editor can create radar ───────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c02","role":"authenticated"}', true);
-- Capture radar id.
create temp table radar_ids (k text primary key, v uuid) on commit drop;
grant all on radar_ids to authenticated;
insert into radar_ids select 'r1', public.create_radar(
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'brief'),
  'news', 'Tech News', '{}'::jsonb
);
select ok((select v from radar_ids where k = 'r1') is not null, '1. editor can create radar');
reset role;

-- ── 2. create_radar fires radar.fetch_queued outbox event ───────────────────
select is(
  (select count(*)::int from public.outbox where event = 'radar.fetch_queued'
   and (payload->>'radar_id')::uuid = (select v from radar_ids where k = 'r1')),
  1,
  '2. create_radar fires radar.fetch_queued outbox event'
);

-- ── 3. Viewer cannot create radar ───────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c03","role":"authenticated"}', true);
select throws_ok(
  format($q$select public.create_radar(%L::uuid, %L::uuid, 'website', 'X', '{}'::jsonb)$q$,
    (select v from ids where k = 'ws'), (select v from ids where k = 'brief')),
  '42501', null,
  '3. viewer cannot create radar'
);
reset role;

-- ── 4. Source record deduplication ──────────────────────────────────────────
-- Two calls with the same content_hash must return the same id.
select is(
  app.create_source_record(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'radar:test', 'news', 'https://example.com/a',
    now(),
    'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
    null, 'en'),
  app.create_source_record(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'radar:test', 'news', 'https://example.com/a',
    now(),
    'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
    null, 'en'),
  '4. duplicate content_hash returns same source_record id'
);

-- ── 5. Cross-workspace isolation: Beta org cannot read Alpha radars ──────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c05","role":"authenticated"}', true);
select is(
  (select count(*)::int from public.radar
   where id = (select v from radar_ids where k = 'r1')),
  0,
  '5. beta org user cannot see alpha org radars'
);
reset role;

-- ── 6. update_radar_cursor blocked for authenticated role ────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c02","role":"authenticated"}', true);
select throws_ok(
  format($q$select app.update_radar_cursor(%L::uuid, '{}'::jsonb, 0, 0)$q$,
    (select v from radar_ids where k = 'r1')),
  null, null,
  '6. authenticated role cannot call app.update_radar_cursor directly'
);
reset role;

-- ── 7. upsert_radar_queries stores queries via service_role ──────────────────
-- Call at postgres level (no JWT set).
select lives_ok(
  format($q$select app.upsert_radar_queries(%L::uuid, '[{"query_text":"CAD software","language":"en","source":"hypothesis"}]'::jsonb)$q$,
    (select v from radar_ids where k = 'r1')),
  '7. upsert_radar_queries inserts queries for radar'
);
select is(
  (select count(*)::int from public.radar_query where radar_id = (select v from radar_ids where k = 'r1')),
  1,
  '7b. radar_query row inserted'
);

-- ── 8. mark_radar_silent records health incident ─────────────────────────────
select lives_ok(
  format($q$select app.mark_radar_silent(%L::uuid)$q$,
    (select v from radar_ids where k = 'r1')),
  '8. mark_radar_silent executes without error'
);
select is(
  (select status from public.radar where id = (select v from radar_ids where k = 'r1')),
  'silent',
  '8b. radar status set to silent'
);

-- ── 9. remove_radar: Editor can remove ───────────────────────────────────────
-- Create a second radar, then remove it.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c02","role":"authenticated"}', true);
insert into radar_ids select 'r2', public.create_radar(
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'brief'),
  'website', 'Co Websites', '{}'::jsonb
);
select lives_ok(
  format($q$select public.remove_radar(%L::uuid)$q$, (select v from radar_ids where k = 'r2')),
  '9. editor can remove radar'
);
reset role;
select is(
  (select count(*)::int from public.radar where id = (select v from radar_ids where k = 'r2')
   and status <> 'paused'),
  0,
  '9b. removed radar is no longer active'
);

-- ── 10. Viewer can SELECT radar (RLS allows reads) ───────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000c03","role":"authenticated"}', true);
select ok(
  (select count(*)::int from public.radar
   where id = (select v from radar_ids where k = 'r1')) = 1,
  '10. viewer can read radar from their org'
);
reset role;

select * from finish();
rollback;
