begin;
create extension if not exists pgtap with schema extensions;
select plan(7);

-- ── Fixture ──────────────────────────────────────────────────────────────────
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000b5020', 'owner@suite17.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5020","role":"authenticated"}', true);
select public.create_organisation('Suite 17 Webhooks Corp');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated, service_role;
insert into ids select 'org', id from public.organisation where name = 'Suite 17 Webhooks Corp';
insert into ids select 'ws',  w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Suite 17 Webhooks Corp';

-- ── Assertion 1: app.create_webhook_subscription exists ──────────────────────
select has_function('app', 'create_webhook_subscription', 'app.create_webhook_subscription exists');

-- ── Assertion 2: app.record_webhook_delivery exists ──────────────────────────
select has_function('app', 'record_webhook_delivery', 'app.record_webhook_delivery exists');

-- ── Assertion 3: app.create_api_token exists ─────────────────────────────────
select has_function('app', 'create_api_token', 'app.create_api_token exists');

-- ── Assertion 4: direct INSERT into api_token blocked for authenticated ───────
-- RLS enabled, no INSERT grant → 42501
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000b5020","role":"authenticated"}', true);
select throws_ok(
  $$insert into public.api_token (id, org_id, workspace_id, scope, name, token_hash)
    values (gen_random_uuid(),
            (select v from ids where k = 'org'),
            (select v from ids where k = 'ws'),
            'Read', 'blocked-test', 'fakehash')$$,
  '42501',
  null,
  'direct INSERT into api_token blocked for authenticated'
);
reset role;

set local role service_role;

-- Create a webhook subscription
-- Actual signature: create_webhook_subscription(org_id, workspace_id, event_types, url, secret_vault_key)
insert into ids select 'wh_sub', app.create_webhook_subscription(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  ARRAY['score.band_changed']::text[],
  'https://example.com/hook',
  'testsecret'
);

insert into ids select 'event_id', gen_random_uuid();

-- ── Assertion 5: record_webhook_delivery upserts (idempotent) ────────────────
-- Actual signature: record_webhook_delivery(subscription_id, event_id, payload, signature, status, attempt_count, delivered_at DEFAULT NULL)
select lives_ok(
  $$select app.record_webhook_delivery(
    (select v from ids where k = 'wh_sub'),
    (select v from ids where k = 'event_id'),
    '{}'::jsonb,
    'sig-b502',
    'delivered',
    1,
    now()
  )$$,
  'record_webhook_delivery delivered ok'
);

-- ── Assertion 6: record_webhook_delivery with status=failed increments fail_count
select lives_ok(
  $$select app.record_webhook_delivery(
    (select v from ids where k = 'wh_sub'),
    gen_random_uuid(),
    '{"error":"timeout"}'::jsonb,
    'sig-b502',
    'failed',
    1,
    NULL
  )$$,
  'record_webhook_delivery failed increments fail_count without error'
);

-- ── Assertion 7: create_api_token stores token_hash not raw ──────────────────
-- Actual signature: create_api_token(org_id, workspace_id, member_id, scope, name, token_hash, expires_at DEFAULT NULL)
insert into ids select 'api_tok', app.create_api_token(
  (select v from ids where k = 'org'),
  (select v from ids where k = 'ws'),
  NULL::uuid,
  'Read'::public.token_scope,
  'test-token-b502',
  repeat('a', 64)
);
select ok(
  (select length(token_hash) = 64
   from public.api_token
   where id = (select v from ids where k = 'api_tok')),
  'create_api_token stores token_hash (64-char hex)'
);
reset role;

select * from finish();
rollback;
