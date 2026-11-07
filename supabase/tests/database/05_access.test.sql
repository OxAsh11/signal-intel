begin;
create extension if not exists pgtap with schema extensions;
select plan(9);

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000d', 'd@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000e', 'e@other.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select public.create_organisation('Alpha Engineering');
create temp table tok (who text, token text) on commit drop;
grant all on tok to authenticated;
insert into tok select 'd', public.invite_member((select id from public.organisation), 'd@alpha.test', 'Editor', (select id from public.workspace));
insert into tok select 'f', public.invite_member((select id from public.organisation), 'f@alpha.test', 'Viewer', (select id from public.workspace));

-- Forwarded link: someone else cannot use it.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000e","role":"authenticated"}', true);
select throws_ok($$ select public.accept_invitation((select token from tok where who = 'f')) $$, '42501', null,
  'invitation accepted only by its email');

-- The invitee accepts.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000d","role":"authenticated"}', true);
select lives_ok($$ select public.accept_invitation((select token from tok where who = 'd')) $$, 'invitee accepts by token');
select is((select count(*)::int from public.workspace), 1, 'accepted Editor sees the workspace');
select throws_ok($$ select public.invite_member((select id from public.organisation), 'x@alpha.test', 'Viewer', (select id from public.workspace)) $$,
  '42501', null, 'Editor cannot invite');

-- Owner promotes D to Admin; Admin cannot grant Owner.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select lives_ok($$ select public.set_member_role((select id from public.member where email = 'd@alpha.test'), 'Admin') $$,
  'Owner makes D an Admin');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000d","role":"authenticated"}', true);
select throws_ok($$ select public.set_member_role((select id from public.member where email = 'd@alpha.test'), 'Owner') $$,
  '42501', null, 'admin cannot grant Owner');

-- Last Owner stays; removal ends access.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select throws_ok($$ select public.set_member_role((select id from public.member where email = 'a@alpha.test'), 'Admin') $$,
  '23514', null, 'last owner cannot be demoted');
select lives_ok($$ select public.remove_member((select id from public.member where email = 'd@alpha.test')) $$, 'Owner removes D');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000d","role":"authenticated"}', true);
select is((select count(*)::int from public.organisation), 0, 'removed member sees nothing');

select * from finish();
rollback;
