begin;
create extension if not exists pgtap with schema extensions;
select plan(75);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- A owns Alpha, E is an Editor, K a Viewer, F is signed in but a member of nothing.
-- Acme is a company with an account; Jane and Mike work there (Mike is flagged), three other
-- people do not. One provider run records two attempts and settles one ledger line.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000028a1', 'a@rp.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000028a2', 'k@rp.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000028a3', 'e@rp.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000028a4', 'f@rp.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a1","role":"authenticated"}', true);
select public.create_organisation('RP Alpha', 'RP WS');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'RP Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'RP Alpha';
insert into ids select 'm_a', id from public.member where user_id = '00000000-0000-4000-8000-0000000028a1';
insert into ids values ('m_e', '00000000-0000-4000-8000-0000000028a3');
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000028a3', (select v from ids where k = 'org'),
   '00000000-0000-4000-8000-0000000028a3', 'e@rp.test'),
  ('00000000-0000-4000-8000-0000000028a2', (select v from ids where k = 'org'),
   '00000000-0000-4000-8000-0000000028a2', 'k@rp.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000028a3', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000028a2', (select v from ids where k = 'ws'), 'Viewer');
create function pg_temp.id(p text) returns uuid language sql as $$ select v from ids where k = p $$;
create function pg_temp.w() returns uuid language sql as $$ select v from ids where k = 'ws' $$;

-- Company, its account, and a derived source for the works_at links.
insert into ids select 'ec', app.create_entity(pg_temp.id('org'), 'company', 'RP Acme', 'tenant');
select set_config('app.writer', 'derivation', true);
insert into public.account (org_id, workspace_id, entity_id)
values (pg_temp.id('org'), pg_temp.w(), pg_temp.id('ec'));
select set_config('app.writer', '', true);
insert into ids select 'ac', id from public.account where entity_id = pg_temp.id('ec');
insert into ids select 'src', app.derived_source_record(pg_temp.w(), 'manual');

insert into ids select 'ep', app.create_entity(pg_temp.id('org'), 'person', 'Jane Roe', 'tenant');
insert into ids select 'ep2', app.create_entity(pg_temp.id('org'), 'person', 'Mike Mumbai', 'tenant');
insert into ids select 'eu', app.create_entity(pg_temp.id('org'), 'person', 'Orphan One', 'tenant');
insert into ids select 'eu2', app.create_entity(pg_temp.id('org'), 'person', 'Orphan Two', 'tenant');
insert into ids select 'eu3', app.create_entity(pg_temp.id('org'), 'person', 'Orphan Three', 'tenant');
insert into public.relationship
  (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes, scope)
values
  (pg_temp.id('org'), pg_temp.id('ep'), 'works_at', pg_temp.id('ec'), pg_temp.id('src'), 90,
   '{"title":"VP Eng"}', 'tenant'),
  (pg_temp.id('org'), pg_temp.id('ep2'), 'works_at', pg_temp.id('ec'), pg_temp.id('src'), 90,
   '{"title":"Sales Lead"}', 'tenant');

-- Only ciphertext and a mask are stored; SQL has no decryption key.
insert into public.contact_point
  (org_id, workspace_id, entity_id, type, value_encrypted, status, provider, masked_hint, value_hash)
values
  (pg_temp.id('org'), pg_temp.w(), pg_temp.id('ep'), 'email', 'enc:v1:jane', 'valid', 'leadmagic',
   'j•••@acme.com', 'rp-hash-jane'),
  (pg_temp.id('org'), pg_temp.w(), pg_temp.id('ep2'), 'email', 'enc:v1:mike', 'unknown', 'findymail',
   'm•••@acme.com', 'rp-hash-mike');
insert into public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
values (pg_temp.id('org'), pg_temp.id('ep'), 'profile_url', 'linkedin.com/in/jane', 90, pg_temp.id('src'));
insert into public.entity_flag (entity_id, workspace_id, kind, reason)
values (pg_temp.id('ep2'), pg_temp.w(), 'location_mismatch',
        'Possibly a different office (Mumbai vs Delhi)');

-- One provider column, one running run, two attempts and one settled ledger line.
insert into public.predicate (key, org_id, workspace_id, label, value_type, fill_method)
values ('rp_email', pg_temp.id('org'), pg_temp.w(), 'Work email', 'text', 'provider');
insert into ids select 'pr', id from public.predicate where key = 'rp_email';
insert into ids values ('r1', gen_random_uuid());
insert into public.predicate_run
  (id, org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, started_at)
values (pg_temp.id('r1'), pg_temp.id('org'), pg_temp.w(), pg_temp.id('ep'), pg_temp.id('pr'), 1,
        'running', now() - interval '1 hour', now() - interval '1 hour');
insert into public.usage_ledger
  (org_id, workspace_id, capability, provider, unit, quantity, cost_usd, status, predicate_run_id)
values (pg_temp.id('org'), pg_temp.w(), 'extract', 'anthropic', 'ai_input_tokens', 1000, 0.5, 'ok', pg_temp.id('r1'));
select app.record_attempt(pg_temp.id('r1'), 'leadmagic', 'found', 0.012, null, null, 90, 'lm-1', 1);
select app.record_attempt(pg_temp.id('r1'), 'findymail', 'error', 0.004, 'rejected the key', null, null, null, 0, 'vendor');
select app.finish_run(pg_temp.id('r1'), 'succeeded');

-- ── Shapes and enums ─────────────────────────────────────────────────────────
select is((select string_agg(a.enumlabel, ',' order by a.enumsortorder)
             from pg_enum a join pg_type t on t.oid = a.enumtypid
             join pg_namespace n on n.oid = t.typnamespace
            where n.nspname = 'public' and t.typname = 'alias_type'),
  'registry_id,domain,profile_url,name_country,email,phone,site', '1. alias_type has people and site values');
select is((select count(*)::int from public.model_price
            where model in ('leadmagic','findymail','findymail_verify','prospeo','pdl_company','pdl_person')
              and unit = 'enrichment_lookup'), 6, '2. all six provider prices are seeded');
select is((select string_agg(k, ',' order by k) from jsonb_object_keys(
            (select to_jsonb(a) from public.predicate_run_attempt a limit 1)) k),
  'billable_units,confidence,cost_usd,created_at,error,error_kind,id,ordinal,org_id,provider,raw_ref,run_id,status,workspace_id',
  '3. an attempt records the vendor, order, result and cost');
select is((select count(*)::int from public.provider_cache), 0,
  '4. the cache is empty until a payload is stored');

-- ── Attempts and run cost ────────────────────────────────────────────────────
select is((select count(*)::int from public.predicate_run_attempt where run_id = pg_temp.id('r1')), 2,
  '5. both waterfall attempts are recorded');
select is((select string_agg(provider || ':' || ordinal || ':' || status || ':' || coalesce(error_kind, '-'), ','
                             order by ordinal)
             from public.predicate_run_attempt where run_id = pg_temp.id('r1')),
  'leadmagic:1:found:-,findymail:2:error:vendor', '6. ordinals and error kinds');
select is((select cost_usd from public.predicate_run where id = pg_temp.id('r1')), 0.516,
  '7. a run costs its settled ledger line plus its attempts');
select is((select status from public.predicate_run where id = pg_temp.id('r1')), 'succeeded',
  '8. finishing the run succeeded');
select ok(pg_temp.id('r1') is null or (select count(*)::int from public.predicate_run_attempt
            where run_id = pg_temp.id('r1') and cost_usd > 0) = 2,
  '9. both attempts carry a cost');
select throws_ok(format('select app.record_attempt(%L::uuid, %L, %L, 1, null, 1)',
                        pg_temp.id('r1'), 'prospeo', 'found'), '23505', null,
  '10. an ordinal cannot be reused in a run');
select throws_ok(format('select app.record_attempt(%L::uuid, %L, %L)',
                        pg_temp.id('r1'), 'prospeo', 'maybe'), '22023', null,
  '11. an unknown attempt status is refused');

-- ── Provider cache ───────────────────────────────────────────────────────────
select app.store_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ec'),
                                  '{"employees":42,"industry":"robotics"}', 'pdl-ref-1', 30);
select is(app.cached_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ec')) -> 'employees', '42'::jsonb,
  '12. a fresh payload is returned');
select is(app.cached_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ep')), null,
  '13. a different entity has no payload');
update public.provider_cache set expires_at = now() - interval '1 minute'
 where workspace_id = pg_temp.w() and provider = 'pdl_company' and entity_id = pg_temp.id('ec');
select is(app.cached_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ec')), null,
  '14. an expired payload is not a hit');
select app.store_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ec'), '{"employees":43}', null, 30);
select is(app.cached_provider_payload(pg_temp.w(), 'pdl_company', pg_temp.id('ec')) -> 'employees', '43'::jsonb,
  '15. storing again refreshes the payload');
select throws_ok(format('select app.store_provider_payload(%L::uuid, %L, %L::uuid, %L::jsonb, null, 0)',
                        pg_temp.w(), 'pdl_company', pg_temp.id('ec'), '{}'), '22023', null,
  '16. a cache TTL must be positive');

-- ── Unlinked people go to Review ─────────────────────────────────────────────
insert into ids select 'ri1', app.queue_unlinked_person(pg_temp.id('eu'), pg_temp.w());
insert into ids select 'ri2', app.queue_unlinked_person(pg_temp.id('eu2'), pg_temp.w());
insert into ids select 'ri3', app.queue_unlinked_person(pg_temp.id('eu3'), pg_temp.w());
select is((select type from public.review_item where id = pg_temp.id('ri1')), 'unlinked_person',
  '17. an unlinked person opens an unlinked_person Review item');
select is((select person_entity_id from public.review_item where id = pg_temp.id('ri1')), pg_temp.id('eu'),
  '18. the item names the person');
select is(app.queue_unlinked_person(pg_temp.id('eu'), pg_temp.w()), pg_temp.id('ri1'),
  '19. queueing twice reuses the pending item');
select is(app.queue_unlinked_person(pg_temp.id('ep'), pg_temp.w()), null,
  '20. a linked person needs no Review item');
select is((select count(*)::int from public.review_item where type = 'unlinked_person'), 3,
  '21. each unlinked person has one item');
select throws_ok($$insert into public.review_item (org_id, workspace_id, type)
                   select id, (select v from ids where k = 'ws'), 'unlinked_person' from public.organisation
                   where name = 'RP Alpha'$$, '23514', null,
  '22. an unlinked_person item must name a person');

-- ── Reads as K, a Viewer ─────────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a2","role":"authenticated"}', true);
select is((select count(*)::int from public.predicate_run_attempt where run_id = pg_temp.id('r1')), 2,
  '23. a Viewer can read attempts');
select is((select count(*)::int from public.provider_cache), 1, '24. a Viewer can read the cache');
select is((select count(*)::int from public.entity_flag), 1, '25. a Viewer can read flags');
select throws_ok(format('select public.person_email(%L::uuid, %L::uuid)', pg_temp.w(), pg_temp.id('ep')),
  '42501', 'insufficient role', '26. a Viewer cannot reveal a work email');
select throws_ok(format('select public.link_person_to_account(%L::uuid, %L::uuid, %L::uuid)',
                        pg_temp.w(), pg_temp.id('eu'), pg_temp.id('ac')),
  '42501', 'insufficient role', '27. a Viewer cannot link a person');
select throws_ok(format('select public.set_provider_order(%L::uuid, %L::jsonb)', pg_temp.w(), '[]'::jsonb),
  '42501', 'insufficient role', '28. a Viewer cannot change the waterfall order');
select is((select string_agg(r ->> 'key', ',' order by r ->> 'key')
             from jsonb_array_elements(public.provider_status(pg_temp.w())) r),
  'findymail,findymail_verify,leadmagic,pdl_company,pdl_person,prospeo',
  '29. provider_status lists every provider');
select is((select (r ->> 'attempts')::int from jsonb_array_elements(public.provider_status(pg_temp.w())) r
            where r ->> 'key' = 'leadmagic'), 1, '30. provider_status counts attempts');
select is((select (r ->> 'month_spend_usd')::numeric from jsonb_array_elements(public.provider_status(pg_temp.w())) r
            where r ->> 'key' = 'findymail'), 0.004, '31. provider_status sums this month''s spend');
select is((select r ->> 'last_error' from jsonb_array_elements(public.provider_status(pg_temp.w())) r
            where r ->> 'key' = 'findymail'), 'rejected the key', '32. provider_status shows the last error');

-- ── The People operating table ───────────────────────────────────────────────
create function pg_temp.people(l int default 50, o int default 0) returns jsonb language sql as $$
  select public.account_table(pg_temp.w(), null, null, null, null, l, o, 'person') $$;
select is(pg_temp.people() -> 'total', '2'::jsonb, '33. only linked people are in the People table');
select is((select string_agg(r ->> 'name', ',' order by r ->> 'name')
             from jsonb_array_elements(pg_temp.people() -> 'rows') r),
  'Jane Roe,Mike Mumbai', '34. the two people of Acme');
select is((select r ->> 'work_email' from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Jane Roe'), 'j•••@acme.com', '35. the table shows a masked email');
select is((select r ->> 'account_name' from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Jane Roe'), 'RP Acme', '36. each row carries its account');
select is((select r ->> 'title' from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Jane Roe'), 'VP Eng', '37. the role comes from works_at');
select is((select r ->> 'linkedin' from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Jane Roe'), 'linkedin.com/in/jane', '38. the LinkedIn alias');
select is((select (r ->> 'location_mismatch')::boolean from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Mike Mumbai'), true, '39. the other-office flag is shown');
select is((select (r ->> 'location_mismatch')::boolean from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ->> 'name' = 'Jane Roe'), false, '40. an unflagged person is false, not null');
select is((select count(*)::int from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r::text like '%enc:v1%'), 0, '41. no ciphertext is in the table response');
select is((select count(*)::int from jsonb_array_elements(pg_temp.people() -> 'rows') r
            where r ? 'account_id' and r ? 'entity_id' and r ? 'cells'), 2,
  '42. person rows keep the operating-table shape');
select is((select count(*)::int from public.view_entities(pg_temp.w(), null, null, null, 50, 0, 'person')), 2,
  '43. view_entities has a person mode');
select throws_ok(format('select * from public.view_entities(%L::uuid, null, null, null, 10, 0, %L)',
                        pg_temp.w(), 'site'), '22023', null, '44. other entity types are still refused');
select is(public.account_table(pg_temp.w(), null, null, null, null, 50, 0) -> 'total', '1'::jsonb,
  '45. company mode is unchanged');
select is((public.account_table(pg_temp.w(), null, null, null, null, 50, 0) -> 'rows' -> 0) ? 'work_email', false,
  '46. company rows do not gain People fields');

-- ── Editor actions and grants ────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a3","role":"authenticated"}', true);
select is(public.person_email(pg_temp.w(), pg_temp.id('ep')) ->> 'masked', 'j•••@acme.com',
  '47. an Editor gets the masked contact');
select is(public.person_email(pg_temp.w(), pg_temp.id('ep')) ->> 'reveal', 'worker',
  '48. the full value is deferred to a worker action');
select is((public.person_email(pg_temp.w(), pg_temp.id('ep')) -> 'contact_point_id') is not null, true,
  '49. the panel gets the contact point id');
select is((select string_agg(k, ',' order by k) from jsonb_object_keys(
            public.person_email(pg_temp.w(), pg_temp.id('ep'))) k),
  'contact_point_id,masked,provider,reveal,status,verified_at', '50. person_email never returns a value');
select public.link_person_to_account(pg_temp.w(), pg_temp.id('eu'), pg_temp.id('ac'));
select is((select status from public.review_item where id = pg_temp.id('ri1')), 'linked',
  '51. a direct link resolves the Review item');
select is((select count(*)::int from public.relationship
            where from_entity_id = pg_temp.id('eu') and relation = 'works_at'
              and to_entity_id = pg_temp.id('ec')), 1, '52. the link is a works_at relationship');
select public.resolve_review_item(pg_temp.id('ri2'), 'link', null, pg_temp.id('ac'));
select is((select status from public.review_item where id = pg_temp.id('ri2')), 'linked',
  '53. Review Link resolves the item');
select public.resolve_review_item(pg_temp.id('ri3'), 'skip');
select is((select status from public.review_item where id = pg_temp.id('ri3')), 'skipped',
  '54. Review Dismiss resolves the item');
select throws_ok(format('select public.resolve_review_item(%L::uuid, %L, null, %L::uuid)',
                        pg_temp.id('ri3'), 'link', pg_temp.id('ac')),
  '23514', null, '55. a resolved item cannot be linked again');

-- ── Waterfall order (Admin) and grants ───────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a1","role":"authenticated"}', true);
select is((select provider_order from public.workspace_settings where workspace_id = pg_temp.w()),
  '["leadmagic","findymail","prospeo"]'::jsonb, '56. the default waterfall order');
select public.set_provider_order(pg_temp.w(), '["prospeo","leadmagic"]'::jsonb);
select is((select provider_order from public.workspace_settings where workspace_id = pg_temp.w()),
  '["prospeo","leadmagic"]'::jsonb, '57. an Admin can change the order');
select throws_ok(format('select public.set_provider_order(%L::uuid, %L::jsonb)',
                        pg_temp.w(), '["unknown"]'::jsonb),
  '22023', 'unknown provider in the order', '58. an unknown provider is refused');
select throws_ok(format('select public.set_provider_order(%L::uuid, %L::jsonb)',
                        pg_temp.w(), '["leadmagic","leadmagic"]'::jsonb),
  '22023', 'a provider may appear once', '59. a provider cannot appear twice');
select throws_ok(format('select public.set_provider_order(%L::uuid, %L::jsonb)', pg_temp.w(), '[]'::jsonb),
  '22023', 'provider order must be a non-empty list', '60. the order cannot be empty');
select ok(not has_function_privilege('authenticated', 'app.record_attempt(uuid, text, text, numeric, text, int, int, text, int, text)', 'execute')
          and has_function_privilege('service_role', 'app.record_attempt(uuid, text, text, numeric, text, int, int, text, int, text)', 'execute'),
  '61. recording an attempt is a worker write');
select ok(not has_function_privilege('authenticated', 'app.cached_provider_payload(uuid, text, uuid)', 'execute')
          and not has_function_privilege('authenticated', 'app.store_provider_payload(uuid, text, uuid, jsonb, text, int)', 'execute'),
  '62. the cache is a worker write');
select ok(has_function_privilege('authenticated', 'public.person_email(uuid, uuid)', 'execute')
          and not has_function_privilege('anon', 'public.person_email(uuid, uuid)', 'execute')
          and has_function_privilege('authenticated', 'public.resolve_review_item(uuid, text, text, uuid)', 'execute')
          and not has_function_privilege('anon', 'public.provider_status(uuid)', 'execute'),
  '63. client RPCs are signed-in only');

-- ── Find people: estimate, budget refusal and request ───────────────────────
-- The fixture has spent $0.50 of a $1 workspace day cap, leaving room for one $0.28 lookup.
reset role;
insert into public.budget (org_id, workspace_id, period, cap_usd)
values (pg_temp.id('org'), pg_temp.w(), 'day', 1.00);
select is((public.estimate_find_people(pg_temp.w(), pg_temp.id('ac'), 3) ->> 'cost_usd')::numeric,
  0.84, '67. an estimate is the limit times the person price');
select is((public.estimate_find_people(pg_temp.w(), pg_temp.id('ac'), 3) ->> 'per_person_usd')::numeric,
  0.28, '68. the price comes from model_price');
select is((public.estimate_find_people(pg_temp.w(), pg_temp.id('ac'), 3) ->> 'within_budget')::int,
  1, '69. the estimate names the largest count that fits');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a2","role":"authenticated"}', true);
select is((public.estimate_find_people(pg_temp.w(), pg_temp.id('ac'), 3) ->> 'count')::int, 3,
  '70. a Viewer can see the estimate');
select throws_ok(format('select public.request_find_people(%L::uuid, %L::uuid, 1, null)',
                         pg_temp.w(), pg_temp.id('ac')),
  '42501', 'permission denied', '71. a Viewer cannot ask to find people');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a3","role":"authenticated"}', true);
select throws_ok(format('select public.request_find_people(%L::uuid, %L::uuid, 3, null)',
                         pg_temp.w(), pg_temp.id('ac')),
  '55000', 'this run needs $0.84 but $0.50 is left in the budget; find the first 1 within budget',
  '72. a refusal names the first N that fit the budget');
select is((public.request_find_people(pg_temp.w(), pg_temp.id('ac'), 1, null) ->> 'cost_usd')::numeric,
  0.28, '73. an Editor can request one lookup within budget');
reset role;  -- the outbox is a worker table, never readable by a signed-in member
select is((select count(*)::int from public.outbox where event = 'people.find_requested'), 1,
  '74. the request announces exactly one find job');
select is((select payload ->> 'limit' from public.outbox where event = 'people.find_requested'),
  '1', '75. the event carries the requested limit');

-- ── Cross-workspace isolation ────────────────────────────────────────────────
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000028a4","role":"authenticated"}', true);
select is((select count(*)::int from public.predicate_run_attempt), 0,
  '64. a non-member sees no attempts');
select is((select count(*)::int from public.entity_flag), 0,
  '65. a non-member sees no flags');
select throws_ok(format('select public.provider_status(%L::uuid)', pg_temp.w()), '42501', 'permission denied',
  '66. a non-member cannot read provider status');

select * from finish();
rollback;
