begin;
create extension if not exists pgtap with schema extensions;
select plan(3);

insert into auth.users (id, email, aud, role)
  values ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select public.create_organisation('Alpha Engineering');
reset role;

select is(
  (select count(*)::int from public.audit_log
     where org_id = (select id from public.organisation where name = 'Alpha Engineering')),
  2,
  'organisation + workspace creation are audited'
);
select throws_ok($$ update public.audit_log set action = 'tampered' $$, '42501', null, 'audit_log rejects UPDATE');
select throws_ok($$ delete from public.audit_log $$, '42501', null, 'audit_log rejects DELETE');

select * from finish();
rollback;
