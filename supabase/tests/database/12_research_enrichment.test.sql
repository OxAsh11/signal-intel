begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Users for auth
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000b200', 'owner@research.test',  'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000b201', 'viewer@research.test', 'authenticated', 'authenticated');

-- Create Suite 12 organisation
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b200","role":"authenticated"}', true);
select public.create_organisation('Suite 12 Research Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 12 Research Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 12 Research Corp';

-- Members and roles
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-00000000b201', (select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b201', 'viewer@research.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-00000000b201', (select v from ids where k = 'ws'), 'Viewer');

-- Brief + brief_version
insert into ids select 'brief', gen_random_uuid();
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
  values (
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    'Suite 12 Brief', 'Draft', 'done'
  );
insert into ids select 'bv', gen_random_uuid();
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights)
  values (
    (select v from ids where k = 'bv'),
    (select v from ids where k = 'brief'),
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    1,
    '{"offer":"research suite"}'::jsonb,
    '{}'::jsonb,
    '{}'::jsonb
  );

-- Entity + account
set local role service_role;
insert into ids select 'ent', app.create_entity(
  (select v from ids where k = 'org'), 'company', 'Suite 12 Alpha Corp', 'tenant'
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

-- ── 1. app.create_research_doc exists ────────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'create_research_doc'),
  'app.create_research_doc exists'
);

-- ── 2. app.create_contact_point exists ───────────────────────────────────────
select ok(
  (select count(*) > 0 from pg_proc p
   join pg_namespace n on n.oid = p.pronamespace
   where n.nspname = 'app' and p.proname = 'create_contact_point'),
  'app.create_contact_point exists'
);

-- ── 3. create_research_doc returns uuid AND emits research_doc.published ─────
set local role service_role;
insert into ids select 'rdoc', app.create_research_doc(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'acct'),
  (select v from ids where k = 'bv'),
  (select v from ids where k = 'ent'),
  1,
  '{"summary":"suite 12 research"}'::jsonb,
  '{}'::uuid[],
  '{}'::jsonb
);
reset role;
select ok(
  (select v from ids where k = 'rdoc') is not null
  and exists(
    select 1 from public.outbox
    where event          = 'research_doc.published'::public.event_type
      and aggregate_type = 'research_doc'
      and payload->>'doc_id' = (select v from ids where k = 'rdoc')::text
  ),
  'create_research_doc returns uuid and emits research_doc.published'
);

-- ── 4. create_contact_point with status=valid emits contact_point.verified ───
set local role service_role;
insert into ids select 'cp1', app.create_contact_point(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'ent'),
  (select v from ids where k = 'acct'),
  'email',
  'enc:suite12.alice@example.com',
  'valid'::public.contact_verification_status,
  now(),
  'suite12-provider'
);
reset role;
select ok(
  exists(
    select 1 from public.outbox
    where event          = 'contact_point.verified'::public.event_type
      and aggregate_type = 'contact_point'
      and payload->>'contact_point_id' = (select v from ids where k = 'cp1')::text
  ),
  'contact_point.verified emitted for status=valid contact point'
);

-- ── 5. create_contact_point with status=unknown does NOT emit verified ────────
set local role service_role;
insert into ids select 'cp2', app.create_contact_point(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  (select v from ids where k = 'ent'),
  (select v from ids where k = 'acct'),
  'phone',
  'enc:suite12.555-0100',
  'unknown'::public.contact_verification_status,
  null,
  null
);
reset role;
select ok(
  not exists(
    select 1 from public.outbox
    where event          = 'contact_point.verified'::public.event_type
      and aggregate_type = 'contact_point'
      and payload->>'contact_point_id' = (select v from ids where k = 'cp2')::text
  ),
  'no contact_point.verified event emitted for status=unknown contact point'
);

-- ── 6. Second create_contact_point with same key returns same uuid (upsert) ──
set local role service_role;
select is(
  app.create_contact_point(
    (select v from ids where k = 'org'),
    (select v from ids where k = 'ws'),
    (select v from ids where k = 'ent'),
    (select v from ids where k = 'acct'),
    'email',
    'enc:suite12.alice@example.com',
    'valid'::public.contact_verification_status,
    now(),
    'suite12-provider'
  ),
  (select v from ids where k = 'cp1'),
  'second create_contact_point with same (entity_id, type, value_encrypted) returns same uuid'
);
reset role;

-- ── 7. contact_point.scope is tenant on insertion ────────────────────────────
select ok(
  (select scope from public.contact_point where id = (select v from ids where k = 'cp1'))
    = 'tenant'::public.data_scope,
  'contact_point.scope is tenant'
);

-- ── 8. Direct INSERT into public.research_doc blocked for authenticated ───────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000b201","role":"authenticated"}', true);
select throws_ok(
  $q$
    INSERT INTO public.research_doc
      (org_id, workspace_id, account_id, brief_version_id, version, content, source_record_ids, inputs)
    VALUES (
      (SELECT v FROM ids WHERE k = 'org'),
      (SELECT v FROM ids WHERE k = 'ws'),
      (SELECT v FROM ids WHERE k = 'acct'),
      (SELECT v FROM ids WHERE k = 'bv'),
      2,
      '{"summary":"blocked"}'::jsonb,
      '{}'::uuid[],
      '{}'::jsonb
    )
  $q$,
  '42501', null,
  'authenticated direct INSERT into research_doc denied'
);
reset role;

select * from finish();
rollback;
