#!/usr/bin/env node
// Schema check for application SQL without Docker: applies supabase/migrations/*.sql to an
// in-memory Postgres (PGlite), then prepares and plans every SQL statement the Python services
// execute, as service_role. Catches unknown columns, wrong RPC signatures and missing grants.
// Also checks that every table and RPC the web app names exists and is reachable by a signed-in user.
import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { openMigratedDb, repo } from './lib/pglite-schema.mjs';

const db = await openMigratedDb();

const failures = [];
const skipped = [];

function toDollar(sql) {
  let n = 0;
  return [sql.replace(/%%|%s/g, (m) => (m === '%%' ? '%' : `$${++n}`)), n];
}

// ── Python SQL ───────────────────────────────────────────────────────────────
const statements = JSON.parse(
  // Relative path: the repo path may contain spaces, which the Windows shell would split.
  execFileSync('uv', ['run', 'python', 'scripts/extract_sql.py'], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
    cwd: repo,
    shell: process.platform === 'win32',
  }),
);

for (const s of statements) {
  const where = `${s.file}:${s.line}`;
  if (s.sql === null) {
    skipped.push(`${where}  (SQL built at runtime)`);
    continue;
  }
  const head = s.sql.trim().split(/\s+/)[0].toLowerCase();
  if (!['select', 'insert', 'update', 'delete', 'with'].includes(head)) {
    skipped.push(`${where}  (${head} statement)`);
    continue;
  }
  const [sql, n] = toDollar(s.sql);
  try {
    await db.transaction(async (tx) => {
      await tx.exec(`set local role service_role`);
      await tx.exec(`set local search_path to procrastinate, public, extensions`); // signal_core.db
      await tx.exec(`prepare chk as ${sql}`);
      const args = n ? `(${Array(n).fill('null').join(',')})` : '';
      await tx.exec(`set local plan_cache_mode = force_generic_plan`);
      await tx.exec(`explain execute chk${args}`);
      await tx.rollback();
    });
  } catch (err) {
    if (err.code === '42P18') skipped.push(`${where}  (parameter type cannot be inferred)`);
    else failures.push(`${where}\n      ${err.message}\n      ${s.sql.trim().replace(/\s+/g, ' ').slice(0, 160)}`);
  } finally {
    await db.exec(`deallocate all`).catch(() => {});
  }
}

// ── Web app: tables and RPCs named in supabase-js calls ───────────────────────
function* walk(dir) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === '.next') continue;
    const p = join(dir, e.name);
    if (e.isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx)$/.test(e.name)) yield p;
  }
}
const webRoot = resolve(repo, 'apps/web/src');
for (const file of walk(webRoot)) {
  const text = readFileSync(file, 'utf8');
  const rel = file.slice(repo.length + 1).replaceAll('\\', '/');
  for (const m of text.matchAll(/\.from\(\s*['"]([a-z_]+)['"]\s*\)/g)) {
    const line = text.slice(0, m.index).split('\n').length;
    const { rows } = await db.query(
      `select has_any_column_privilege('authenticated', c.oid, 'select') ok from pg_class c
        join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'public' and c.relname = $1`,
      [m[1]],
    );
    if (!rows.length) failures.push(`${rel}:${line}\n      table public.${m[1]} does not exist`);
    else if (!rows[0].ok) failures.push(`${rel}:${line}\n      authenticated cannot select from public.${m[1]}`);
  }
  for (const m of text.matchAll(/\.rpc\(\s*['"]([a-z_]+)['"]/g)) {
    const line = text.slice(0, m.index).split('\n').length;
    const { rows } = await db.query(
      `select bool_or(has_function_privilege('authenticated', p.oid, 'execute')) ok from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public' and p.proname = $1`,
      [m[1]],
    );
    if (rows[0].ok === null) failures.push(`${rel}:${line}\n      function public.${m[1]} does not exist`);
    else if (!rows[0].ok) failures.push(`${rel}:${line}\n      authenticated cannot execute public.${m[1]}`);
  }
}

await db.close();
console.log(`${statements.length} Python statements checked, ${skipped.length} skipped, ${failures.length} failing.`);
if (process.argv.includes('--verbose')) for (const s of skipped) console.log(`skip  ${s}`);
for (const f of failures) console.log(`FAIL  ${f}`);
process.exit(failures.length ? 1 : 0);
