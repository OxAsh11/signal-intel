/**
 * Helpers for the end-to-end suite. They talk to the local Supabase stack directly:
 *   E2E_SUPABASE_URL, E2E_ANON_KEY, E2E_SERVICE_ROLE_KEY: from `supabase status -o env`
 *   E2E_DB_URL: the local Postgres (postgresql://postgres:postgres@127.0.0.1:54322/postgres)
 * Never point these at a shared database: the suite creates users and organisations.
 */
import { randomUUID } from 'node:crypto';
import { expect, type BrowserContext, type Page } from '@playwright/test';
import { createClient } from '@supabase/supabase-js';
import { Client } from 'pg';

function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`${name} is not set. Run \`supabase start\` and export the E2E_* variables.`);
  return v;
}

const SUPABASE_URL = () => need('E2E_SUPABASE_URL');
const host = new URL(process.env.E2E_SUPABASE_URL ?? 'http://127.0.0.1:54321').hostname;
if (!['127.0.0.1', 'localhost'].includes(host)) {
  throw new Error(`E2E_SUPABASE_URL must be a local Supabase stack, not ${host}.`);
}

const admin = () => createClient(SUPABASE_URL(), need('E2E_SERVICE_ROLE_KEY'), { auth: { persistSession: false } });
const anon = () => createClient(SUPABASE_URL(), need('E2E_ANON_KEY'), { auth: { persistSession: false } });

/** A fresh, confirmed user with a unique email. */
export async function newUser(label: string): Promise<{ id: string; email: string }> {
  const email = `${label}-${randomUUID().slice(0, 8)}@e2e.test`;
  const { data, error } = await admin().auth.admin.createUser({ email, email_confirm: true });
  if (error || !data.user) throw new Error(`createUser: ${error?.message}`);
  return { id: data.user.id, email };
}

/**
 * Signs the browser in as `email` the way the magic link would: a real Supabase session stored in
 * the @supabase/ssr cookie ("base64-" + base64url JSON, split into 3180-character chunks).
 */
export async function signIn(context: BrowserContext, email: string): Promise<void> {
  const { data: link, error } = await admin().auth.admin.generateLink({ type: 'magiclink', email });
  if (error) throw new Error(`generateLink: ${error.message}`);
  const { data, error: vErr } = await anon().auth.verifyOtp({ token_hash: link.properties.hashed_token, type: 'magiclink' });
  if (vErr || !data.session) throw new Error(`verifyOtp: ${vErr?.message}`);
  const ref = new URL(SUPABASE_URL()).hostname.split('.')[0];
  const name = `sb-${ref}-auth-token`;
  const value = 'base64-' + Buffer.from(JSON.stringify(data.session)).toString('base64url');
  const chunks = value.length <= 3180 ? [{ name, value }] : Array.from({ length: Math.ceil(value.length / 3180) }, (_, i) => ({ name: `${name}.${i}`, value: value.slice(i * 3180, (i + 1) * 3180) }));
  await context.addCookies(chunks.map((c) => ({ ...c, domain: 'localhost', path: '/', sameSite: 'Lax' as const })));
}

/** Runs SQL as the database owner: used only to play the worker's part (the AI draft). */
export async function sql<T = Record<string, unknown>>(text: string, params: unknown[] = []): Promise<T[]> {
  const client = new Client({ connectionString: need('E2E_DB_URL') });
  await client.connect();
  try {
    const result = await client.query(text, params);
    // pg returns one Result per statement for a multi-statement query. Seed helpers write in a
    // transaction and return ids from a final SELECT, so use the last statement that produced rows.
    const results = Array.isArray(result) ? result : [result];
    const returning = results.filter((r) => r.rows.length > 0);
    const last = returning[returning.length - 1] ?? results[results.length - 1];
    return (last?.rows ?? []) as T[];
  } finally {
    await client.end();
  }
}

/** Onboarding through the real screen: a new user creates their organisation and first workspace. */
export async function onboard(page: Page, org: string, workspace = 'Default'): Promise<void> {
  await page.goto('/onboarding');
  await page.getByLabel('Organisation name').fill(org);
  await page.getByLabel('First workspace').fill(workspace);
  await page.getByRole('button', { name: 'Create organisation' }).click();
  await page.waitForURL((u) => u.pathname === '/');
}

/** What the worker's AI intake would save for the FlytBase example (shape of BriefDraft). */
export const FLYTBASE_DRAFT = {
  offer: 'FlytBase: enterprise drone autonomy software that runs docked drones remotely, including BVLOS flights.',
  hypothesis:
    'Operators of large physical sites announce a drone pilot, a docking-station rollout, a BVLOS approval or a tender for drone inspection or security shortly before they buy autonomy software.',
  examples: {
    positive: [
      'A solar farm operator publishes a tender for automated drone inspection',
      'A port authority announces a drone-in-a-box security pilot',
      'A mining company receives a BVLOS approval for remote drone operations',
    ],
    negative: ['A wedding photographer buying a consumer drone', 'A drone manufacturer selling hardware only'],
  },
  sources: { radar_types: ['news_publications', 'tenders_procurement', 'job_postings'] },
  icp: {
    verticals: ['Energy and utilities', 'Mining', 'Ports and logistics', 'Oil and gas'],
    geographies: ['United States', 'India', 'United Arab Emirates'],
    size_bands: ['1000+'],
    commercial_roles: ['Operator'],
  },
  personas: {
    buyer: ['Head of Security', 'VP Operations'],
    user: ['Drone programme manager', 'Remote pilot'],
    influencer: ['Head of Innovation'],
  },
  // Columns and evidence rules as in packages/signal_core/tests/fixtures/flytbase_columns.json.
  predicates: [
    {
      key: 'drone_programme', label: 'Runs or plans a drone programme', value_type: 'boolean',
      allowed_values: null, extraction_hint: 'Announced, tendered or hiring for drone operations',
      decay_half_life_days: 365, role: 'signal',
    },
    {
      key: 'industry', label: 'Industry', value_type: 'enum',
      allowed_values: ['energy', 'utilities', 'public safety', 'mining', 'drone vendor'],
      extraction_hint: '', decay_half_life_days: null, role: 'fit.vertical',
    },
    {
      key: 'site_count', label: 'Industrial sites', value_type: 'number', allowed_values: null,
      extraction_hint: 'Plants, substations or sites operated', decay_half_life_days: 730, role: 'scale',
    },
  ],
  evidence_rules: [
    { predicate: 'drone_programme', kind: 'requires', op: 'is_true', value: null, min_sources: 1, max_age_days: 730 },
    { predicate: 'industry', kind: 'disqualifies', op: 'one_of', value: ['drone vendor'], min_sources: 1, max_age_days: null },
  ],
  assumptions: ['Assumed energy includes transmission.'],
};

export const FLYTBASE_DESCRIPTION =
  'We sell FlytBase, an enterprise drone autonomy platform for docked drones and remote BVLOS operations. Find operators of large sites (energy, mining, ports) about to automate drone inspection or security: tenders, pilots, BVLOS approvals, drone programme hires.';

/**
 * The si_brief lens cookie, decoded. Next URL-encodes cookie values on write (`:` becomes `%3A`)
 * and decodes them on read, so the raw value must be the encoded form of what the app set.
 */
export async function lensCookie(context: BrowserContext) {
  const c = (await context.cookies()).find((x) => x.name === 'si_brief');
  if (!c) return undefined;
  const value = decodeURIComponent(c.value);
  expect(c.value, 'si_brief is stored URL-encoded').toBe(encodeURIComponent(value));
  return { ...c, value };
}
