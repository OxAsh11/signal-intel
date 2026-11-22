import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import { newUser, onboard, signIn, sql } from './support';

/** The filter in `f`: base64url of the AST's JSON. */
const enc = (ast: unknown) => Buffer.from(typeof ast === 'string' ? ast : JSON.stringify(ast)).toString('base64url');

async function workspaceOf(userId: string): Promise<{ ws: string; org: string; member: string }> {
  const [row] = await sql<{ ws: string; org: string; member: string }>(
    `select w.id as ws, w.org_id as org, m.id as member
       from public.workspace w join public.member m on m.org_id = w.org_id
      where m.user_id = $1
      order by w.created_at limit 1`,
    [userId],
  );
  return row;
}

type Seeded = { brief: string; sites: string; double: string; note: string };

// Eight accounts, A to H, one cell state each in the Sites column; Alpha's Double is derived.
const ROWS = ['Alpha Grid', 'Bravo Power', 'Charlie Wind', 'Delta Hydro', 'Echo Solar', 'Foxtrot Nuclear', 'Golf Coal', 'Hotel Gas'];

/**
 * One Active Brief with three columns (Sites: extract, Double: formula, Note: extract text) and
 * eight accounts that between them show every cell state (26/27 pgTAP show how each is reached):
 *   Alpha value + derived (12, 80% confidence; Double 24), Bravo stale (an old inputs hash),
 *   Charlie error ("boom"), Delta queued, Echo running, Foxtrot no_data, Golf skipped, Hotel empty.
 * Alpha is Confirmed / High 90, owned by the user, in NL, last signal 5 Mar 2026.
 * One script in one transaction (simple query): the account, verdict and score writes need app.writer.
 */
async function seed({ ws, org, member }: { ws: string; org: string; member: string }): Promise<Seeded> {
  const tag = randomUUID().replace(/-/g, '').slice(0, 12);
  const sites = `acc_sites_${tag}`;
  const dbl = `acc_double_${tag}`;
  const note = `acc_note_${tag}`;
  const id = (k: string) => `(select v from ids where k = '${k}')`;
  const spec = JSON.stringify({
    predicates: [
      { key: sites, label: 'Sites', value_type: 'number', role: 'scale' },
      { key: dbl, label: 'Double', value_type: 'number', role: 'info', fill_method: 'formula', formula: `${sites} * 2`, inputs: [sites] },
      { key: note, label: 'Note', value_type: 'text', role: 'info' },
    ],
  });
  const entities = ROWS.map((n, i) => `('e${i}', app.create_entity('${org}', 'company', '${n}', 'tenant'))`).join(', ');
  await sql(`
    begin;
    create temp table ids (k text primary key, v uuid) on commit drop;
    insert into ids values ('b', gen_random_uuid()), ('bv', gen_random_uuid());
    insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
    values (${id('b')}, '${org}', '${ws}', 'Table Brief ${tag}', 'Draft', 'done');
    insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, published_at)
    values (${id('bv')}, ${id('b')}, '${org}', '${ws}', 1, '${spec}', '{}', '{}', now() - interval '3 days');
    update public.brief set state = 'Active', current_version_id = ${id('bv')} where id = ${id('b')};
    insert into ids select 'p_sites', id from public.predicate where workspace_id = '${ws}' and key = '${sites}';
    insert into ids select 'p_dbl', id from public.predicate where workspace_id = '${ws}' and key = '${dbl}';

    insert into ids select * from (values ${entities}) t(k, v);
    select set_config('app.writer', 'derivation', true);
    insert into public.account (org_id, workspace_id, entity_id, owner_member_id)
    select '${org}', '${ws}', v, case when k = 'e0' then '${member}'::uuid end from ids where k ~ '^e[0-9]$';
    select set_config('app.writer', '', true);

    insert into public.extractor_version (capability, prompt_version, model, parser_version) values ('extract.acc-${tag}', '1', 'test', '1');
    insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status)
    values ('${org}', '${ws}', 'radar:acc', 'news', 'https://acc.example/${tag}', now(), 'acc-${tag}', 'extracted');
    insert into ids select 's', id from public.source_record where content_hash = 'acc-${tag}';
    insert into ids select 'sd', app.derived_source_record('${ws}', 'formula');
    insert into public.mention (org_id, source_record_id, entity_type, raw_name, raw_country, entity_id, match_method)
    values ('${org}', ${id('s')}, 'company', 'Alpha Grid', 'NL', ${id('e0')}, 'exact');

    insert into ids select 'c_a', app.create_claim('${org}', ${id('e0')}, ${id('p_sites')}, '12', 'Alpha runs twelve sites', null, null, ${id('s')},
        '2026-03-05T10:00:00Z', (select id from public.extractor_version where capability = 'extract.acc-${tag}'), 80::smallint);
    insert into ids select 'c_ad', app.create_claim('${org}', ${id('e0')}, ${id('p_dbl')}, '24', null, null, null, ${id('sd')},
        '2026-03-04T10:00:00Z', (select id from public.extractor_version where capability = 'extract.acc-${tag}'), 100::smallint);
    insert into ids select 'c_b', app.create_claim('${org}', ${id('e1')}, ${id('p_sites')}, '1234', 'Bravo has 1234', null, null, ${id('s')},
        '2026-03-03T10:00:00Z', (select id from public.extractor_version where capability = 'extract.acc-${tag}'), 60::smallint);

    select set_config('app.writer', 'derivation', true);
    insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids)
    values ('${org}', '${ws}', ${id('bv')}, ${id('e0')}, 'Confirmed', array[${id('c_a')}]);
    insert into public.score (org_id, workspace_id, account_id, brief_version_id, priority, band)
    select '${org}', '${ws}', a.id, ${id('bv')}, 90, 'High' from public.account a where a.entity_id = ${id('e0')};
    select set_config('app.writer', '', true);

    insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, brief_version_id, status,
                                      queued_at, started_at, finished_at, inputs_hash, claim_id, error)
    select '${org}', '${ws}', (select v from ids where k = x.e), ${id('p_sites')}, 1, ${id('bv')}, x.status, now() - interval '1 hour',
           case when x.status = 'queued' then null else now() - interval '1 hour' end,
           case when x.status in ('queued', 'running') then null else now() - interval '59 minutes' end,
           x.hash, x.claim, x.err
      from (values ('e1', 'succeeded', 'old-hash', ${id('c_b')}, null), ('e2', 'error', null, null, 'boom'),
                   ('e3', 'queued', null, null, null), ('e4', 'running', null, null, null),
                   ('e5', 'no_data', null, null, null), ('e6', 'condition_not_met', null, null, null)) x(e, status, hash, claim, err);
    commit;
  `);
  const [row] = await sql<{ brief: string; sites: string; double: string; note: string }>(
    `select b.id as brief, $2::text as sites, $3::text as double, $4::text as note from public.brief b where b.workspace_id = $1 and b.name = $5`,
    [ws, sites, dbl, note, `Table Brief ${tag}`],
  );
  return row;
}

const table = (page: Page) => page.locator('[data-table]');
const cell = (page: Page, row: string, key: string) => table(page).locator(`tr[data-row="${row}"] td[data-column="${key}"]`);
const noSidewaysScroll = async (page: Page, what: string) => {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, `${what} scrolls sideways`).toBeLessThanOrEqual(1);
};

test.describe('Accounts table', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('accounts');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'Accounts Table Org');
  });

  test('rows come in name order, system columns first, then the Brief columns in spec order', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/accounts?brief=${s.brief}`);
    await page.waitForURL(`**/workspace/${w.ws}/accounts?brief=${s.brief}`);

    await expect(table(page).locator('thead th')).toHaveText(['Account', 'Verdict', 'Score', 'Stage', 'Owner', 'Last signal', 'Sites', 'Double', 'Note']);
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);
    for (const [i, name] of ROWS.entries()) await expect(table(page).locator('tbody tr').nth(i)).toContainText(name);

    const alpha = table(page).locator('tr[data-row="Alpha Grid"]');
    await expect(alpha).toContainText('NL');
    await expect(alpha).toContainText('Confirmed');
    await expect(alpha).toContainText('90');
    await expect(alpha).toContainText('High');
    await expect(alpha).toContainText('Untouched');
    await expect(alpha).toContainText('5 Mar 2026');
    await expect(alpha.locator('td').nth(4)).not.toHaveText('—'); // the owner
    await expect(table(page).locator('tr[data-row="Hotel Gas"] td').nth(1)).toHaveText('—');

    // The first column stays put when the table scrolls sideways.
    const first = table(page).locator('tbody tr').first().locator('td').first();
    await expect(first).toHaveCSS('position', 'sticky');

    await page.screenshot({ path: 'test-results/accounts-table.png', fullPage: true });
  });

  test('each of the nine cell states renders as specified', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);

    const value = cell(page, 'Alpha Grid', s.sites);
    await expect(value.locator('[data-state="value"]')).toContainText('12');
    await expect(value.getByTitle('Confidence 80')).toHaveText('H');

    const stale = cell(page, 'Bravo Power', s.sites);
    await expect(stale.locator('[data-state="stale"]')).toContainText('1,234'); // Intl.NumberFormat('en')
    await expect(stale.getByTitle('Confidence 60')).toHaveText('M');
    await expect(stale.getByTitle('Stale: the inputs changed since this ran')).toHaveCount(1);

    const error = cell(page, 'Charlie Wind', s.sites);
    await expect(error.locator('[data-state="error"]')).toContainText('boom');

    await expect(cell(page, 'Delta Hydro', s.sites).locator('[data-state="queued"]').getByRole('img', { name: 'Queued' })).toBeVisible();
    await expect(cell(page, 'Echo Solar', s.sites).locator('[data-state="running"]').getByRole('img', { name: 'Running' })).toBeVisible();
    await expect(cell(page, 'Foxtrot Nuclear', s.sites).locator('[data-state="no_data"]')).toHaveText('No data');
    await expect(cell(page, 'Golf Coal', s.sites).locator('[data-state="skipped"]')).toHaveText('Skipped');
    await expect(cell(page, 'Hotel Gas', s.sites).locator('[data-state="empty"]')).toHaveText('—');

    const derived = cell(page, 'Alpha Grid', s.double);
    await expect(derived.locator('[data-state="derived"] .si-derived-val')).toHaveText('24');
    await expect(derived.getByText('H', { exact: true })).toHaveCount(0); // a derived value has no confidence chip
  });

  test('density: 900 px folds columns into a drawer, 390 px shows cards without sideways scroll', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const url = `/workspace/${w.ws}/accounts?brief=${s.brief}`;

    await page.setViewportSize({ width: 900, height: 800 });
    await page.goto(url);
    await expect(table(page)).toBeVisible();
    await expect(page.getByRole('button', { name: '+1 columns' })).toBeVisible();
    await expect(table(page).locator('thead th', { hasText: 'Double' })).toBeVisible();
    await expect(table(page).locator('thead th', { hasText: 'Note' })).toBeHidden();
    await page.getByRole('button', { name: '+1 columns' }).click();
    const drawer = page.getByRole('dialog', { name: 'More columns' });
    await expect(drawer).toContainText('Note');
    await expect(drawer).toContainText('Alpha Grid');
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();

    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole('button', { name: '+1 columns' })).toBeHidden();
    await expect(table(page).locator('thead th', { hasText: 'Note' })).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('[data-cards]')).toBeVisible();
    await expect(table(page)).toBeHidden();
    await expect(page.locator('[data-cards] li')).toHaveCount(ROWS.length);
    const alpha = page.locator('[data-cards] li[data-row="Alpha Grid"]');
    await expect(alpha).toContainText('Confirmed');
    await expect(alpha).toContainText('High');
    await expect(alpha).toContainText('Untouched');
    await expect(alpha).toContainText('Sites');
    await expect(alpha).toContainText('12');
    await noSidewaysScroll(page, 'the accounts cards at 390px');
    await page.screenshot({ path: 'test-results/accounts-phone.png', fullPage: true });
  });

  test('keyboard: arrows move, Space selects, Enter opens the panel and runs nothing', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
    await expect(table(page)).toHaveAttribute('data-ready', '');
    await page.evaluate(() => {
      (window as unknown as { __opened: unknown[] }).__opened = [];
      window.addEventListener('si:open-cell', (e) => (window as unknown as { __opened: unknown[] }).__opened.push((e as CustomEvent).detail));
    });
    const [{ before }] = await sql<{ before: number }>(`select count(*)::int as before from public.predicate_run where workspace_id = $1`, [w.ws]);

    await cell(page, 'Alpha Grid', s.sites).focus();
    await page.keyboard.press('ArrowDown');
    await expect(cell(page, 'Bravo Power', s.sites)).toBeFocused();
    await page.keyboard.press('ArrowRight');
    await expect(cell(page, 'Bravo Power', s.double)).toBeFocused();
    await page.keyboard.press(' ');
    await expect(page.getByRole('region', { name: 'Selected accounts' })).toContainText('1 selected');
    await page.keyboard.press(' ');
    await expect(page.getByRole('region', { name: 'Selected accounts' })).toHaveCount(0);
    await page.keyboard.press('Enter');
    expect(await page.evaluate(() => (window as unknown as { __opened: unknown[] }).__opened.length)).toBe(1);
    await expect(page.getByRole('dialog', { name: 'Cell detail' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Cell detail' })).toHaveCount(0);

    const [{ after }] = await sql<{ after: number }>(`select count(*)::int as after from public.predicate_run where workspace_id = $1`, [w.ws]);
    expect(after, 'Enter queued no run').toBe(before);
  });

  test('empty states: no accounts points at Sources; a filter that matches nothing offers Clear filter', async ({ page }) => {
    const w = await workspaceOf(userId);
    await page.goto('/accounts');
    await page.waitForURL(`**/workspace/${w.ws}/accounts`);
    const none = page.locator('[data-table-state="no-accounts"]');
    await expect(none).toContainText('Sources are running: 0 of 0 active, 0 records fetched');
    await expect(none.getByRole('link')).toHaveAttribute('href', '/sources');

    const s = await seed(w);
    const none2 = { field: { system: 'name' }, op: 'contains', value: 'zzzz-nothing' };
    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}&f=${enc(none2)}`);
    await expect(page.locator('[data-table-state="no-match"]')).toContainText('No accounts match');
    await page.getByRole('link', { name: 'Clear filter' }).click();
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);
    expect(new URL(page.url()).searchParams.has('f')).toBe(false);
  });

  test('a readable filter narrows the rows; a view applies its own filter until f replaces it', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const base = `/workspace/${w.ws}/accounts?brief=${s.brief}`;
    await page.goto(`${base}&f=${enc({ field: { system: 'name' }, op: 'contains', value: 'grid' })}`);
    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    await page.goto(`${base}&view=errored`);
    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    await expect(table(page).locator('tbody tr').first()).toContainText('Charlie Wind');
    // An f beside a view replaces the view's filter.
    await page.goto(`${base}&view=errored&f=${enc({ field: { system: 'name' }, op: 'contains', value: 'grid' })}`);
    await expect(table(page).locator('tbody tr').first()).toContainText('Alpha Grid');
  });

  test('a tampered f never errors: "This filter couldn\'t be read", with Clear filter', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const base = `/workspace/${w.ws}/accounts?brief=${s.brief}`;
    // Over the server's limits (depth 8, 50 conditions) yet small enough for a URL (the platform answers 431 beyond ~8 KB).
    let deep: unknown = { field: { system: 'name' }, op: 'contains', value: 'x' };
    for (let i = 0; i < 60; i++) deep = { all: [deep] };
    const wide = { all: Array.from({ length: 60 }, () => ({ field: { system: 'name' }, op: 'contains', value: 'x' })) };
    const bad = [
      'garbage!!',
      enc('not json at all'),
      enc('[1, 2, 3]'),
      enc(deep),
      enc(wide),
      enc({ field: { column: 'no_such_column' }, op: 'is_true' }),
      enc({ field: { system: 'name' }, op: "contains'; drop table public.account; --", value: 'x' }),
      enc({ field: { system: 'nope' }, op: 'one_of', value: ['x'] }),
    ];
    for (const f of bad) {
      const res = await page.goto(`${base}&f=${f}`);
      expect(res?.status(), `status for ${f.slice(0, 30)}`).toBe(200);
      await expect(page.locator('[data-table-state="bad-filter"]')).toContainText("This filter couldn't be read");
      await expect(page.getByRole('link', { name: 'Clear filter' })).toBeVisible();
    }
    // A bad sort is unreadable too.
    await page.goto(`${base}&sort=${encodeURIComponent('name:sideways')}`);
    await expect(page.locator('[data-table-state="bad-filter"]')).toBeVisible();
    await page.getByRole('link', { name: 'Clear filter' }).click();
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);
    // The accounts survived the injection attempt.
    const [{ n }] = await sql<{ n: number }>(`select count(*)::int as n from public.account where workspace_id = $1`, [w.ws]);
    expect(n).toBe(ROWS.length);
  });

  test('sorting by a header changes the URL and the order', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
    await table(page).locator('thead').getByRole('link', { name: 'Account' }).click();
    await expect(page).toHaveURL(/sort=name%3Aasc/);
    await table(page).locator('thead').getByRole('link', { name: /Account/ }).click();
    await expect(page).toHaveURL(/sort=name%3Adesc/);
    await expect(table(page).locator('tbody tr').first()).toContainText('Hotel Gas');
  });

  test.describe('cell panel', () => {
    const panel = (page: Page) => page.getByRole('dialog', { name: 'Cell detail' });
    const runCount = async (ws: string) =>
      (await sql<{ n: number }>(`select count(*)::int as n from public.predicate_run where workspace_id = $1`, [ws]))[0].n;

    test('a value cell shows its quote, source link, date and history; opening runs nothing', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      const before = await runCount(w.ws);
      await cell(page, 'Alpha Grid', s.sites).click();
      await expect(panel(page)).toContainText('Sites');
      await expect(panel(page).locator('[data-panel-value]')).toContainText('12');
      await expect(panel(page).locator('[data-panel-value]')).toContainText('Found in sources');
      const evidence = panel(page).locator('[data-panel-evidence]');
      await expect(evidence).toContainText('Alpha runs twelve sites');
      await expect(evidence).toContainText('5 Mar 2026');
      await expect(evidence.getByRole('link', { name: 'Source' })).toHaveAttribute('href', /acc\.example/);
      await expect(panel(page).locator('[data-panel-history]')).toBeVisible();
      await page.screenshot({ path: 'test-results/accounts-panel.png', fullPage: true });
      expect(await runCount(w.ws), 'opening the panel queued no run').toBe(before);

      await page.keyboard.press('Escape');
      await cell(page, 'Bravo Power', s.sites).click();
      await expect(panel(page).locator('[data-panel-history]')).toContainText('succeeded');
    });

    test('a derived cell lists its inputs', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      await cell(page, 'Alpha Grid', s.double).click();
      await expect(panel(page).locator('[data-panel-value]')).toContainText('Formula');
      await expect(panel(page).locator('[data-panel-inputs]')).toContainText('Sites');
      await expect(panel(page).locator('[data-panel-inputs]')).toContainText('12');
    });

    test('Edit writes a manual value, shows the evidence note, and keeps the old value struck through', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      await cell(page, 'Alpha Grid', s.sites).click();
      await panel(page).getByRole('button', { name: 'Edit' }).click();
      await expect(panel(page).locator('[data-panel-note="evidence"]')).toContainText('This counts as evidence.');
      await expect(panel(page).locator('[data-panel-note="evidence"]')).toContainText('you can undo it any time with Mark wrong');
      await panel(page).getByLabel('New value').fill('40');
      await panel(page).getByRole('button', { name: 'Save value' }).click();
      await expect(panel(page).locator('[data-panel-value]')).toContainText('Entered by you');
      await expect(panel(page).locator('[data-panel-value]')).toContainText('40');
      await expect(panel(page).locator('[data-claim-status="active"]')).toContainText('Counts as evidence · undo with Mark wrong');
      const old = panel(page).locator('[data-claim-status="superseded"]').filter({ hasText: '12' });
      await expect(old).toHaveCount(1);
      await expect(old.locator('.line-through').first()).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(cell(page, 'Alpha Grid', s.sites)).toContainText('40');
    });

    test('Mark wrong needs a reason and creates a Review item', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      await cell(page, 'Bravo Power', s.sites).click();
      await panel(page).getByRole('button', { name: 'Mark wrong' }).click();
      await expect(panel(page).getByRole('button', { name: 'Confirm mark wrong' })).toBeDisabled();
      await panel(page).getByLabel('Why is this value wrong?').fill('The site count is for a different company.');
      await panel(page).getByRole('button', { name: 'Confirm mark wrong' }).click();
      await expect(panel(page).locator('[data-claim-status="retracted"]')).toHaveCount(1);
      const [{ n }] = await sql<{ n: number }>(
        `select count(*)::int as n from public.review_item where workspace_id = $1 and type = 'claim_wrong' and status = 'pending'`,
        [w.ws],
      );
      expect(n).toBe(1);
      await page.reload();
      await expect(page.getByRole('navigation').getByRole('link', { name: /^Review\s*1\s*pending$/ })).toBeVisible();
    });

    test('Re-run shows the estimate, confirms, then the cell is queued', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      const before = await runCount(w.ws);
      await cell(page, 'Hotel Gas', s.sites).click();
      await panel(page).getByRole('button', { name: 'Re-run' }).click();
      await expect(panel(page).locator('[data-panel-estimate]')).toHaveText(/^1 account · est\. \$\d+\.\d{2}$/);
      expect(await runCount(w.ws), 'the estimate queued nothing').toBe(before);
      await panel(page).getByRole('button', { name: 'Confirm re-run' }).click();
      await expect(panel(page).getByRole('status')).toContainText('Queued');
      expect(await runCount(w.ws)).toBe(before + 1);
      await page.keyboard.press('Escape');
      await expect(cell(page, 'Hotel Gas', s.sites).locator('[data-state="queued"]')).toBeVisible();
    });

    test('a Viewer opens the panel but sees no Re-run, Edit or Mark wrong', async ({ browser }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      const viewer = await newUser('panel-viewer');
      const [member] = await sql<{ id: string }>('insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id', [w.org, viewer.id, viewer.email]);
      await sql(`insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`, [w.org, member.id, w.ws]);
      const ctx = await browser.newContext();
      await signIn(ctx, viewer.email);
      const vp = await ctx.newPage();
      await vp.setViewportSize({ width: 1440, height: 900 });
      await vp.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
      await cell(vp, 'Alpha Grid', s.sites).click();
      await expect(panel(vp).locator('[data-panel-evidence]')).toContainText('Alpha runs twelve sites');
      for (const name of ['Re-run', 'Edit', 'Mark wrong']) await expect(panel(vp).getByRole('button', { name })).toHaveCount(0);
      await ctx.close();
    });
  });
});
