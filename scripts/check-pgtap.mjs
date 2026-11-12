// Runs the pgTAP suites in supabase/tests/database against an in-memory Postgres (PGlite) with
// every migration applied. A local stand-in for `supabase test db`, which needs Docker; CI still
// runs the real thing. Usage: node scripts/check-pgtap.mjs [filter]
import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { pgtap } from '@electric-sql/pglite/pgtap';
import { openMigratedDb, repo } from './lib/pglite-schema.mjs';

const dir = resolve(repo, 'supabase/tests/database');
const filter = process.argv[2] ?? '';
const files = readdirSync(dir).filter((f) => f.endsWith('.test.sql') && f.includes(filter)).sort();

let failedFiles = 0;
for (const file of files) {
  // A fresh database per suite: a suite that dies mid-transaction cannot poison the next one.
  const db = await openMigratedDb({ pgtap });
  await db.exec(`alter table auth.users add column if not exists aud text, add column if not exists role text;
                 grant usage on schema extensions to public;
                 set search_path to "$user", public, extensions`);
  const lines = [];
  let crash = null;
  try {
    const results = await db.exec(readFileSync(resolve(dir, file), 'utf8'));
    for (const r of results) for (const row of r.rows) for (const v of Object.values(row)) if (typeof v === 'string') lines.push(...v.split('\n'));
  } catch (e) {
    crash = e.message;
  }
  const notOk = lines.filter((l) => /^not ok/.test(l) || /^# (Failed|Looks like)/.test(l) || /^#\s+(have|want|died|caught|wanted):?/.test(l.trim()));
  const ok = lines.filter((l) => /^ok /.test(l)).length;
  const bad = crash || lines.some((l) => /^not ok/.test(l) || /^# Looks like/.test(l));
  console.log(`${bad ? 'FAIL' : 'ok  '}  ${file}  (${ok} passed)`);
  if (bad) {
    failedFiles += 1;
    const detail = lines.filter((l) => /^not ok/.test(l) || l.startsWith('#'));
    for (const l of detail.length ? detail : notOk) console.log(`      ${l}`);
    if (crash) console.log(`      died: ${crash}`);
  }
  await db.close().catch(() => {});
}
console.log(failedFiles ? `\n${failedFiles} of ${files.length} suites failing.` : `\nAll ${files.length} pgTAP suites passed.`);
process.exit(failedFiles ? 1 : 0);
