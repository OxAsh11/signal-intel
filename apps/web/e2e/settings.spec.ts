import { randomUUID } from 'node:crypto';
import { expect, test, type BrowserContext, type Page } from '@playwright/test';
import { newUser, onboard, signIn, sql } from './support';

type Workspace = { ws: string; org: string };

async function workspaceOf(userId: string): Promise<Workspace> {
  const [row] = await sql<{ ws: string; org: string }>(
    `select w.id as ws, w.org_id as org
       from public.workspace w join public.member m on m.org_id = w.org_id
      where m.user_id = $1 order by w.created_at limit 1`,
    [userId],
  );
  return row;
}

/** One failed Prospeo attempt, so Settings can show "Failing" without any vendor call. */
async function seedFailure({ ws, org }: Workspace): Promise<void> {
  const tag = randomUUID().replace(/-/g, '').slice(0, 10);
  const [{ entity }] = await sql<{ entity: string }>(
    `select app.create_entity($1, 'company', 'Provider Settings Co', 'tenant') as entity`,
    [org],
  );
  const [{ predicate }] = await sql<{ predicate: string }>(
    `insert into public.predicate (org_id, workspace_id, key, label, value_type, fill_method)
     values ($1, $2, $3, 'Provider probe', 'text', 'extract') returning id as predicate`,
    [org, ws, `settings_probe_${tag}`],
  );
  const [{ run }] = await sql<{ run: string }>(
    `insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, inputs_hash)
     values ($1, $2, $3, $4, 1, 'error', $5) returning id as run`,
    [org, ws, entity, predicate, tag],
  );
  await sql(
    `select app.record_attempt($1, 'prospeo', 'error', 0, 'rejected the key', 1, null, null, 0, 'vendor')`,
    [run],
  );
}

const section = (page: Page) => page.locator('[data-providers]');
const row = (page: Page, key: string) => section(page).locator(`[data-provider-row="${key}"]`);
const state = (page: Page, key: string) => row(page, key).locator('[data-provider-state]');
const order = (page: Page) => section(page).locator('[data-order-key]').allTextContents();

const overlaps = (a: { x: number; y: number; width: number; height: number }, b: { x: number; y: number; width: number; height: number }) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

/** Set the fake worker's status fixture. The web fetches it server-side, so a page route cannot. */
async function fakeProviders(rows: unknown[]): Promise<void> {
  const res = await fetch('http://127.0.0.1:3101/_fake/provider-status', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(rows),
  });
  if (!res.ok) throw new Error('could not set the fake provider status');
}

const DEFAULTS = [
  ['leadmagic', 'email_finder', '0.012'],
  ['findymail', 'email_finder', '0.015'],
  ['findymail_verify', 'email_verifier', '0.004'],
  ['prospeo', 'email_finder', '0.010'],
  ['pdl_company', 'company', '0.100'],
  ['pdl_person', 'person_search', '0.280'],
].map(([key, kind, default_price_usd]) => ({
  key, kind, default_price_usd, configured: false, last_error: null, last_error_at: null,
}));
const CONNECTED = { ...DEFAULTS[0]!, configured: true };

test.describe('Settings → Providers and the phone top bar', () => {
  let workspace: Workspace;

  test.beforeEach(async ({ page, context }) => {
    await fakeProviders(DEFAULTS);
    const user = await newUser('settings');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Settings');
    workspace = await workspaceOf(user.id);
    await seedFailure(workspace);
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('status, price and spend are read-only; a failed attempt shows Failing', async ({ page }) => {
    await page.goto('/settings');
    await expect(section(page).getByRole('heading', { name: 'Providers' })).toBeVisible();
    // No keys in CI: the worker says every provider is unconfigured; never an input for a key.
    await expect(state(page, 'leadmagic')).toHaveText('Not set up yet');
    await expect(state(page, 'findymail')).toHaveText('Not set up yet');
    await expect(state(page, 'prospeo')).toHaveText('Failing');
    await expect(row(page, 'prospeo')).toContainText('rejected the key');
    await expect(row(page, 'leadmagic')).toContainText('$0.01 / call');
    await expect(row(page, 'leadmagic')).toContainText('this month');
    await expect(section(page).getByLabel(/API key/)).toHaveCount(0);
    await page.screenshot({ path: 'test-results/settings-providers.png', fullPage: true });
  });

  test('a connected provider from the worker shows Connected', async ({ page }) => {
    // The worker fixture says a key exists; no key or vendor call is involved.
    await fakeProviders([CONNECTED, ...DEFAULTS.slice(1)]);
    await page.goto('/settings');
    await expect(state(page, 'leadmagic')).toHaveText('Connected');
    await expect(state(page, 'prospeo')).toHaveText('Failing');
  });

  test('an Admin reorders the waterfall and it persists', async ({ page }) => {
    const savedOrder = async () => {
      const [{ saved }] = await sql<{ saved: string[] }>(
        `select array(select jsonb_array_elements_text(provider_order)) as saved
           from public.workspace_settings where workspace_id = $1`,
        [workspace.ws],
      );
      return saved;
    };
    await page.goto('/settings');
    await expect.poll(() => order(page)).toEqual(['LeadMagic', 'Findymail', 'Prospeo']);
    await section(page).getByRole('button', { name: 'Move LeadMagic down' }).click();
    // Check the write first: a UI-only poll cannot tell a cached reply from a wrong order.
    await expect.poll(savedOrder).toEqual(['findymail', 'leadmagic', 'prospeo']);
    await expect.poll(() => order(page)).toEqual(['Findymail', 'LeadMagic', 'Prospeo']);
    await page.reload();
    await expect.poll(() => order(page)).toEqual(['Findymail', 'LeadMagic', 'Prospeo']);
    expect(await savedOrder()).toEqual(['findymail', 'leadmagic', 'prospeo']);
    await page.screenshot({ path: 'test-results/settings-provider-order.png', fullPage: true });
  });

  test('a Viewer sees the order read-only', async ({ browser }) => {
    const viewer = await newUser('settings-viewer');
    const [member] = await sql<{ id: string }>(
      'insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id',
      [workspace.org, viewer.id, viewer.email],
    );
    await sql(
      `insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`,
      [workspace.org, member.id, workspace.ws],
    );
    const ctx: BrowserContext = await browser.newContext();
    await signIn(ctx, viewer.email);
    const page = await ctx.newPage();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/settings');
    await expect.poll(() => order(page)).toEqual(['LeadMagic', 'Findymail', 'Prospeo']);
    await expect(section(page).getByRole('button', { name: /^Move / })).toHaveCount(0);
    await expect(section(page)).toContainText('read-only');
    await ctx.close();
  });

  test('at 390 px the search collapses to an icon and never crowds the user menu', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/settings');
    const toggle = page.getByRole('button', { name: 'Open search' });
    const input = page.getByRole('combobox', { name: 'Search accounts' });
    const menu = page.locator('[data-user-menu]');
    await expect(toggle).toBeVisible();
    await expect(input).toBeHidden();
    const a = await toggle.boundingBox();
    const b = await menu.boundingBox();
    expect(a && b && !overlaps(a, b)).toBeTruthy();
    await page.screenshot({ path: 'test-results/phone-top-bar-search.png', fullPage: true });

    await toggle.click();
    await expect(input).toBeVisible();
    const c = await input.boundingBox();
    const d = await menu.boundingBox();
    expect(c && d && !overlaps(c, d)).toBeTruthy();
    await page.screenshot({ path: 'test-results/phone-top-bar-search-open.png', fullPage: true });
  });
});
