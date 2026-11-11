// An in-memory Postgres (PGlite) with a minimal Supabase stub and every migration applied.
// Shared by scripts/check-sql.mjs and scripts/pg-test-server.mjs.
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite/vector';
import { pg_trgm } from '@electric-sql/pglite/contrib/pg_trgm';
import { unaccent } from '@electric-sql/pglite/contrib/unaccent';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';

export const repo = resolve(dirname(fileURLToPath(import.meta.url)), '../..');

const SUPABASE_STUB = `
  create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
  grant usage on schema public to anon, authenticated, service_role;
  create schema auth;
  create table auth.users (id uuid primary key, email text);
  create function auth.uid() returns uuid language sql stable as $$
    select coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''),
                    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'))::uuid $$;
  grant usage on schema auth to anon, authenticated, service_role;
  grant execute on function auth.uid() to anon, authenticated, service_role;
`;

export async function openMigratedDb() {
  const db = await PGlite.create({ extensions: { vector, pg_trgm, unaccent, pgcrypto } });
  await db.exec(SUPABASE_STUB);
  const migDir = resolve(repo, 'supabase/migrations');
  for (const file of readdirSync(migDir).filter((f) => f.endsWith('.sql')).sort()) {
    await db.exec(readFileSync(resolve(migDir, file), 'utf8'));
  }
  // Supabase gives service_role full access to tables in public, and every role usage on extensions.
  await db.exec(`grant all on all tables in schema public to service_role;
                 grant all on all sequences in schema public to service_role;
                 grant usage on schema extensions to anon, authenticated, service_role`);
  return db;
}
