begin;
create extension if not exists pgtap with schema extensions;
select plan(87);

-- ── Fixture ──────────────────────────────────────────────────────────────────
-- Alpha (A owner, K Viewer) holds the data. Beta (B owner) holds a foreign Brief. Gamma (C owner)
-- is a brand-new workspace with nothing in it. X is signed in but a member of nothing.
insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-0000000026a1', 'a@hm.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000026a2', 'k@hm.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000026a3', 'b@hm.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000026a4', 'c@hm.test', 'authenticated', 'authenticated'),
  ('00000000-0000-4000-8000-0000000026a5', 'x@hm.test', 'authenticated', 'authenticated');

set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a1","role":"authenticated"}', true);
select public.create_organisation('HM Alpha', 'Alpha WS');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a3","role":"authenticated"}', true);
select public.create_organisation('HM Beta');
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a4","role":"authenticated"}', true);
select public.create_organisation('HM Gamma');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
grant all on ids to authenticated;
insert into ids select 'org', id from public.organisation where name = 'HM Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'HM Alpha';
insert into ids select 'org_b', id from public.organisation where name = 'HM Beta';
insert into ids select 'ws_b', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'HM Beta';
insert into ids select 'ws_c', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'HM Gamma';
insert into public.member (id, org_id, user_id, email) values
  ('00000000-0000-4000-8000-0000000026a2', (select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000026a2', 'k@hm.test');
insert into public.role_assignment (org_id, member_id, workspace_id, role) values
  ((select v from ids where k = 'org'), '00000000-0000-4000-8000-0000000026a2', (select v from ids where k = 'ws'), 'Viewer');

-- Columns: two extract columns.
insert into public.predicate (key, org_id, workspace_id, label, value_type, allowed_values)
select k, (select v from ids where k = 'org'), (select v from ids where k = 'ws'), k, t, a::jsonb
  from (values ('hm_sites', 'number', null), ('hm_industry', 'enum', '["energy", "mining"]')) v(k, t, a);
insert into ids select 'p_sites', id from public.predicate where key = 'hm_sites';
insert into ids select 'p_ind', id from public.predicate where key = 'hm_industry';

-- Briefs: B1 Active (v1 ten days ago, v2 two days ago), B2 Draft, B3 Archived; Beta's foreign Brief.
insert into ids values ('b1', gen_random_uuid()), ('b2', gen_random_uuid()), ('b3', gen_random_uuid()), ('bf', gen_random_uuid()),
                       ('bv1', gen_random_uuid()), ('bv2', gen_random_uuid()), ('bvf', gen_random_uuid());
insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
values ((select v from ids where k = 'b1'), (select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'Drone docks', 'Draft', 'done'),
       ((select v from ids where k = 'b2'), (select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'Draft one', 'Draft', 'done'),
       ((select v from ids where k = 'b3'), (select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'Old one', 'Archived', 'done'),
       ((select v from ids where k = 'bf'), (select v from ids where k = 'org_b'), (select v from ids where k = 'ws_b'), 'Foreign', 'Active', 'done');
insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, published_at)
values ((select v from ids where k = 'bv1'), (select v from ids where k = 'b1'), (select v from ids where k = 'org'), (select v from ids where k = 'ws'),
        1, '{"predicates": ["hm_sites", "hm_industry"]}', '{}', '{}', now() - interval '10 days'),
       ((select v from ids where k = 'bv2'), (select v from ids where k = 'b1'), (select v from ids where k = 'org'), (select v from ids where k = 'ws'),
        2, '{"predicates": ["hm_sites", "hm_industry"]}', '{}', '{}', now() - interval '2 days'),
       ((select v from ids where k = 'bvf'), (select v from ids where k = 'bf'), (select v from ids where k = 'org_b'), (select v from ids where k = 'ws_b'),
        1, '{}', '{}', '{}', now() - interval '1 day');
update public.brief set state = 'Active', current_version_id = (select v from ids where k = 'bv2') where id = (select v from ids where k = 'b1');
update public.brief set current_version_id = (select v from ids where k = 'bvf') where id = (select v from ids where k = 'bf');

-- Radars: News (active), Jobs (error), Tenders (silent); a paused one is left out.
insert into ids values ('r_news', gen_random_uuid()), ('r_jobs', gen_random_uuid()), ('r_tend', gen_random_uuid()), ('r_off', gen_random_uuid());
insert into public.radar (id, org_id, workspace_id, brief_id, connector_type, name, status)
select (select v from ids where k = r), (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'b1'), t, n, s
  from (values ('r_news', 'news', 'News radar', 'active'), ('r_jobs', 'job_post', 'Jobs radar', 'error'),
               ('r_tend', 'tender', 'Tender radar', 'silent'), ('r_off', 'website', 'Paused radar', 'paused')) v(r, t, n, s);

-- Eight accounts: E1–E6 new this week, E7 and E8 a month old.
insert into ids select 'e' || n, app.create_entity((select v from ids where k = 'org'), 'company', 'HM E' || n, 'tenant')
  from generate_series(1, 8) n;
select set_config('app.writer', 'derivation', true);
insert into public.account (org_id, workspace_id, entity_id, stage, created_at)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e' || n),
       case n when 1 then 'Engaged' when 2 then 'Contacted' else 'Untouched' end::public.stage,
       case when n <= 6 then now() else now() - interval '30 days' end
  from generate_series(1, 8) n;
insert into ids select 'a' || substr(e.display_name, 5), a.id from public.account a join public.entity e on e.id = a.entity_id
 where a.workspace_id = (select v from ids where k = 'ws');
select set_config('app.writer', '', true);

-- Sources: one record of the News radar this week (mentions E1–E6), one three weeks old.
insert into public.extractor_version (capability, prompt_version, model, parser_version) values ('extract.hm26', '1', 'test', '1');
insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status, radar_id, created_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'radar:' || (select v from ids where k = 'r_news'), 'news',
        'https://hm26.example/1', now(), 'hm26-1', 'extracted', (select v from ids where k = 'r_news'), now()),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'radar:' || (select v from ids where k = 'r_news'), 'news',
        'https://hm26.example/0', now() - interval '21 days', 'hm26-0', 'extracted', (select v from ids where k = 'r_news'), now() - interval '21 days');
insert into ids select 's1', id from public.source_record where content_hash = 'hm26-1';
insert into ids select 's0', id from public.source_record where content_hash = 'hm26-0';
insert into ids select 'sd', app.derived_source_record((select v from ids where k = 'ws'), 'formula');
insert into public.mention (org_id, source_record_id, entity_type, raw_name, entity_id, match_method)
select (select v from ids where k = 'org'), (select v from ids where k = 's1'), 'company', 'HM E' || n, (select v from ids where k = 'e' || n), 'exact'
  from generate_series(1, 6) n;

-- Claims. E1: a cited quote (3 days old) and a newer uncited one. E2: its cited claim has no
-- quote, so "why now" falls back to its newest quoted claim, not the newer derived one.
create function pg_temp.claim(e text, p text, val jsonb, q text, src text, age interval) returns uuid language sql as $$
  select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = e), (select v from ids where k = p),
                          val, q, null, null, (select v from ids where k = src), now() - age,
                          (select id from public.extractor_version where capability = 'extract.hm26'), 80::smallint)
$$;
insert into ids select 'c1_cited', pg_temp.claim('e1', 'p_ind', '"energy"', 'E1 runs energy sites', 's1', interval '3 days');
insert into ids select 'c1_new', pg_temp.claim('e1', 'p_sites', '12', 'E1 has twelve sites', 's1', interval '1 day');
insert into ids select 'c2_cited', pg_temp.claim('e2', 'p_ind', '"mining"', null, 's1', interval '1 day');
insert into ids select 'c2_quote', pg_temp.claim('e2', 'p_sites', '4', 'E2 operates four mines', 's1', interval '4 days');
insert into ids select 'c2_derived', pg_temp.claim('e2', 'p_sites', '5', 'a derived quote', 'sd', interval '0 days');
insert into ids select 'c8', pg_temp.claim('e8', 'p_ind', '"energy"', 'E8 quote', 's1', interval '2 days');

-- Verdicts and scores under v2. Ready to contact: E1–E6 (E7 is Unverified, E8 is Low).
select set_config('app.writer', 'derivation', true);
insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'bv2'),
       (select v from ids where k = e), val::public.verdict_value, c
  from (values ('e1', 'Confirmed', array[(select v from ids where k = 'c1_cited')]),
               ('e2', 'Confirmed', array[(select v from ids where k = 'c2_cited')]),
               ('e3', 'Likely', '{}'::uuid[]), ('e4', 'Likely', '{}'::uuid[]), ('e5', 'Likely', '{}'::uuid[]),
               ('e6', 'Likely', '{}'::uuid[]), ('e7', 'Unverified', '{}'::uuid[]),
               ('e8', 'Confirmed', array[(select v from ids where k = 'c8')])) v(e, val, c);
insert into ids select 'v' || substr(e.display_name, 5), vd.id from public.verdict vd join public.entity e on e.id = vd.entity_id
 where vd.workspace_id = (select v from ids where k = 'ws');
insert into public.score (org_id, workspace_id, account_id, brief_version_id, priority, band)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = a),
       (select v from ids where k = 'bv2'), p, b::public.band
  from (values ('a1', 90, 'High'), ('a2', 80, 'High'), ('a3', 70, 'Medium'), ('a4', 60, 'Medium'), ('a5', 50, 'Medium'),
               ('a6', 40, 'Medium'), ('a7', 95, 'High'), ('a8', 99, 'Low')) v(a, p, b);
select set_config('app.writer', '', true);

-- Verdict history (verdict.changed events). Inside the last 7 days: E1 Confirmed → Likely →
-- Confirmed (after Likely three weeks ago), E4 Likely → Unverified → Likely, E3 / E5 / E6 first
-- verdicts, E7 a first Unverified (not a change). E2's changes are older than 7 days.
insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload, created_at)
select 'verdict.changed', (select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'verdict', (select v from ids where k = vk),
       jsonb_build_object('verdict_id', (select v from ids where k = vk), 'entity_id', (select v from ids where k = 'e' || substr(vk, 2)),
                          'brief_version_id', (select v from ids where k = 'bv2'), 'value', val),
       now() - age
  from (values ('v1', 'Likely', interval '20 days'), ('v1', 'Confirmed', interval '6 days'), ('v1', 'Likely', interval '5 days'),
               ('v1', 'Confirmed', interval '1 day'),
               ('v4', 'Likely', interval '4 days'), ('v4', 'Unverified', interval '84 hours'), ('v4', 'Likely', interval '3 days'),
               ('v3', 'Likely', interval '60 hours'), ('v5', 'Likely', interval '50 hours'), ('v6', 'Likely', interval '40 hours'),
               ('v7', 'Unverified', interval '30 hours'),
               ('v2', 'Likely', interval '12 days'), ('v2', 'Confirmed', interval '10 days')) v(vk, val, age);

-- Cells of hm_sites: E1 stale (its run finished five days ago, between v1 and v2); E3 errored on hm_industry.
update public.model_price set usd_per_million = 2 where model = 'claude-sonnet-5-5' and unit = 'ai_input_tokens';
update public.model_price set usd_per_million = 8 where model = 'claude-sonnet-5-5' and unit = 'ai_output_tokens';
insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at, inputs_hash, error)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'), (select v from ids where k = 'p_sites'),
        1, 'succeeded', now() - interval '5 days', now() - interval '5 days', 'old-hash', null),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e3'), (select v from ids where k = 'p_ind'),
        1, 'error', now() - interval '1 day', now() - interval '1 day', null, 'boom');
select is(app.cell_state((select v from ids where k = 'e1'), (select v from ids where k = 'p_sites')) || ','
          || app.cell_state((select v from ids where k = 'e3'), (select v from ids where k = 'p_ind')),
  'stale,error', '1. seeded: one stale and one errored cell');

-- Contacts, next steps, review, replies, meetings, tasks.
insert into public.contact_point (org_id, workspace_id, entity_id, account_id, type, value_encrypted, status)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'), (select v from ids where k = 'a1'), 'email', 'enc-1', 'valid'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e1'), (select v from ids where k = 'a1'), 'email', 'enc-2', 'risky'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'e2'), (select v from ids where k = 'a2'), 'phone', 'enc-3', 'valid');
insert into public.next_step (org_id, workspace_id, account_id, suggested_text)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a1'), 'Call the operations lead');
insert into public.review_item (org_id, workspace_id, mention_id, status)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), m.id, case when m.rn = 1 then 'pending' else 'merged' end
  from (select id, row_number() over (order by id) rn from public.mention where source_record_id = (select v from ids where k = 's1')) m
 where m.rn <= 2;
insert into public.verdict_review (org_id, workspace_id, verdict_id, reason, verdict_value)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'v3'), 'conflict', 'Likely');
insert into public.activity (org_id, workspace_id, account_id, kind, occurred_at, payload)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a1'), 'reply_received', now(), '{"reply_class": "Interested"}'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a2'), 'reply_received', now(), '{}'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a3'), 'reply_received', now() - interval '40 days', '{}'),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a3'), 'message_sent', now(), '{}');
insert into public.meeting (org_id, workspace_id, account_id, external_id, scheduled_at, held_at)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a1'), 'hm-m1', now(), now()),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a2'), 'hm-m2', now() + interval '2 days', null),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'a3'), 'hm-m3', now() - interval '40 days', now() - interval '40 days');
insert into public.task (org_id, workspace_id, account_id, title, due_date, origin, status)
select (select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = a), 't', (now() at time zone 'utc')::date + d, 'manual', s
  from (values ('a1', 0, 'open'), ('a2', 0, 'open'), ('a3', 0, 'done'), ('a4', 1, 'open')) v(a, d, s);

-- Budget: $85 of a $100 monthly workspace cap (a Brief cap and a daily cap are not the meter's).
insert into public.budget (org_id, workspace_id, brief_id, period, cap_usd)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), null, 'month', 100),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), (select v from ids where k = 'b1'), 'month', 10),
       ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), null, 'day', 500);
insert into public.usage_ledger (org_id, workspace_id, capability, provider, model, unit, quantity, cost_usd, status)
values ((select v from ids where k = 'org'), (select v from ids where k = 'ws'), 'extract', 'anthropic', 'm', 'ai_input_tokens', 1, 85, 'ok');

-- Every assertion below runs as K, a Viewer of Alpha.
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a2","role":"authenticated"}', true);
create function pg_temp.w() returns uuid language sql as $$ select v from ids where k = 'ws' $$;
create function pg_temp.b() returns uuid language sql as $$ select v from ids where k = 'b1' $$;
create function pg_temp.id(p text) returns uuid language sql as $$ select v from ids where k = p $$;

-- ── Header ───────────────────────────────────────────────────────────────────
select is(public.home_header(pg_temp.w(), pg_temp.b()) - 'brief',
  jsonb_build_object('lens', 'brief', 'workspace_name', 'Alpha WS', 'brief_count', 1, 'sources_running', 1,
                     'href', '/briefs/' || pg_temp.b()),
  '2. header with the Brief: lens, workspace, Pilot/Active count, active radars');
select is(public.home_header(pg_temp.w(), pg_temp.b()) -> 'brief',
  jsonb_build_object('id', pg_temp.b(), 'name', 'Drone docks', 'state', 'Active', 'version', 2,
                     'live_since', (select published_at from public.brief_version where id = pg_temp.id('bv1')),
                     'href', '/briefs/' || pg_temp.b()),
  '3. header with the Brief: name, current version, state, live since the first publish');
select is(public.home_header(pg_temp.w(), null),
  jsonb_build_object('lens', 'all', 'workspace_name', 'Alpha WS', 'brief', null, 'brief_count', 1,
                     'sources_running', 1, 'href', '/briefs'),
  '4. header for all: the workspace name and the count of Pilot/Active Briefs');
select is(public.home_header(pg_temp.w(), pg_temp.id('bf')), public.home_header(pg_temp.w(), null),
  '5. a foreign Brief id behaves as null');
select is(public.home_header(pg_temp.w(), pg_temp.id('b3')), public.home_header(pg_temp.w(), null),
  '6. an archived Brief id behaves as null');
select is(public.home_header(pg_temp.w(), gen_random_uuid()), public.home_header(pg_temp.w(), null),
  '7. an unknown Brief id behaves as null');
select is(public.home_header(pg_temp.w(), pg_temp.id('b2')) -> 'brief' ->> 'version', null,
  '8. a Draft Brief has no version yet');

-- ── Journey ──────────────────────────────────────────────────────────────────
select is((select string_agg(s ->> 'key', ',' order by o) from jsonb_array_elements(public.home_journey(pg_temp.w(), pg_temp.b()) -> 'steps') with ordinality x(s, o)),
  'brief,sources,first_accounts,first_confirmed,contacts,first_email', '9. six steps in order');
select is((select string_agg(s ->> 'done', ',' order by o) from jsonb_array_elements(public.home_journey(pg_temp.w(), pg_temp.b()) -> 'steps') with ordinality x(s, o)),
  'true,true,true,true,true,false', '10. every step done but first_email');
select is(public.home_journey(pg_temp.w(), pg_temp.b()) -> 'complete', 'true'::jsonb, '11. complete when all but first_email are done');
select is((select string_agg(s ->> 'href', ',' order by o) from jsonb_array_elements(public.home_journey(pg_temp.w(), null) -> 'steps') with ordinality x(s, o)),
  '/briefs/new,/sources,/accounts,/accounts?verdict=Confirmed,/accounts?view=ready_to_contact,/email', '12. each step links to its page');
select is(public.home_journey(pg_temp.w(), pg_temp.b()) -> 'steps' -> 3 -> 'done', 'true'::jsonb, '13. first_confirmed under the Brief');
select is(public.home_journey(pg_temp.w(), pg_temp.id('b2')) -> 'steps' -> 3 -> 'done', 'false'::jsonb,
  '14. first_confirmed is under the lens: the Draft Brief has none');
select is(public.home_journey(pg_temp.w(), pg_temp.id('b2')) -> 'complete', 'false'::jsonb, '15. so the journey is not complete there');

-- ── Metrics ──────────────────────────────────────────────────────────────────
select is(public.home_metrics(pg_temp.w(), pg_temp.b()),
  jsonb_build_object(
    'accounts_found', jsonb_build_object('count', 8, 'href', '/accounts'),
    'confirmed', jsonb_build_object('count', 3, 'href', '/accounts?verdict=Confirmed'),
    'replies_month', jsonb_build_object('count', 2, 'href', '/workspace/' || pg_temp.w() || '/inbox'),
    'meetings_held_month', jsonb_build_object('count', 1, 'href', '/workspace/' || pg_temp.w() || '/meetings'),
    'spend_month', jsonb_build_object('spent_usd', 85, 'cap_usd', 100, 'pct', 85, 'href', '/settings')),
  '16. metrics match the seed; the spend cap is the workspace monthly budget');
select is(public.home_metrics(pg_temp.w(), null) -> 'accounts_found' -> 'count', '8'::jsonb, '17. all: every account');
select is(public.home_metrics(pg_temp.w(), pg_temp.id('b2')) -> 'accounts_found' -> 'count', '0'::jsonb,
  '18. a Brief lens counts the accounts judged under it');
select is(public.home_metrics(pg_temp.w(), pg_temp.id('b2')) -> 'confirmed' -> 'count', '0'::jsonb, '19. and its Confirmed');

-- ── Needs you ────────────────────────────────────────────────────────────────
select is(public.home_needs_you(pg_temp.w(), pg_temp.b()) - 'stale',
  jsonb_build_object(
    'review', jsonb_build_object('count', 2, 'href', '/review'),
    'replies_to_classify', jsonb_build_object('count', 2, 'href', '/workspace/' || pg_temp.w() || '/inbox'),
    'errored', jsonb_build_object('count', 1, 'href', '/accounts?view=errored')),
  '20. review decisions, replies to classify, errored accounts');
select is(public.home_needs_you(pg_temp.w(), pg_temp.b()) -> 'stale',
  jsonb_build_object('count', 1, 'href', '/accounts?view=stale',
                     'estimate_usd', (public.estimate_column_run(pg_temp.w(), pg_temp.id('p_sites'), 'stale', pg_temp.b()) -> 'cost_usd')),
  '21. one stale account; the estimate is estimate_column_run(stale).cost_usd');
select is((public.home_needs_you(pg_temp.w(), pg_temp.b()) #>> '{stale,estimate_usd}')::numeric, 0.01::numeric, '22. which is one extract call');
select is(public.home_needs_you(pg_temp.w(), null) -> 'stale', public.home_needs_you(pg_temp.w(), pg_temp.b()) -> 'stale',
  '23. all: the same stale count and estimate over every column');
select is(public.home_needs_you(pg_temp.w(), null) #>> '{errored,count}', '1', '24. all: one errored account');

-- ── Accounts to act on ───────────────────────────────────────────────────────
select is((select string_agg(x ->> 'name', ',' order by o) from jsonb_array_elements(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) -> 'accounts') with ordinality t(x, o)),
  'HM E1,HM E2,HM E3,HM E4,HM E5', '25. at most 5, by priority, only Confirmed/Likely and High/Medium');
select is((select string_agg((x ->> 'verdict') || '/' || (x ->> 'band'), ',' order by o) from jsonb_array_elements(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) -> 'accounts') with ordinality t(x, o)),
  'Confirmed/High,Confirmed/High,Likely/Medium,Likely/Medium,Likely/Medium', '26. verdict and band');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) -> 'accounts' -> 0,
  jsonb_build_object('account_id', pg_temp.id('a1'), 'entity_id', pg_temp.id('e1'), 'name', 'HM E1', 'verdict', 'Confirmed',
                     'band', 'High', 'priority', 90.00,
                     'why_now', jsonb_build_object('claim_id', pg_temp.id('c1_cited'), 'quote', 'E1 runs energy sites',
                                                   'observed_at', now() - interval '3 days'),
                     'valid_contacts', 1,
                     'next_action', jsonb_build_object('text', 'Call the operations lead', 'mode', 'Suggest'),
                     'href', '/workspace/' || pg_temp.w() || '/accounts/' || pg_temp.id('a1')),
  '27. the top account: "why now" is the quoted claim its verdict cites, not the newer one');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) #>> '{accounts,1,why_now,quote}', 'E2 operates four mines',
  '28. no cited quote: the newest quoted claim, never a derived one');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) #> '{accounts,2,why_now}', 'null'::jsonb, '29. no quoted claim: why_now is null');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) #> '{accounts,2,next_action}', 'null'::jsonb, '30. no next step: next_action is null');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) #> '{accounts,2,valid_contacts}', '0'::jsonb, '31. no contacts: 0');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) - 'accounts',
  '{"total": 6, "href": "/accounts?view=ready_to_contact"}'::jsonb, '32. the total and the link to the whole view');
select is(public.home_accounts_to_act(pg_temp.w(), null) -> 'accounts', public.home_accounts_to_act(pg_temp.w(), pg_temp.b()) -> 'accounts',
  '33. all: the Pilot/Active Briefs (here the one)');

-- ── What changed ─────────────────────────────────────────────────────────────
create function pg_temp.ch(b uuid) returns jsonb language sql as $$ select public.home_changes(pg_temp.w(), b) -> 'items' $$;
select is(jsonb_array_length(pg_temp.ch(pg_temp.b())), 10, '34. at most 10 items (11 happened)');
select ok((select bool_and((x ->> 'at')::timestamptz >= now() - interval '7 days') from jsonb_array_elements(pg_temp.ch(pg_temp.b())) x),
  '35. only the last 7 days');
select ok(not exists (select 1 from (select (x ->> 'at')::timestamptz a, lag((x ->> 'at')::timestamptz) over (order by o) p
                                       from jsonb_array_elements(pg_temp.ch(pg_temp.b())) with ordinality t(x, o)) s
                       where s.a > s.p),
  '36. newest first');
select is((select string_agg(distinct x ->> 'kind', ',') from jsonb_array_elements(pg_temp.ch(pg_temp.b())) x),
  'brief_version,new_accounts,verdict', '37. covers each kind');
select is((select string_agg(coalesce(x ->> 'from', '-') || '>' || (x ->> 'to') || ':' || (x ->> 'name'), ',' order by o)
             from jsonb_array_elements(pg_temp.ch(pg_temp.b())) with ordinality t(x, o) where x ->> 'kind' = 'verdict'),
  'Likely>Confirmed:HM E1,-' || '>Likely:HM E6,-' || '>Likely:HM E5,-' || '>Likely:HM E3,Unverified>Likely:HM E4,Likely>Unverified:HM E4,-' || '>Likely:HM E4,Confirmed>Likely:HM E1',
  '38. verdict changes newest first: a first Unverified is not news, the oldest change falls past the limit');
select is((select x from jsonb_array_elements(pg_temp.ch(pg_temp.b())) x where x ->> 'kind' = 'verdict' limit 1),
  jsonb_build_object('kind', 'verdict', 'at', now() - interval '1 day', 'entity_id', pg_temp.id('e1'), 'account_id', pg_temp.id('a1'),
                     'name', 'HM E1', 'from', 'Likely', 'to', 'Confirmed', 'quote', 'E1 runs energy sites',
                     'href', '/workspace/' || pg_temp.w() || '/accounts/' || pg_temp.id('a1')),
  '39. a verdict item carries the cited claim''s quote');
select is((select x from jsonb_array_elements(pg_temp.ch(pg_temp.b())) x where x ->> 'kind' = 'brief_version'),
  jsonb_build_object('kind', 'brief_version', 'at', now() - interval '2 days', 'brief_id', pg_temp.b(), 'brief_name', 'Drone docks',
                     'version', 2, 'stale_cells', 1, 'href', '/briefs/' || pg_temp.b()),
  '40. the Brief version published this week and the stale cell it caused (v1 is older than 7 days)');
select is((select x from jsonb_array_elements(pg_temp.ch(pg_temp.b())) x where x ->> 'kind' = 'new_accounts'),
  jsonb_build_object('kind', 'new_accounts', 'at', now(), 'radar_id', pg_temp.id('r_news'), 'radar_name', 'News radar', 'count', 6,
                     'href', '/accounts?radar=' || pg_temp.id('r_news')),
  '41. new accounts by radar');
select is(pg_temp.ch(null), pg_temp.ch(pg_temp.b()), '42. all: the same items here');
select is(pg_temp.ch(pg_temp.id('b2')), '[]'::jsonb, '43. the Draft Brief lens has no changes');

-- ── CRM ──────────────────────────────────────────────────────────────────────
select is((select string_agg((x ->> 'stage') || '=' || (x ->> 'count'), ',' order by o) from jsonb_array_elements(public.home_crm(pg_temp.w(), pg_temp.b()) -> 'stages') with ordinality t(x, o)),
  'Untouched=6,Queued=0,Contacted=1,Engaged=1,Qualified=0,Meeting=0,Handed off=0,Dormant=0,Disqualified=0', '44. every stage, with counts');
select is(public.home_crm(pg_temp.w(), pg_temp.b()) #>> '{stages,6,href}', '/crm/pipeline?stage=Handed%20off', '45. each stage links to the pipeline');
select is(public.home_crm(pg_temp.w(), pg_temp.b()) -> 'tasks_due_today',
  jsonb_build_object('count', 2, 'href', '/workspace/' || pg_temp.w() || '/meetings'), '46. open tasks due today');

-- ── Sources ──────────────────────────────────────────────────────────────────
select is((select string_agg((x ->> 'name') || ':' || (x ->> 'health') || ':' || (x ->> 'records_7d'), ',' order by o)
             from jsonb_array_elements(public.home_sources(pg_temp.w(), pg_temp.b()) -> 'radars') with ordinality t(x, o)),
  'Jobs radar:incident:0,Tender radar:silent:0,News radar:ok:1', '47. failures first; paused radars left out; records in the last 7 days');
select is(public.home_sources(pg_temp.w(), pg_temp.b()) -> 'radars' -> 0,
  jsonb_build_object('id', pg_temp.id('r_jobs'), 'name', 'Jobs radar', 'type', 'job_post', 'health', 'incident', 'records_7d', 0,
                     'href', '/sources'),
  '48. a radar row');
select is(public.home_sources(pg_temp.w(), null), public.home_sources(pg_temp.w(), pg_temp.b()), '49. all: every radar of the workspace');
select is(public.home_sources(pg_temp.w(), pg_temp.id('b2')) -> 'radars', '[]'::jsonb, '50. a Brief lens: only its radars');

-- ── Sidebar and budget ───────────────────────────────────────────────────────
select is(public.sidebar_counts(pg_temp.w()), '{"review": 2}'::jsonb, '51. sidebar: pending review items plus pending verdict reviews');
select is(public.budget_meter(pg_temp.w()), '{"spent_usd": 85, "cap_usd": 100, "pct": 85}'::jsonb, '52. budget meter at 85%');

-- ── Foreign Brief = null, for every lens RPC ─────────────────────────────────
select is(public.home_journey(pg_temp.w(), pg_temp.id('bf')), public.home_journey(pg_temp.w(), null), '53. journey: foreign Brief as null');
select is(public.home_metrics(pg_temp.w(), pg_temp.id('bf')), public.home_metrics(pg_temp.w(), null), '54. metrics: foreign Brief as null');
select is(public.home_needs_you(pg_temp.w(), pg_temp.id('bf')), public.home_needs_you(pg_temp.w(), null), '55. needs you: foreign Brief as null');
select is(public.home_accounts_to_act(pg_temp.w(), pg_temp.id('bf')), public.home_accounts_to_act(pg_temp.w(), null), '56. accounts: foreign Brief as null');
select is(public.home_changes(pg_temp.w(), pg_temp.id('bf')), public.home_changes(pg_temp.w(), null), '57. changes: foreign Brief as null');
select is(public.home_crm(pg_temp.w(), pg_temp.id('bf')), public.home_crm(pg_temp.w(), null), '58. crm: foreign Brief as null');
select is(public.home_sources(pg_temp.w(), pg_temp.id('b3')), public.home_sources(pg_temp.w(), null), '59. sources: archived Brief as null');

-- ── Empty workspace (Review Focus 3) ─────────────────────────────────────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a4","role":"authenticated"}', true);
create function pg_temp.c() returns uuid language sql as $$ select v from ids where k = 'ws_c' $$;
select is(public.home_header(pg_temp.c(), null),
  '{"lens": "all", "workspace_name": "Default", "brief": null, "brief_count": 0, "sources_running": 0, "href": "/briefs"}'::jsonb,
  '60. empty: header');
select is((select string_agg(s ->> 'done', ',') from jsonb_array_elements(public.home_journey(pg_temp.c(), null) -> 'steps') s),
  'false,false,false,false,false,false', '61. empty: no step done');
select is(public.home_journey(pg_temp.c(), null) -> 'complete', 'false'::jsonb, '62. empty: journey not complete');
select is(public.home_metrics(pg_temp.c(), null),
  jsonb_build_object(
    'accounts_found', jsonb_build_object('count', 0, 'href', '/accounts'),
    'confirmed', jsonb_build_object('count', 0, 'href', '/accounts?verdict=Confirmed'),
    'replies_month', jsonb_build_object('count', 0, 'href', '/workspace/' || pg_temp.c() || '/inbox'),
    'meetings_held_month', jsonb_build_object('count', 0, 'href', '/workspace/' || pg_temp.c() || '/meetings'),
    'spend_month', jsonb_build_object('spent_usd', 0, 'cap_usd', null, 'pct', null, 'href', '/settings')),
  '63. empty: metrics are zeros (no cap: cap and pct null)');
select is(public.home_needs_you(pg_temp.c(), null),
  jsonb_build_object(
    'review', jsonb_build_object('count', 0, 'href', '/review'),
    'replies_to_classify', jsonb_build_object('count', 0, 'href', '/workspace/' || pg_temp.c() || '/inbox'),
    'stale', jsonb_build_object('count', 0, 'estimate_usd', 0, 'href', '/accounts?view=stale'),
    'errored', jsonb_build_object('count', 0, 'href', '/accounts?view=errored')),
  '64. empty: needs you is zeros');
select is(public.home_accounts_to_act(pg_temp.c(), null), '{"accounts": [], "total": 0, "href": "/accounts?view=ready_to_contact"}'::jsonb,
  '65. empty: no accounts');
select is(public.home_changes(pg_temp.c(), null), '{"items": []}'::jsonb, '66. empty: no changes');
select is((select string_agg(x ->> 'count', ',') from jsonb_array_elements(public.home_crm(pg_temp.c(), null) -> 'stages') x),
  '0,0,0,0,0,0,0,0,0', '67. empty: every stage at 0');
select is(public.home_crm(pg_temp.c(), null) #> '{tasks_due_today,count}', '0'::jsonb, '68. empty: no tasks');
select is(public.home_sources(pg_temp.c(), null), '{"radars": [], "href": "/sources"}'::jsonb, '69. empty: no radars');
select is(public.sidebar_counts(pg_temp.c()), '{"review": 0}'::jsonb, '70. empty: review 0');
select is(public.budget_meter(pg_temp.c()), '{"spent_usd": 0, "cap_usd": null, "pct": null}'::jsonb,
  '71. a workspace without a cap: null cap and pct');

-- ── Access (Review Focus 1): a non-member gets 42501 from every RPC ───────────
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a5","role":"authenticated"}', true);
select throws_ok(format('select public.%s(%L::uuid, null)', f, pg_temp.w()), '42501', 'permission denied', '72. non-member: ' || f)
  from unnest(array['home_header', 'home_journey', 'home_metrics', 'home_needs_you', 'home_accounts_to_act',
                    'home_changes', 'home_crm', 'home_sources']) f;
select throws_ok(format('select public.sidebar_counts(%L::uuid)', pg_temp.w()), '42501', 'permission denied', '73. non-member: sidebar_counts');
select throws_ok(format('select public.budget_meter(%L::uuid)', pg_temp.w()), '42501', 'permission denied', '74. non-member: budget_meter');
-- B (Beta's owner) is a member elsewhere, not of Alpha: still refused.
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-0000000026a3","role":"authenticated"}', true);
select throws_ok(format('select public.home_header(%L::uuid, %L::uuid)', pg_temp.w(), pg_temp.id('bf')), '42501', 'permission denied',
  '75. a member of another workspace is refused, even with its own Brief');
select throws_ok(format('select public.budget_meter(%L::uuid)', gen_random_uuid()), '42501', 'permission denied', '76. an unknown workspace is refused');
reset role;

-- ── Grants ───────────────────────────────────────────────────────────────────
select ok(has_function_privilege('authenticated', 'public.home_changes(uuid, uuid)', 'execute'), '77. authenticated may call a home RPC');
select ok(not has_function_privilege('anon', 'public.home_changes(uuid, uuid)', 'execute'), '78. anon may not');
select ok(not has_function_privilege('anon', 'public.budget_meter(uuid)', 'execute'), '79. anon may not read the meter');
select ok(not has_function_privilege('authenticated', 'app.home_lens(uuid, uuid)', 'execute'), '80. app helpers are not granted');

select * from finish();
rollback;
