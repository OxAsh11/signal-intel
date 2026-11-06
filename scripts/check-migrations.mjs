#!/usr/bin/env node
// Local migration smoke test without Docker: applies supabase/migrations/*.sql to an
// in-memory Postgres 17 (PGlite) with a minimal Supabase stub (auth schema + roles),
// then checks enum drift, tenant isolation, write paths and audit immutability.
// The full pgTAP suite (supabase/tests) runs against real Supabase in CI.
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite/vector';
import { pg_trgm } from '@electric-sql/pglite/contrib/pg_trgm';
import { unaccent } from '@electric-sql/pglite/contrib/unaccent';
import { pgcrypto } from '@electric-sql/pglite/contrib/pgcrypto';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const migDir = resolve(repo, 'supabase/migrations');

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

const failures = [];
function check(name, ok, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures.push(name);
}

const db = await PGlite.create({ extensions: { vector, pg_trgm, unaccent, pgcrypto } });
await db.exec(SUPABASE_STUB);

for (const file of readdirSync(migDir).filter((f) => f.endsWith('.sql')).sort()) {
  try {
    await db.exec(readFileSync(resolve(migDir, file), 'utf8'));
    check(`apply ${file}`, true);
  } catch (err) {
    check(`apply ${file}`, false, err.message);
    process.exit(1);
  }
}

// 1. Enum drift: Postgres enums == packages/contracts/schema/enums.json
const { enums } = JSON.parse(readFileSync(resolve(repo, 'packages/contracts/schema/enums.json'), 'utf8'));
for (const [name, def] of Object.entries(enums)) {
  const { rows } = await db.query(
    `select e.enumlabel as v from pg_enum e join pg_type t on t.oid = e.enumtypid
     join pg_namespace n on n.oid = t.typnamespace
     where n.nspname = 'public' and t.typname = $1 order by e.enumsortorder`,
    [def.pg],
  );
  const got = rows.map((r) => r.v);
  check(`enum ${def.pg} matches ${name}`, JSON.stringify(got) === JSON.stringify(def.values), got.join(','));
}

// 2. No insert/update/delete/truncate grants for anon or authenticated on any public table.
{
  const { rows } = await db.query(
    `select table_name, grantee, privilege_type from information_schema.role_table_grants
     where table_schema = 'public' and grantee in ('anon','authenticated')
       and privilege_type in ('INSERT','UPDATE','DELETE','TRUNCATE')`,
  );
  check('no client write grants on public tables', rows.length === 0, rows.map((r) => `${r.grantee}:${r.privilege_type}:${r.table_name}`).join(' '));
}

// 3. RLS enabled on every public table.
{
  const { rows } = await db.query(
    `select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity`,
  );
  check('RLS enabled on every public table', rows.length === 0, rows.map((r) => r.relname).join(','));
}

// Helpers to act as a signed-in user inside a transaction.
const A = '00000000-0000-4000-8000-00000000000a';
const B = '00000000-0000-4000-8000-00000000000b';
const C = '00000000-0000-4000-8000-00000000000c';
await db.exec(`insert into auth.users (id, email) values
  ('${A}','a@alpha.test'), ('${B}','b@beta.test'), ('${C}','c@alpha.test')`);

async function asUser(uid, fn) {
  return db.transaction(async (tx) => {
    await tx.exec(`set local role authenticated`);
    await tx.query(`select set_config('request.jwt.claim.sub', $1, true)`, [uid ?? '']);
    return fn(tx);
  });
}
async function throws(fn) {
  try { await fn(); return false; } catch { return true; }
}

const orgA = (await asUser(A, (tx) => tx.query(`select public.create_organisation('Alpha Engineering') as id`))).rows[0].id;
const orgB = (await asUser(B, (tx) => tx.query(`select public.create_organisation('Beta Cold Storage') as id`))).rows[0].id;
check('create_organisation works for a signed-in user', Boolean(orgA && orgB));

// 4. Tenant isolation: each user sees only their own org's rows.
for (const [uid, org, label] of [[A, orgA, 'A'], [B, orgB, 'B']]) {
  for (const table of ['organisation', 'workspace', 'member', 'role_assignment', 'audit_log', 'workspace_settings']) {
    const col = table === 'organisation' ? 'id' : 'org_id';
    const { rows } = await asUser(uid, (tx) => tx.query(`select count(*)::int as n, count(*) filter (where ${col} <> $1)::int as foreign from public.${table}`, [org]));
    check(`user ${label} sees no foreign rows in ${table}`, rows[0].foreign === 0 && rows[0].n > 0, `${rows[0].n} visible`);
  }
}
check('outbox is not readable by clients', await throws(() => asUser(A, (tx) => tx.query(`select count(*) from public.outbox`))));

// 5. Write paths: anonymous and Viewer cannot create; direct table writes are refused.
check('anonymous create_organisation is refused', await throws(() => asUser(null, (tx) => tx.query(`select public.create_organisation('X')`))));
const wsA = (await db.query(`select id from public.workspace where org_id = $1`, [orgA])).rows[0].id;
await db.query(`insert into public.member (id, org_id, user_id, email) values ('00000000-0000-4000-8000-0000000000cc', $1, '${C}', 'c@alpha.test')`, [orgA]);
await db.query(`insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, '00000000-0000-4000-8000-0000000000cc', $2, 'Viewer')`, [orgA, wsA]);
check('Viewer sees their workspace', (await asUser(C, (tx) => tx.query(`select count(*)::int n from public.workspace`))).rows[0].n === 1);
check('Viewer cannot create a workspace', await throws(() => asUser(C, (tx) => tx.query(`select public.create_workspace($1, 'Sneaky')`, [orgA]))));
check('Viewer cannot read the audit log', (await asUser(C, (tx) => tx.query(`select count(*)::int n from public.audit_log`))).rows[0].n === 0);
check('direct insert into workspace is refused', await throws(() => asUser(A, (tx) => tx.query(`insert into public.workspace (org_id, name) values ($1, 'Direct')`, [orgA]))));
check('Owner can create a workspace', !(await throws(() => asUser(A, (tx) => tx.query(`select public.create_workspace($1, 'EU team')`, [orgA])))));
check('user B cannot create a workspace in org A', await throws(() => asUser(B, (tx) => tx.query(`select public.create_workspace($1, 'Hijack')`, [orgA]))));

// 6. Audit log: written with actor and channel; append-only even for the table owner.
{
  const { rows } = await db.query(`select action, channel, actor_user_id from public.audit_log where org_id = $1 order by id`, [orgA]);
  check('audit rows written for org A', rows.length === 3, rows.map((r) => r.action).join(','));
  check('audit records actor and interface channel', rows.every((r) => r.actor_user_id === A && r.channel === 'interface'));
  check('audit_log rejects UPDATE', await throws(() => db.query(`update public.audit_log set action = 'x'`)));
  check('audit_log rejects DELETE', await throws(() => db.query(`delete from public.audit_log`)));
}

// 7. Workspace settings defaults (S1 gate 90/40, M11 dormancy 45, M12 attribution 90).
{
  const { rows } = await db.query(`select gate_auto_threshold a, gate_review_threshold r, dormancy_days d, attribution_window_days w from public.workspace_settings where workspace_id = $1`, [wsA]);
  const s = rows[0];
  check('workspace settings carry PRD defaults', s && s.a === 90 && s.r === 40 && s.d === 45 && s.w === 90);
}

await db.close();
if (failures.length) {
  console.error(`\n${failures.length} check(s) failed.`);
  process.exit(1);
}
console.log('\nAll migration checks passed.');
