begin;
create extension if not exists pgtap with schema extensions;
select plan(11);

insert into auth.users (id, email, aud, role) values
  ('00000000-0000-4000-8000-00000000ea01', 'a@ei.test', 'authenticated', 'authenticated');
set local role authenticated;
select set_config('request.jwt.claims', '{"sub":"00000000-0000-4000-8000-00000000ea01","role":"authenticated"}', true);
select public.create_organisation('EI Alpha');
reset role;

create temp table ids (k text primary key, v uuid) on commit drop;
insert into ids select 'org', id from public.organisation where name = 'EI Alpha';
insert into ids select 'ws', w.id from public.workspace w join public.organisation o on o.id = w.org_id where o.name = 'EI Alpha';

insert into public.predicate (key, label, value_type) values ('opening.ei21', 'Opening EI21', 'text');
insert into public.extractor_version (capability, prompt_version, model, parser_version)
  values ('extract.ei21', '0.1.0', 'claude-test', '0.1.0');
insert into ids select 'e', app.create_entity((select v from ids where k = 'org'), 'company', 'EI Co', 'tenant');
insert into ids select 'e2', app.create_entity((select v from ids where k = 'org'), 'company', 'EI Co Dup', 'tenant');
insert into ids select 's1', app.create_source_record((select v from ids where k = 'org'), (select v from ids where k = 'ws'),
  'radar:x', 'directory', 'https://ei21.example.com/1', now(), 'ei21-hash-1', null, 'en', 'text one');

insert into ids select 'c1', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e'),
  (select id from public.predicate where key = 'opening.ei21'), '"2027"'::jsonb, null, null, null,
  (select v from ids where k = 's1'), now(), (select id from public.extractor_version where capability = 'extract.ei21'), 60::smallint);
insert into ids select 'c2', app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e'),
  (select id from public.predicate where key = 'opening.ei21'), '"2027"'::jsonb, 'quote', null, null,
  (select v from ids where k = 's1'), now(), (select id from public.extractor_version where capability = 'extract.ei21'), 90::smallint);

select is((select v from ids where k = 'c2'), (select v from ids where k = 'c1'), '1. identical claim returns the same id');
select is((select count(*)::int from public.claim where source_record_id = (select v from ids where k = 's1')), 1, '2. only one claim row exists');
select is((select confidence::int from public.claim where id = (select v from ids where k = 'c1')), 90, '3. the higher confidence is kept');
select is((select quote from public.claim where id = (select v from ids where k = 'c1')), 'quote', '4. a missing quote is filled');
select is((select count(*)::int from public.outbox where event = 'claim.created' and aggregate_id = (select v from ids where k = 'c1')), 1,
  '5. claim.created is emitted once');

select isnt((select app.create_claim((select v from ids where k = 'org'), (select v from ids where k = 'e'),
  (select id from public.predicate where key = 'opening.ei21'), '"2028"'::jsonb, null, null, null,
  (select v from ids where k = 's1'), now(), (select id from public.extractor_version where capability = 'extract.ei21'), 70::smallint)),
  (select v from ids where k = 'c1'), '6. a different value creates a second claim');
select is((select count(*)::int from public.claim where source_record_id = (select v from ids where k = 's1')), 2, '7. two claims now exist');

select app.merge_entities((select v from ids where k = 'e'), (select v from ids where k = 'e2'), null);
select is((select count(*)::int from public.outbox where event = 'entity.merged'
  and payload->>'loser' = (select v::text from ids where k = 'e2')), 1, '8. entity.merged is emitted with the loser');
select ok(exists (select 1 from pg_constraint where conname = 'meeting_person_entity_fk' and contype = 'f'
  and conrelid = 'public.meeting'::regclass), '9. meeting.person_entity_id has a foreign key');

-- The dedupe flag is scoped to create_claim: outside it, confidence and quote stay immutable.
select throws_ok(
  $$update public.claim set confidence = 99 where id = (select v from ids where k = 'c1')$$,
  '42501', 'column confidence of claim is immutable',
  '10. a direct confidence update outside create_claim is blocked');
select throws_ok(
  $$update public.claim set quote = 'edited' where id = (select v from ids where k = 'c1')$$,
  '42501', 'column quote of claim is immutable',
  '11. a direct quote update outside create_claim is blocked');

select * from finish();
rollback;
