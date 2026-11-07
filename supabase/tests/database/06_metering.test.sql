begin;
create extension if not exists pgtap with schema extensions;
select plan(21);

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000000a', 'a@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000021', 'j@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000022', 'k@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000023', 'l@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-000000000024', 'o@alpha.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-00000000000b', 'b@beta.test', 'authenticated', 'authenticated');

-- Alpha: Owner A, Editor J, Viewer K, Admin L. Beta: Owner B.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select public.create_organisation('Alpha Engineering');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
select public.create_organisation('Beta Cold Storage');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'Alpha Engineering';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'Alpha Engineering';

insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000000a1', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000021', 'j@alpha.test'),
  ('00000000-0000-4000-8000-0000000000a2', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000022', 'k@alpha.test'),
  ('00000000-0000-4000-8000-0000000000a3', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000023', 'l@alpha.test'),
  ('00000000-0000-4000-8000-0000000000a4', (select v from ids where k = 'org'), '00000000-0000-4000-8000-000000000024', 'o@alpha.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000000a1', (select v from ids where k = 'ws'), 'Editor'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000000a2', (select v from ids where k = 'ws'), 'Viewer'),
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000000a4', (select v from ids where k = 'ws'), 'Operator');
insert into public.role_assignment (org_id, member_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000000a3', 'Admin');

-- Ledger is insert-only.
insert into public.usage_ledger (org_id, workspace_id, capability, provider, model, unit, quantity, cost_usd, status)
  values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'extract', 'anthropic', 'claude-sonnet-5-5', 'ai_input_tokens', 1000, 4, 'ok');
select throws_ok($$ update public.usage_ledger set cost_usd = 0 $$, '42501', null, 'ledger is insert-only: update raises');
select throws_ok($$ delete from public.usage_ledger $$, '42501', null, 'ledger is insert-only: delete raises');

-- The reservation takes a per-workspace advisory lock before reading spend.
select ok((select prosrc from pg_proc where proname = 'reserve_budget' and pronamespace = 'app'::regnamespace)
          like '%hashtextextended(p_ws::text, 0)%', 'reserve_budget locks on the workspace alone');
select ok((select prosrc from pg_proc where proname = 'settle_reservation' and pronamespace = 'app'::regnamespace)
          like '%hashtextextended(r.workspace_id::text, 0)%', 'settle_reservation locks on the workspace alone');

-- Admin sets a day cap of 10 (spent 4).
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000023","role":"authenticated"}', true);
set local role authenticated;
select lives_ok($$ select public.set_budget((select v from ids where k = 'ws'), null, 'day', 10) $$, 'Admin can set a budget');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000021","role":"authenticated"}', true);
select throws_ok($$ select public.set_budget((select v from ids where k = 'ws'), null, 'day', 99) $$, '42501', null, 'set_budget requires Admin');
reset role;

-- Reserve within cap, then over cap; the second of two 5 USD reservations is refused.
select is((app.reserve_budget((select v from ids where k = 'ws'), null, 5, 'extract.run', '{}')) ->> 'ok', 'true', 'reserve within cap ok');
select is((app.reserve_budget((select v from ids where k = 'ws'), null, 5, 'extract.run', '{}')) ->> 'ok', 'false',
          'concurrent calls cannot both overspend (sequential proof)');
select is((select count(*)::int from public.budget_approval where status = 'pending'), 1, 'reserve over cap pauses with one pending approval');

-- Dedupe (0014): an identical refusal reuses the pending approval instead of opening another.
select is((app.reserve_budget((select v from ids where k = 'ws'), null, 5, 'extract.run', '{}')) ->> 'approval_id',
          (select id::text from public.budget_approval where status = 'pending'),
          'an identical refusal returns the same pending approval');

-- big and small use distinct job args so they are separate requests.
insert into ids select 'big', (app.reserve_budget((select v from ids where k = 'ws'), null, 30, 'extract.run', '{"k":"big"}') ->> 'approval_id')::uuid;
insert into ids select 'small', (app.reserve_budget((select v from ids where k = 'ws'), null, 20, 'extract.run', '{"k":"small"}') ->> 'approval_id')::uuid;

-- Approvals: Viewer no; Editor within the limit only; Admin any.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000022","role":"authenticated"}', true);
set local role authenticated;
select throws_ok($$ select public.decide_budget_approval((select v from ids where k = 'small'), true) $$, '42501', null, 'viewer cannot approve');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000024","role":"authenticated"}', true);
select throws_ok($$ select public.decide_budget_approval((select v from ids where k = 'small'), true) $$, '42501', null, 'operator cannot approve');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000021","role":"authenticated"}', true);
select throws_ok($$ select public.decide_budget_approval((select v from ids where k = 'big'), true) $$, '42501', null, 'editor limit enforced');
select lives_ok($$ select public.decide_budget_approval((select v from ids where k = 'small'), true) $$, 'editor approves within the limit');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000023","role":"authenticated"}', true);
select lives_ok($$ select public.decide_budget_approval((select v from ids where k = 'big'), true) $$, 'admin can approve any');
reset role;

select is((app.reserve_budget((select v from ids where k = 'ws'), null, 30, 'extract.run', '{}', (select v from ids where k = 'big')) ->> 'ok'),
          'true', 'an approved request lets the same work through');

-- Access and AI status.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
set local role authenticated;
select is((select count(*)::int from public.usage_ledger), 0, 'other org cannot read ledger');
select throws_ok($$ select app.reserve_budget((select v from ids where k = 'ws'), null, 1, 'x', '{}') $$, '42501', null,
  'clients cannot call reserve_budget');
select throws_ok($$ select public.workspace_spend((select v from ids where k = 'ws')) $$, '42501', null,
  'workspace_spend refused for a non-member');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-000000000022","role":"authenticated"}', true);
select is((select array_agg(k order by k) from jsonb_object_keys(public.workspace_spend((select v from ids where k = 'ws'))) k),
          array['day', 'month'], 'workspace_spend returns day and month for a viewer');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000000b","role":"authenticated"}', true);
select is(public.ai_status(), '{"chat": false, "embed": false, "checked_at": null}'::jsonb, 'ai_status default not configured');

select * from finish();
rollback;
