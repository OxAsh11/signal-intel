begin;
create extension if not exists pgtap with schema extensions;
select plan(6);

select is_empty($$
  select table_name || ':' || grantee || ':' || privilege_type
  from information_schema.role_table_grants
  where table_schema = 'public' and grantee in ('anon', 'authenticated')
    and privilege_type in ('INSERT', 'UPDATE', 'DELETE', 'TRUNCATE') $$,
  'clients hold no write grants on public tables');

select is_empty($$
  select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
  where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity $$,
  'RLS is enabled on every public table');

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000c', 'c@alpha.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select public.create_organisation('Alpha Engineering');

reset role;
insert into public.member (id, org_id, user_id, email)
  select '00000000-0000-4000-8000-0000000000cc', id, '00000000-0000-4000-8000-00000000000c', 'c@alpha.test'
  from public.organisation where name = 'Alpha Engineering';
insert into public.role_assignment (org_id, member_id, workspace_id, role)
  select w.org_id, '00000000-0000-4000-8000-0000000000cc', w.id, 'Viewer' from public.workspace w;

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000c","role":"authenticated"}', true);
select throws_ok($$ select public.create_workspace((select id from public.organisation), 'Sneaky') $$,
  '42501', null, 'a Viewer cannot create a workspace');
select throws_ok($$ insert into public.workspace (org_id, name) select id, 'Direct' from public.organisation $$,
  '42501', null, 'direct table writes are refused');

select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select lives_ok($$ select public.create_workspace((select id from public.organisation), 'EU team') $$,
  'an Owner can create a workspace');

select set_config('request.jwt.claims', '', true);
select throws_ok($$ select public.create_organisation('Anon Inc') $$, '28000', null,
  'anonymous callers cannot create an organisation');

select * from finish();
rollback;
