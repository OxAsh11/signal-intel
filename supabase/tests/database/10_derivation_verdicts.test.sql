begin;
create extension if not exists pgtap with schema extensions;
select plan(14);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Users for auth
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-000000000e00', 'owner@deriv.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000e01', 'editor@deriv.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000e02', 'viewer@deriv.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000e03', 'ob2@deriv.test',    'authenticated', 'authenticated');

-- Create EI Deriv Alpha and EI Deriv Beta organisations.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000e00","role":"authenticated"}', true);
select public.create_organisation('EI Deriv Alpha');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000e03","role":"authenticated"}', true);
select public.create_organisation('EI Deriv Beta');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org',   id from public.organisation where name = 'EI Deriv Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'EI Deriv Beta';
insert into ids select 'ws',    w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Deriv Alpha';
insert into ids select 'ws_b',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Deriv Beta';

-- Members and roles for EI Deriv Alpha
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-000000000e01', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000e01', 'editor@deriv.test'),
  ('00000000-0000-4000-8000-000000000e02', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000e02', 'viewer@deriv.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000e01', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000e02', (select v from ids where k = 'ws'), 'Viewer');

-- Brief + brief_version #1 (direct insert at service_role; no guard trigger on these tables)
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Test Brief D10', 'Draft', 'done'
  );
insert into ids select 'bv', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv'),
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer": "test offer"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Brief + brief_version #2 (for assertion 7: Confirmed + empty claims → 23514)
insert into ids select 'brief2', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief2'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Test Brief D10b', 'Draft', 'done'
  );
insert into ids select 'bv2', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv2'),
    (select v from ids where k = 'brief2'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer": "test offer 2"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Entity in EI Deriv Alpha (service_role context via app.create_entity)
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Derivation Corp', 'tenant'
);

-- Entity in EI Deriv Beta (for cross-org test in assertion 8)
insert into ids select 'ent_b', app.create_entity(
  (select v from ids where k = 'org_b'), 'company', 'Beta Deriv Corp', 'tenant'
);

-- A fixed doc_id for memory_chunk conflict-key tests (assertions 9 + 10)
insert into ids select 'doc1', gen_random_uuid();

-- A predicate, source record, loser entity, and extractor version for assertion 14 (claim merge).
insert into public.predicate (key, label, value_type)
  values ('revenue.d10', 'Revenue D10', 'number');
insert into ids select 'pred10', id from public.predicate where key = 'revenue.d10';

insert into ids select 'src10', app.create_source_record(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  'test-connector', 'news', 'https://deriv-test.example.com/article1',
  now(), 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb10', null, 'en'
);

insert into ids select 'loser', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Loser Corp D10', 'tenant'
);

-- extractor_version required by create_claim (NOT NULL).
insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('extract.d10test', '0.1.0', 'claude-test', '0.1.0');
insert into ids select 'extver10', id from public.extractor_version
  where capability = 'extract.d10test';

-- Claim on the loser entity (will be re-pointed to winner by merge).
-- value is a JSON number (predicate value_type = 'number'); confidence cast to smallint.
insert into ids select 'claim10', app.create_claim(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'loser'),
  (select v from ids where k = 'pred10'),
  '100'::jsonb,
  null, null, null,
  (select v from ids where k = 'src10'),
  now(), (select v from ids where k = 'extver10'), 80::smallint
);

-- ── 1. app.upsert_verdict exists ─────────────────────────────────────────────
select has_function(
  'app', 'upsert_verdict',
  ARRAY['uuid','uuid','public.verdict_value','uuid[]','jsonb','jsonb'],
  'app.upsert_verdict exists'
);

-- ── 2. app.upsert_memory_chunk exists ────────────────────────────────────────
select has_function(
  'app', 'upsert_memory_chunk',
  ARRAY['uuid','uuid','uuid','uuid','text','uuid','integer','text','extensions.vector','text','public.data_scope','timestamp with time zone'],
  'app.upsert_memory_chunk exists'
);

-- ── 3. Direct INSERT into public.verdict blocked by guard trigger (42501) ─────
-- app.writer is not set, so the BEFORE trigger raises an exception.
select throws_ok(
  $q$
    INSERT INTO public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids, quotes, inputs)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'bv'),
      (SELECT v FROM ids WHERE k = 'ent'),
      'Likely'::public.verdict_value,
      '{}',
      '[]'::jsonb,
      '{}'::jsonb
    )
  $q$,
  '42501', null,
  'direct insert into verdict denied without derivation writer'
);

-- ── 4. Direct INSERT into public.memory_chunk blocked by guard trigger (42501) ─
select throws_ok(
  $q$
    INSERT INTO public.memory_chunk (org_id, workspace_id, doc_type, doc_id, chunk_index, content,
                                      embedding, embedding_model, scope)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      'source_record',
      (SELECT v FROM ids WHERE k = 'doc1'),
      0,
      'test content guard',
      ('[' || repeat('0.1,', 1023) || '0.1]')::extensions.vector,
      'test-model',
      'tenant'::public.data_scope
    )
  $q$,
  '42501', null,
  'direct insert into memory_chunk denied without derivation writer'
);

-- ── 5. app.upsert_verdict with valid inputs returns UUID ──────────────────────
select isnt_empty(
  $q$
    SELECT app.upsert_verdict(
      (SELECT v FROM ids WHERE k = 'bv'),
      (SELECT v FROM ids WHERE k = 'ent'),
      'Likely'::public.verdict_value,
      '{}',
      '[]'::jsonb,
      '{}'::jsonb
    )
  $q$,
  'upsert_verdict returns a uuid'
);

-- ── 6. app.upsert_verdict ON CONFLICT DO UPDATE returns UUID ──────────────────
select isnt_empty(
  $q$
    SELECT app.upsert_verdict(
      (SELECT v FROM ids WHERE k = 'bv'),
      (SELECT v FROM ids WHERE k = 'ent'),
      'Confirmed'::public.verdict_value,
      ARRAY[gen_random_uuid()],
      '[]'::jsonb,
      '{}'::jsonb
    )
  $q$,
  'upsert_verdict upserts on conflict'
);

-- ── 7. Confirmed + empty claim_ids raises 23514 (confirmed_has_claims CHECK) ──
-- Use bv2 + ent (no existing row) so the INSERT fires fresh and CHECK is evaluated.
select throws_ok(
  $q$
    SELECT app.upsert_verdict(
      (SELECT v FROM ids WHERE k = 'bv2'),
      (SELECT v FROM ids WHERE k = 'ent'),
      'Confirmed'::public.verdict_value,
      '{}',
      '[]'::jsonb,
      '{}'::jsonb
    )
  $q$,
  '23514', null,
  'confirmed verdict with empty claims raises check violation'
);

-- ── 8. Cross-org entity raises 42501 ─────────────────────────────────────────
-- bv belongs to org; ent_b belongs to org_b — mismatch triggers the guard.
select throws_ok(
  $q$
    SELECT app.upsert_verdict(
      (SELECT v FROM ids WHERE k = 'bv'),
      (SELECT v FROM ids WHERE k = 'ent_b'),
      'Likely'::public.verdict_value,
      '{}',
      '[]'::jsonb,
      '{}'::jsonb
    )
  $q$,
  '42501', null,
  'upsert_verdict with cross-org entity raises 42501'
);

-- ── 9. app.upsert_memory_chunk with valid inputs returns UUID ─────────────────
select isnt_empty(
  $q$
    SELECT app.upsert_memory_chunk(
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'ent'),
      (SELECT v FROM ids WHERE k = 'brief'),
      'source_record',
      (SELECT v FROM ids WHERE k = 'doc1'),
      0,
      'test content first',
      ('[' || repeat('0.1,', 1023) || '0.1]')::extensions.vector,
      'test-model',
      'tenant'::public.data_scope,
      now()
    )
  $q$,
  'upsert_memory_chunk returns a uuid'
);

-- ── 10. app.upsert_memory_chunk ON CONFLICT DO UPDATE returns UUID ────────────
-- Same (doc_type, doc_id, chunk_index, embedding_model) — different content.
select isnt_empty(
  $q$
    SELECT app.upsert_memory_chunk(
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'ent'),
      (SELECT v FROM ids WHERE k = 'brief'),
      'source_record',
      (SELECT v FROM ids WHERE k = 'doc1'),
      0,
      'test content updated',
      ('[' || repeat('0.2,', 1023) || '0.2]')::extensions.vector,
      'test-model',
      'tenant'::public.data_scope,
      now()
    )
  $q$,
  'upsert_memory_chunk upserts on conflict'
);

-- ── 11. Verdict RLS: Viewer can SELECT from public.verdict ────────────────────
-- After assertions 5+6 there is 1 verdict row for (bv, ent) in ws.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000e02","role":"authenticated"}', true);
select is(
  (select count(*)::int from public.verdict where workspace_id = (select v from ids where k = 'ws')),
  1,
  'viewer can read verdict in own workspace'
);
reset role;

-- ── 12. public.outbox has a verdict.changed row after app.upsert_verdict ──────
-- At this point assertion 5 has emitted one verdict.changed event.
-- Assertion 6's upsert also emits one, so count >= 1.
select ok(
  (
    select count(*)::int
    from public.outbox
    where event = 'verdict.changed'::public.event_type
      and aggregate_type = 'verdict'
  ) > 0,
  'outbox has verdict.changed event after upsert_verdict'
);

-- ── 13. ON CONFLICT: only one verdict row for same (bv, ent) ─────────────────
-- Assertions 5 (insert Likely) and 6 (upsert Confirmed) both used (bv, ent).
select is(
  (
    select count(*)::int
    from public.verdict
    where brief_version_id = (select v from ids where k = 'bv')
      and entity_id        = (select v from ids where k = 'ent')
  ),
  1,
  'upsert_verdict ON CONFLICT updated not duplicated — count is 1'
);

-- ── 14. app.merge_entities re-points claims to winner (migration 0021 bypass) ─
-- Run merge first (its own statement so the UPDATE is visible to the next read).
select app.merge_entities(
  (select v from ids where k = 'ent'),
  (select v from ids where k = 'loser'),
  null
);

-- Now assert: claim10's entity_id must be ent, not loser.
select is(
  (
    select entity_id from public.claim
    where id = (select v from ids where k = 'claim10')
  ),
  (select v from ids where k = 'ent'),
  'merge_entities re-points claims to winner entity'
);

select * from finish();
rollback;
