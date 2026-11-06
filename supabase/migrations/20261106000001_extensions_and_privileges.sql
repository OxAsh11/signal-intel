-- F8 storage substrate: extensions used across the platform.
create schema if not exists extensions;
create extension if not exists vector    with schema extensions;  -- M15 memory_chunk embeddings
create extension if not exists pg_trgm   with schema extensions;  -- M4 fuzzy name matching
create extension if not exists unaccent  with schema extensions;  -- M4 name normalisation
create extension if not exists pgcrypto  with schema extensions;  -- S8 field-level encryption helpers

-- Internal helpers live in `app` (not exposed through the Data API).
create schema if not exists app;
revoke all on schema app from public;
grant usage on schema app to authenticated, service_role;

-- S8: "No client may call a write function anonymously" and F1: RLS is the first line.
-- Supabase grants ALL on new public tables to anon/authenticated by default; undo that.
-- Every table must opt in with an explicit `grant select ... to authenticated`.
alter default privileges in schema public revoke all on tables    from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke execute on functions from public, anon, authenticated;
alter default privileges in schema app    revoke execute on functions from public, anon, authenticated;
