begin;
create extension if not exists pgtap with schema extensions;
select plan(7);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000b5030', 'owner@suite18.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5030","role":"authenticated"}', true);
select public.create_organisation('Suite 18 Compliance Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 18 Compliance Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 18 Compliance Corp';

set local role service_role;

-- Person entity for DSR tests
insert into ids select 'person_ent', app.create_entity(
  (select v from ids where k = 'org'), 'person', 'Suite 18 Test Person', 'tenant'
);

-- Brief for compute_metric (function takes brief_id, not brief_version_id)
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values ((select v from ids where k = 'brief'),
          (select v from ids where k = 'org'),
          (select v from ids where k = 'ws'),
          'Suite 18 Brief', 'Draft', 'done');

-- metric_definition global seeds are already inserted by the phase3_activation migration
-- (org_id=NULL, key='accounts_discovered', published_at IS NOT NULL).
-- No fixture insert needed; compute_metric will find the global seed.

reset role;

-- ── Assertion 1: app.process_dsr_deletion exists ─────────────────────────────
select has_function('app', 'process_dsr_deletion', 'app.process_dsr_deletion exists');

-- ── Assertion 2: app.compute_metric exists ───────────────────────────────────
select has_function('app', 'compute_metric', 'app.compute_metric exists');

-- ── Assertion 3: process_dsr_deletion runs without error ─────────────────────
set local role service_role;
select lives_ok(
  $$select app.process_dsr_deletion(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'person_ent')
  )$$,
  'process_dsr_deletion runs without error'
);

-- ── Assertion 4: process_dsr_deletion adds person to suppression_list ─────────
select ok(
  (select exists (
    select 1 from public.suppression_list
    where org_id = (select v from ids where k = 'org')
      and person_entity_id = (select v from ids where k = 'person_ent')
  )),
  'process_dsr_deletion adds person to suppression_list'
);

-- ── Assertion 5: process_dsr_deletion anonymises entity display_name ──────────
-- The function sets display_name = '[deleted]' (not null)
select ok(
  (select display_name = '[deleted]'
   from public.entity
   where id = (select v from ids where k = 'person_ent')),
  'process_dsr_deletion sets entity display_name to [deleted]'
);

-- ── Assertion 6: compute_metric returns a jsonb result for 'accounts_discovered'
-- Actual signature: compute_metric(workspace_id, brief_id, metric_key, filters)
-- Uses the global canonical seed (org_id=NULL) inserted by the migration.
select ok(
  (select app.compute_metric(
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'brief'),
    'accounts_discovered',
    '{}'::jsonb
  ) is not null),
  'compute_metric returns a jsonb result for accounts_discovered'
);

-- ── Assertion 7: compute_metric returns NULL for unknown metric key ───────────
select ok(
  (select app.compute_metric(
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'brief'),
    'nonexistent_metric_key_b503',
    '{}'::jsonb
  ) is null),
  'compute_metric returns NULL for unknown metric key'
);
reset role;

select * from finish();
rollback;
