#!/usr/bin/env node
// Post-merge check: proves nothing leaked between a branch, main, the database and Railway.
// Run after every merge: `pnpm ship:check`. Exit code 1 when anything needs attention.
//
// Checks:
//   1. Work on GitHub that is not on main (remote branches with unmerged commits).
//   2. Open pull requests.
//   3. Migrations: the newest migration on main was applied by a successful "Migrate dev" run.
//   4. Railway: web and worker each run main's latest commit (SKIPPED counts when the service
//      had nothing to rebuild for that commit).
//   5. Worktrees: finished ones still registered, uncommitted edits, folders git no longer tracks.
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

const repo = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
const GH = process.env.GH_BIN || (existsSync('C:/Program Files/GitHub CLI/gh.exe') ? 'C:/Program Files/GitHub CLI/gh.exe' : 'gh');
const SERVICES = ['web', 'worker'];

const run = (cmd, args, opts = {}) => {
  try {
    return execFileSync(cmd, args, { cwd: repo, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], ...opts }).trim();
  } catch (e) {
    return opts.allowFail ? null : (() => { throw e; })();
  }
};
const git = (...a) => run('git', a);
const short = (sha) => (sha ?? '').slice(0, 7);
const subject = (sha) => run('git', ['log', '-1', '--format=%s', sha], { allowFail: true }) ?? '';

let problems = 0;
const ok = (msg) => console.log(`  ✓ ${msg}`);
const bad = (msg) => { problems++; console.log(`  ✗ ${msg}`); };
const info = (msg) => console.log(`    ${msg}`);
const section = (t) => console.log(`\n${t}`);

git('fetch', '--prune', '--quiet');
const main = git('rev-parse', 'origin/main');
console.log(`main is ${short(main)} · ${subject(main)}`);

// 1. Unmerged work on GitHub
section('1. Work on GitHub that is not on main');
const openPrs = JSON.parse(run(GH, ['pr', 'list', '--state', 'open', '--json', 'number,headRefName,title,baseRefName'], { allowFail: true }) ?? '[]');
const prByBranch = new Map(openPrs.map((p) => [p.headRefName, p]));
const remotes = git('for-each-ref', '--format=%(refname:short)', 'refs/remotes/origin')
  .split('\n')
  .filter((r) => r && r !== 'origin/main' && r !== 'origin/HEAD' && r !== 'origin');
let leaks = 0;
for (const r of remotes) {
  const ahead = git('rev-list', '--count', `origin/main..${r}`);
  if (ahead === '0') continue;
  const branch = r.replace(/^origin\//, '');
  const pr = prByBranch.get(branch);
  const commits = git('log', '--format=%h %s', `origin/main..${r}`).split('\n');
  if (pr) {
    info(`${branch}: ${ahead} commit(s), in open PR #${pr.number}`);
  } else {
    leaks++;
    bad(`${branch}: ${ahead} commit(s) not on main and in no open PR`);
    commits.slice(0, 5).forEach((c) => info(`- ${c}`));
  }
}
if (!leaks) ok('every commit on GitHub is on main or in an open PR');

// 2. Open PRs
section('2. Open pull requests');
if (!openPrs.length) ok('none open');
for (const p of openPrs) info(`#${p.number} ${p.headRefName} → ${p.baseRefName}: ${p.title}`);
for (const p of openPrs) if (p.baseRefName !== 'main') bad(`#${p.number} targets ${p.baseRefName}, not main: retarget it before its base merges`);

// 3. Migrations
section('3. Database migrations (dev)');
const lastMigration = git('log', '-1', '--format=%H', 'origin/main', '--', 'supabase/migrations');
const runs = JSON.parse(run(GH, ['run', 'list', '--workflow', 'Migrate dev', '--branch', 'main', '--limit', '20', '--json', 'headSha,conclusion,status'], { allowFail: true }) ?? '[]');
const applied = runs.some((r) => r.conclusion === 'success' && run('git', ['merge-base', '--is-ancestor', lastMigration, r.headSha], { allowFail: true }) !== null);
const pending = runs.some((r) => r.status !== 'completed');
const newest = readdirSync(resolve(repo, 'supabase/migrations')).filter((f) => f.endsWith('.sql')).sort().pop();
if (applied) ok(`newest migration ${newest} applied (from ${short(lastMigration)})`);
else if (pending) bad(`Migrate dev is still running for ${newest}: re-run ship:check when it finishes`);
else bad(`no successful Migrate dev run includes ${short(lastMigration)} (${newest})`);

// 4. Railway
section('4. Railway deployments');
for (const service of SERVICES) {
  const out = run('railway', ['deployment', 'list', '--service', service, '--json'], { allowFail: true, shell: process.platform === 'win32' });
  if (!out) { bad(`${service}: could not read Railway (run \`railway login\` and \`railway link\`)`); continue; }
  const list = JSON.parse(out);
  const latest = list[0];
  const sha = latest?.meta?.commitHash;
  const lastGood = list.find((d) => d.status === 'SUCCESS');
  const label = (d) => `${d.status} ${short(d?.meta?.commitHash)} · ${(d?.meta?.commitMessage ?? '').split('\n')[0].slice(0, 60)}`;
  if (sha === main && (latest.status === 'SUCCESS' || latest.status === 'SKIPPED')) {
    ok(`${service}: on main ${short(main)} (${latest.status === 'SKIPPED' ? 'nothing to rebuild; running ' + short(lastGood?.meta?.commitHash) : 'deployed'})`);
  } else if (sha === main) {
    bad(`${service}: deploying main now (${latest.status}); re-run when it finishes`);
  } else {
    bad(`${service}: not on main. Latest deployment: ${latest ? label(latest) : 'none'}`);
    info(main !== sha ? 'Railway did not build main\'s newest commit: check the service\'s "Branch connected to production" is main.' : '');
  }
}

// 5. Worktrees
section('5. Worktrees');
const blocks = git('worktree', 'list', '--porcelain').split('\n\n').filter(Boolean);
// The first entry is the primary checkout (the project folder itself); the rest are worktrees.
const primary = resolve(/^worktree (.+)$/m.exec(blocks[0])?.[1] ?? repo);
const registered = new Set();
for (const b of blocks.slice(1)) {
  const path = /^worktree (.+)$/m.exec(b)?.[1];
  const branch = /^branch refs\/heads\/(.+)$/m.exec(b)?.[1];
  if (!path) continue;
  registered.add(resolve(path));
  const dirty = run('git', ['-C', path, 'status', '--porcelain'], { allowFail: true }) ?? '';
  const merged = branch && git('rev-list', '--count', `origin/main..${branch}`) === '0';
  const changes = dirty ? dirty.split('\n').length : 0;
  if (merged && !changes) bad(`${branch}: merged into main; remove it (git worktree remove --force "${path}")`);
  else if (merged) bad(`${branch}: merged into main but has ${changes} uncommitted change(s): keep or discard them, then remove it`);
  else info(`${branch}: in progress${changes ? `, ${changes} uncommitted change(s)` : ''}`);
}
const wtDir = resolve(primary, '.claude/worktrees');
if (existsSync(wtDir)) {
  for (const d of readdirSync(wtDir)) {
    const p = resolve(wtDir, d);
    if (!registered.has(p)) bad(`.claude/worktrees/${d}: folder git no longer tracks (left-over copy); delete it`);
  }
}
if (blocks.length <= 1) ok('no extra worktrees');

console.log(problems ? `\n${problems} item(s) need attention.` : '\nAll clear: nothing leaked.');
process.exit(problems ? 1 : 0);
