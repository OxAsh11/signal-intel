begin;
create extension if not exists pgtap with schema extensions;
select plan(8);

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000b', 'b@beta.test',  'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select lives_ok($$ select public.create_organisation('Alpha Engineering') $$, 'user A creates an organisation');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
select lives_ok($$ select public.create_organisation('Beta Cold Storage') $$, 'user B creates an organisation');

-- As B: only Beta is visible.
select results_eq($$ select name from public.organisation $$, $$ values ('Beta Cold Storage') $$, 'B sees only its organisation');
select is((select count(*)::int from public.workspace), 1, 'B sees only its workspace');
select is((select count(*)::int from public.audit_log where org_id not in (select id from public.organisation)), 0, 'B sees no foreign audit rows');

-- As A.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select results_eq($$ select name from public.organisation $$, $$ values ('Alpha Engineering') $$, 'A sees only its organisation');
select is((select count(*)::int from public.member), 1, 'A sees only its own members');
select throws_ok($$ select count(*) from public.outbox $$, '42501', null, 'outbox is not readable by clients');

select * from finish();
rollback;
