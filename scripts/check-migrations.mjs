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

// ── Phase 0b: foundation schema (F2–F8) and health ─────────────────────────
const one = async (sql, params) => (await db.query(sql, params)).rows[0];
const asWriter = (fn) =>
  db.transaction(async (tx) => {
    await tx.query(`select set_config('app.writer', 'derivation', true)`);
    return fn(tx);
  });

// 8. Canary end to end (observed → resolved → fact → derived), twice.
{
  const r1 = (await one(`select app.run_canary() as r`)).r;
  check('canary run 1 passes end to end', r1.status === 'ok', JSON.stringify(r1).slice(0, 160));
  const r2 = (await one(`select app.run_canary() as r`)).r;
  check('canary run 2 reuses the resolved entity', r2.status === 'ok' && r2.new_entity === false && r2.entity_id === r1.entity_id);
  const v = await one(`select count(*)::int n, max(cardinality(claim_ids))::int c from public.verdict where entity_id = $1`, [r1.entity_id]);
  check('canary verdict recomputed in place, citing its claim', v.n === 1 && v.c === 1);
  const ev = await one(`select array_agg(distinct event::text order by event::text) e from public.outbox where org_id = '00000000-0000-4000-a000-0000000000c1'`);
  check('canary emits catalogue events', ['claim.created', 'entity.created', 'source_record.created', 'verdict.changed'].every((x) => ev.e.includes(x)), ev.e.join(','));
  const h = await one(`select status from public.health_check where component = 'pipeline.canary' order by id desc limit 1`);
  check('canary records health ok', h.status === 'ok');
  const sr = await one(`select extraction_status from public.source_record where id = $1`, [r2.source_record_id]);
  check('canary marks its source record extracted', sr.extraction_status === 'extracted');
}

// Fixtures in org A for the remaining checks.
const ext = (await one(`insert into public.extractor_version (capability, prompt_version, model, parser_version)
                         values ('extract', 'v1', 'test', 'v1') returning id`)).id;
const srA = (await one(`insert into public.source_record (org_id, connector_id, source_type, fetched_at, content_hash)
                         values ($1, 'test', 'job_post', now(), 'hash-1') returning id`, [orgA])).id;
const coA = (await one(`insert into public.entity (org_id, entity_type, display_name) values ($1, 'company', 'Van Dijk Engineering') returning id`, [orgA])).id;
const pBool = (await one(`insert into public.predicate (key, label, value_type, decay_half_life_days) values ('test.hiring', 'Hiring', 'boolean', 90) returning id`)).id;
const pEnum = (await one(`insert into public.predicate (key, label, value_type, allowed_values) values ('test.uses_tool', 'Uses tool', 'enum', '["Revit","ArchiCAD"]') returning id`)).id;
const pDate = (await one(`insert into public.predicate (key, label, value_type) values ('test.founded', 'Founded', 'date') returning id`)).id;
const claimSql = `insert into public.claim (org_id, entity_id, predicate_id, value, source_record_id, observed_at, extractor_version_id, confidence, scope)
                  values ($1, $2, $3, $4::jsonb, $5, now(), $6, 80, coalesce($7, 'tenant')::public.data_scope) returning id, expires_at`;

// 9. Typed predicates (F3).
check('claim accepts a valid enum value', !(await throws(() => db.query(claimSql, [orgA, coA, pEnum, '"Revit"', srA, ext, null]))));
check('claim rejects out-of-registry value', await throws(() => db.query(claimSql, [orgA, coA, pEnum, '"AutoCAD"', srA, ext, null])));
check('claim rejects wrong value type', await throws(() => db.query(claimSql, [orgA, coA, pBool, '"yes"', srA, ext, null])));
check('claim rejects a non-date for a date predicate', await throws(() => db.query(claimSql, [orgA, coA, pDate, '"last spring"', srA, ext, null])));
{
  const r = (await db.query(claimSql, [orgA, coA, pBool, 'true', srA, ext, null])).rows[0];
  check('claim expiry defaults from the predicate half-life', r.expires_at !== null);
}
check('claim without a source record is refused', await throws(() => db.query(claimSql, [orgA, coA, pBool, 'true', null, ext, null])));

// 10. People are always tenant-scoped (F1).
check('person entity cannot be shared', await throws(() => db.query(`insert into public.entity (org_id, entity_type, display_name, scope) values ($1, 'person', 'Jane Doe', 'shared')`, [orgA])));
const personA = (await one(`insert into public.entity (org_id, entity_type, display_name) values ($1, 'person', 'Jane Doe') returning id`, [orgA])).id;
check('person data is always tenant (claims)', await throws(() => db.query(claimSql, [orgA, personA, pBool, 'true', srA, ext, 'shared'])));
check('relationship checks endpoint types', await throws(() => db.query(
  `insert into public.relationship (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence) values ($1, $2, 'works_at', $3, $4, 90)`,
  [orgA, coA, personA, srA])));
check('person works_at company is accepted', !(await throws(() => db.query(
  `insert into public.relationship (org_id, from_entity_id, relation, to_entity_id, source_record_id, confidence, attributes) values ($1, $2, 'works_at', $3, $4, 90, '{"title":"BIM lead"}')`,
  [orgA, personA, coA, srA]))));

// 11. Shared facts stay hidden while the platform switch is off (F1).
{
  const shared = (await one(`insert into public.entity (org_id, entity_type, display_name, scope) values ($1, 'company', 'Public Port Authority', 'shared') returning id`, [orgA])).id;
  const seenByB = async () => (await asUser(B, (tx) => tx.query(`select count(*)::int n from public.entity where id = $1`, [shared]))).rows[0].n;
  check('shared facts hidden while switch off', (await seenByB()) === 0);
  await db.query(`update public.platform_setting set value = 'true' where key = 'shared_facts_enabled'`);
  check('shared facts visible once the switch is on (no migration)', (await seenByB()) === 1);
  await db.query(`update public.platform_setting set value = 'false' where key = 'shared_facts_enabled'`);
  check('tenant facts never cross organisations', (await asUser(B, (tx) => tx.query(`select count(*)::int n from public.entity where org_id = $1`, [orgA]))).rows[0].n === 0);
  check('org members read their own facts', (await asUser(A, (tx) => tx.query(`select count(*)::int n from public.claim`))).rows[0].n >= 2);
  check('clients cannot read the canary organisation', (await asUser(A, (tx) => tx.query(`select count(*)::int n from public.verdict`))).rows[0].n === 0);
}

// 12. Provenance is immutable; re-fetching never duplicates (M2, F3).
check('source_record dedupes on content hash', await throws(() => db.query(
  `insert into public.source_record (org_id, connector_id, source_type, fetched_at, content_hash) values ($1, 'test', 'job_post', now(), 'hash-1')`, [orgA])));
check('source_record payload is immutable', await throws(() => db.query(`update public.source_record set url = 'https://x.test' where id = $1`, [srA])));
check('source_record status can advance', !(await throws(() => db.query(`update public.source_record set extraction_status = 'irrelevant' where id = $1`, [srA]))));
check('claims cannot be deleted', await throws(() => db.query(`delete from public.claim where entity_id = $1`, [coA])));
check('claim value is immutable (retract instead)', await throws(() => db.query(`update public.claim set value = 'false' where predicate_id = $1`, [pBool])));
check('extractor_version is immutable', await throws(() => db.query(`update public.extractor_version set model = 'x' where id = $1`, [ext])));
check('entities are never deleted (merge instead)', await throws(() => db.query(`delete from public.entity where id = $1`, [coA])));

// 13. Merges never delete; references resolve through the canonical entity (F2).
{
  const b = (await one(`insert into public.entity (org_id, entity_type, display_name) values ($1, 'company', 'Van Dijk Ingenieurs') returning id`, [orgA])).id;
  const c = (await one(`insert into public.entity (org_id, entity_type, display_name) values ($1, 'company', 'Van Dijk Group') returning id`, [orgA])).id;
  await db.query(`update public.entity set status = 'merged', merged_into = $2 where id = $1`, [b, c]);
  await db.query(`update public.entity set status = 'merged', merged_into = $2 where id = $1`, [coA, b]);
  check('canonical_entity follows a merge chain', (await one(`select app.canonical_entity($1) id`, [coA])).id === c);
  check('merged status requires a target', await throws(() => db.query(`update public.entity set status = 'merged', merged_into = null where id = $1`, [c])));
  await db.query(`update public.entity set status = 'active', merged_into = null where id = $1`, [coA]);
  check('unmerge restores the entity', (await one(`select app.canonical_entity($1) id`, [coA])).id === coA);
}

// 14. Briefs: versions are immutable and sequential (F5).
const briefA = (await one(`insert into public.brief (org_id, workspace_id, name) values ($1, $2, 'CAD adopters NL') returning id`, [orgA, wsA])).id;
const bvSql = `insert into public.brief_version (brief_id, org_id, workspace_id, version, config, rubric, weights) values ($1, $2, $3, $4, '{}', '{}', '{}') returning id`;
const bv1 = (await one(bvSql, [briefA, orgA, wsA, 1])).id;
check('brief versions are sequential', await throws(() => db.query(bvSql, [briefA, orgA, wsA, 3])));
check('brief versions are immutable', await throws(() => db.query(`update public.brief_version set config = '{"x":1}' where id = $1`, [bv1])));
check('Viewer can read Briefs in their workspace', (await asUser(C, (tx) => tx.query(`select count(*)::int n from public.brief`))).rows[0].n === 1);

// 15. Derived tables only accept the derivation engine (F7).
const accA = (await one(`insert into public.account (org_id, workspace_id, entity_id) values ($1, $2, $3) returning id`, [orgA, wsA, coA])).id;
const verdictSql = `insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids) values ($1, $2, $3, $4, $5, $6) returning id`;
check('derived tables reject direct writes', await throws(() => db.query(verdictSql, [orgA, wsA, bv1, coA, 'Likely', []])));
check('derivation writer can write a verdict', !(await throws(() => asWriter((tx) => tx.query(verdictSql, [orgA, wsA, bv1, coA, 'Likely', []])))));
check('Confirmed verdict needs claims', await throws(() => asWriter((tx) => tx.query(
  `update public.verdict set value = 'Confirmed', claim_ids = '{}' where entity_id = $1`, [coA]))));
check('account.stage cannot be set by hand', await throws(() => db.query(`update public.account set stage = 'Engaged' where id = $1`, [accA])));
check('account owner can still change', !(await throws(() => db.query(`update public.account set owner_member_id = null where id = $1`, [accA]))));
check('derivation writer can move a stage', !(await throws(() => asWriter((tx) => tx.query(`update public.account set stage = 'Queued' where id = $1`, [accA])))));
check('score rejects out-of-range dimensions', await throws(() => asWriter((tx) => tx.query(
  `insert into public.score (org_id, workspace_id, account_id, brief_version_id, fit, band) values ($1, $2, $3, $4, 140, 'High')`, [orgA, wsA, accA, bv1]))));

// 16. Activity and feedback are append-only; replays never double count (F6).
{
  const integ = '00000000-0000-4000-8000-0000000000e1';
  const actSql = `insert into public.activity (org_id, workspace_id, account_id, kind, integration_id, external_id, occurred_at) values ($1, $2, $3, 'message_sent', $4, 'msg-1', now()) returning id`;
  const act = (await one(actSql, [orgA, wsA, accA, integ])).id;
  check('replayed external event is rejected', await throws(() => db.query(actSql, [orgA, wsA, accA, integ])));
  check('activity is append-only', await throws(() => db.query(`update public.activity set channel = 'x' where id = $1`, [act])));
  const memberA = (await one(`select id from public.member where user_id = $1`, [A])).id;
  const fb = (await one(`insert into public.feedback (org_id, workspace_id, member_id, target_type, target_id, action) values ($1, $2, $3, 'verdict', $4, 'confirm') returning id`, [orgA, wsA, memberA, coA])).id;
  check('feedback is append-only', await throws(() => db.query(`delete from public.feedback where id = $1`, [fb])));
  check('correction feedback needs a value', await throws(() => db.query(
    `insert into public.feedback (org_id, workspace_id, member_id, target_type, target_id, action) values ($1, $2, $3, 'verdict', $4, 'correct')`, [orgA, wsA, memberA, coA])));
}

// 17. Health incidents need an owner and a resolution note (M17).
{
  await db.query(`select app.record_health('radar.jobs', 'silent', '{"error":"0 records in expected window"}', 'M2')`);
  const inc = await one(`select id, status, owner from public.health_incident where component = 'radar.jobs'`);
  check('silence opens an incident with an owner', inc.status === 'open' && inc.owner === 'M2');
  check('incident cannot close without a note', await throws(() => db.query(`update public.health_incident set status = 'resolved', resolved_at = now() where id = $1`, [inc.id])));
  await db.query(`select app.record_health('radar.jobs', 'ok', '{}')`);
  check('recovery resolves the incident with a note', (await one(`select status from public.health_incident where id = $1`, [inc.id])).status === 'resolved');
}

// 18. Memory chunks accept 1024-d embeddings and full-text search (M15/S9).
check('memory_chunk stores an embedding', !(await throws(() => asWriter((tx) => tx.query(
  `insert into public.memory_chunk (org_id, workspace_id, entity_id, doc_type, doc_id, chunk_index, content, embedding, embedding_model)
   values ($1, $2, $3, 'source_record', $4, 0, 'Hiring Revit technicians in Rotterdam', $5::extensions.vector, 'voyage-3.5')`,
  [orgA, wsA, coA, srA, `[${Array(1024).fill(0.01).join(',')}]`])))));
check('memory_chunk full-text search works', (await one(
  `select count(*)::int n from public.memory_chunk where content_tsv @@ plainto_tsquery('simple', 'revit rotterdam')`)).n === 1);

// ── Phase 0c: organisations and access (M14) ─────────────────────────────────
{
  const D = '00000000-0000-4000-8000-00000000000d'; // d@alpha.test
  const E = '00000000-0000-4000-8000-00000000000e'; // e@other.test
  const G = '00000000-0000-4000-8000-000000000011'; // g@other.test
  const H = '00000000-0000-4000-8000-000000000012'; // h@alpha.test
  await db.exec(`insert into auth.users (id, email) values ('${D}','d@alpha.test'), ('${E}','e@other.test'),
                 ('${G}','g@other.test'), ('${H}','h@alpha.test')`);
  const rpc = (uid, sql, params) => asUser(uid, (tx) => tx.query(sql, params));
  const memberOf = async (uid) => (await one(`select id from public.member where user_id = $1 and org_id = $2`, [uid, orgA]))?.id;

  // Invite → accept by token.
  const tokD = (await rpc(A, `select public.invite_member($1, 'D@Alpha.test', 'Editor', $2) t`, [orgA, wsA])).rows[0].t;
  check('invitation token is stored only as a hash', (await one(`select count(*)::int n from public.invitation where token_hash = $1`, [tokD])).n === 0);
  check('invitee accepts by token', !(await throws(() => rpc(D, `select public.accept_invitation($1)`, [tokD]))));
  check('accepted Editor sees the workspace', (await rpc(D, `select count(*)::int n from public.workspace`)).rows[0].n === 1);
  check('an invitation is used once', await throws(() => rpc(D, `select public.accept_invitation($1)`, [tokD])));
  check('clients cannot read token hashes', await throws(() => rpc(A, `select token_hash from public.invitation`)));

  const mA = await memberOf(A);
  const mC = await memberOf(C);
  const mD = await memberOf(D);

  // Forwarded link.
  const tokF = (await rpc(A, `select public.invite_member($1, 'f@alpha.test', 'Viewer', $2) t`, [orgA, wsA])).rows[0].t;
  check('invitation accepted only by its email', await throws(() => rpc(E, `select public.accept_invitation($1)`, [tokF])));

  // Permission matrix.
  check('Viewer cannot invite', await throws(() => rpc(C, `select public.invite_member($1, 'x@alpha.test', 'Viewer', $2)`, [orgA, wsA])));
  check('Editor cannot change roles', await throws(() => rpc(D, `select public.set_member_role($1, 'Editor', $2)`, [mC, wsA])));
  check('Owner makes D an Admin', !(await throws(() => rpc(A, `select public.set_member_role($1, 'Admin')`, [mD]))));
  check('admin cannot grant Owner', await throws(() => rpc(D, `select public.set_member_role($1, 'Owner')`, [mC])));
  check('admin cannot invite an Owner', await throws(() => rpc(D, `select public.invite_member($1, 'y@alpha.test', 'Owner')`, [orgA])));
  check('admin can invite an Editor', !(await throws(() => rpc(D, `select public.invite_member($1, 'z@alpha.test', 'Editor', $2)`, [orgA, wsA]))));
  check('admin cannot remove the Owner', await throws(() => rpc(D, `select public.remove_member($1)`, [mA])));
  check('workspace roles need a workspace', await throws(() => rpc(A, `select public.set_member_role($1, 'Viewer')`, [mC])));
  check('member of another org cannot manage this one', await throws(() => rpc(B, `select public.invite_member($1, 'q@alpha.test', 'Viewer', $2)`, [orgA, wsA])));

  // At least one Owner.
  check('last owner cannot be removed', await throws(() => rpc(A, `select public.remove_member($1)`, [mA])));
  check('last owner cannot be demoted', await throws(() => rpc(A, `select public.set_member_role($1, 'Admin')`, [mA])));

  // Removing a member ends access immediately.
  check('Owner removes a member', !(await throws(() => rpc(A, `select public.remove_member($1)`, [mD]))));
  check('removed member sees nothing', (await rpc(D, `select (select count(*) from public.workspace)::int + (select count(*) from public.organisation)::int n`)).rows[0].n === 0);

  // Allowed domains.
  const tokG = (await rpc(A, `select public.invite_member($1, 'g@other.test', 'Viewer', $2) t`, [orgA, wsA])).rows[0].t;
  check('invalid domain is rejected', await throws(() => rpc(A, `select public.set_allowed_domains($1, array['not a domain'])`, [orgA])));
  check('Owner sets allowed domains', !(await throws(() => rpc(A, `select public.set_allowed_domains($1, array[' Alpha.TEST '])`, [orgA]))));
  check('domains are normalised', (await one(`select allowed_email_domains d from public.organisation where id = $1`, [orgA])).d.join() === 'alpha.test');
  check('domain outside allowed list refused at invite', await throws(() => rpc(A, `select public.invite_member($1, 'k@other.test', 'Viewer', $2)`, [orgA, wsA])));
  check('domain outside allowed list refused at accept', await throws(() => rpc(G, `select public.accept_invitation($1)`, [tokG])));

  // In-app acceptance without a token.
  await rpc(A, `select public.invite_member($1, 'h@alpha.test', 'Operator', $2)`, [orgA, wsA]);
  const mine = (await rpc(H, `select * from public.my_invitations()`)).rows;
  check('my_invitations lists only my pending invitations', mine.length === 1 && mine[0].role === 'Operator' && mine[0].org_name === 'Alpha Engineering');
  check('others cannot see my invitations', (await rpc(E, `select count(*)::int n from public.my_invitations()`)).rows[0].n === 0);
  check('accept_my_invitation joins the workspace', !(await throws(() => rpc(H, `select public.accept_my_invitation($1)`, [mine[0].id]))));

  const mH = await memberOf(H);

  // Revoke, teams, audit.
  const pend = (await one(`select id from public.invitation where email = 'z@alpha.test'`)).id;
  check('Owner revokes an invitation', !(await throws(() => rpc(A, `select public.revoke_invitation($1)`, [pend]))));
  const team = (await rpc(A, `select public.create_team($1, 'Benelux') id`, [orgA])).rows[0].id;
  check('Owner adds a member to a team', !(await throws(() => rpc(A, `select public.set_team_member($1, $2, true)`, [team, mH]))));
  check('Operator cannot manage teams', await throws(() => rpc(H, `select public.create_team($1, 'Rogue')`, [orgA])));
  const actions = (await one(`select array_agg(distinct action) a from public.audit_log where org_id = $1`, [orgA])).a;
  check('every access change is audited', ['invitation.created', 'invitation.accepted', 'invitation.revoked', 'role.changed',
    'member.removed', 'organisation.domains_changed', 'team.created', 'team.member_added'].every((x) => actions.includes(x)), actions.join(','));
}

await db.close();
if (failures.length) {
  console.error(`\n${failures.length} check(s) failed.`);
  process.exit(1);
}
console.log('\nAll migration checks passed.');
