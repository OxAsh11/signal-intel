-- pgTAP suite (runs in CI via `supabase test db`). Mirrors scripts/check-migrations.mjs on real Supabase.
begin;
create extension if not exists pgtap with schema extensions;
select plan(3);
select has_schema('app');
select has_function('app', 'has_workspace_role', array['uuid', 'member_role']);
select has_function('public', 'create_organisation', array['text', 'text']);
select * from finish();
rollback;
