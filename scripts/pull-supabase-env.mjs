#!/usr/bin/env node
// Copies a Supabase project's URL and API keys into local env files WITHOUT printing them.
//   .env at the repo root (read by both the web app and the worker)
// Prerequisite (once): `pnpm exec supabase login` (opens your browser).
// Usage: pnpm env:pull <project-ref>
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ref = process.argv[2];
if (!ref || !/^[a-z0-9]{20}$/.test(ref)) {
  console.error('Usage: pnpm env:pull <project-ref>   (20 lowercase letters/digits, from Project Settings → General)');
  process.exit(1);
}

let raw;
try {
  raw = execFileSync(
    process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm',
    ['exec', 'supabase', 'projects', 'api-keys', '--project-ref', ref, '--reveal', '--output', 'json'],
    { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], shell: process.platform === 'win32' },
  );
} catch (err) {
  console.error('Could not read the keys. Run `pnpm exec supabase login` first, and check the project ref.');
  console.error(String(err.stderr ?? '').split('\n').filter((l) => !/sb_(secret|publishable)_|eyJ/.test(l)).join('\n'));
  process.exit(1);
}

const keys = JSON.parse(raw.slice(raw.indexOf('[')));
const val = (k) => k.api_key ?? k.key ?? '';
const find = (pred) => keys.map(val).find(pred) ?? '';
const byName = (n) => val(keys.find((k) => k.name === n) ?? {});
const publishable = find((v) => v.startsWith('sb_publishable_')) || byName('anon');
const secret = find((v) => v.startsWith('sb_secret_')) || byName('service_role');
if (!publishable) {
  console.error('No publishable/anon key found on this project.');
  process.exit(1);
}

function upsert(file, entries) {
  const path = resolve(repo, file);
  let text = existsSync(path) ? readFileSync(path, 'utf8') : '';
  for (const [name, value] of Object.entries(entries)) {
    if (!value) continue;
    const line = `${name}=${value}`;
    const re = new RegExp(`^${name}=.*$`, 'm');
    text = re.test(text) ? text.replace(re, line) : `${text}${text && !text.endsWith('\n') ? '\n' : ''}${line}\n`;
  }
  writeFileSync(path, text);
  console.log(`updated ${file}: ${Object.keys(entries).filter((k) => entries[k]).join(', ')}`);
}

upsert('.env', {
  NEXT_PUBLIC_SUPABASE_URL: `https://${ref}.supabase.co`,
  NEXT_PUBLIC_SUPABASE_ANON_KEY: publishable,
  SUPABASE_PROJECT_REF: ref,
  SUPABASE_SERVICE_ROLE_KEY: secret,
});
console.log('\nStill to add by hand in .env: DATABASE_URL_SESSION (Supabase → Connect → Session pooler; it contains your DB password).');
