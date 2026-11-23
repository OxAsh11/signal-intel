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

type Seeded = { brief: string; sites: string; double: string; note: string; programme: string };

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
async function seed({ ws, org, member }: { ws: string; org: string; member: string }, opts: { programme?: boolean } = {}): Promise<Seeded> {
  const tag = randomUUID().replace(/-/g, '').slice(0, 12);
  const sites = `acc_sites_${tag}`;
  const dbl = `acc_double_${tag}`;
  const note = `acc_note_${tag}`;
  // With `programme`: a fourth column, Drone programme (boolean, extract), Yes for Alpha and Bravo.
  const prog = `acc_prog_${tag}`;
  const id = (k: string) => `(select v from ids where k = '${k}')`;
  // Publishable (app.assert_brief_publishable), so Add column can publish a new version of it.
  const spec = JSON.stringify({
    offer: 'Drone autonomy software for operators of large sites.',
    hypothesis: 'Operators of many sites announce drone programmes before they buy autonomy software.',
    examples: { positive: ['A utility tenders drone inspection', 'A port pilots a docked drone', 'A mine gets a BVLOS approval'], negative: ['A wedding photographer', 'A drone maker'] },
    sources: { radar_types: ['news_publications'] },
    predicates: [
      { key: sites, label: 'Sites', value_type: 'number', role: 'scale' },
      { key: dbl, label: 'Double', value_type: 'number', role: 'info', fill_method: 'formula', formula: `${sites} * 2`, inputs: [sites] },
      { key: note, label: 'Note', value_type: 'text', role: 'info' },
      ...(opts.programme ? [{ key: prog, label: 'Drone programme', value_type: 'boolean', role: 'signal' }] : []),
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
    insert into ids select 'p_prog', id from public.predicate where workspace_id = '${ws}' and key = '${prog}';

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
    ${opts.programme ? `select app.create_claim('${org}', v, ${id('p_prog')}, 'true', 'Runs a drone programme', null, null, ${id('s')},
        '2026-03-02T10:00:00Z', (select id from public.extractor_version where capability = 'extract.acc-${tag}'), 90::smallint)
      from ids where k in ('e0', 'e1');` : ''}

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
  const [row] = await sql<Seeded>(
    `select b.id as brief, $2::text as sites, $3::text as double, $4::text as note, $6::text as programme
       from public.brief b where b.workspace_id = $1 and b.name = $5`,
    [ws, sites, dbl, note, `Table Brief ${tag}`, prog],
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
      // Started and finished times, not just dates.
      await expect(panel(page).locator('[data-panel-history]')).toContainText(/Started \d{1,2} \w{3,4} \d{4}, \d{2}:\d{2} · finished \d{1,2} \w{3,4} \d{4}, \d{2}:\d{2}/);
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
      await expect(old).toContainText('overridden by the newer value 40');
      // Close the panel (its backdrop covers the table): after Save the focus may have left the
      // panel, so use its Close button rather than Escape, and wait until it is gone.
      await panel(page).getByRole('button', { name: 'Close' }).click();
      await expect(panel(page)).toBeHidden();
      await expect(cell(page, 'Alpha Grid', s.sites)).toContainText('40');

      // Mark wrong acts on the value the cell shows (the manual 40), not on the overridden source.
      await cell(page, 'Alpha Grid', s.sites).click();
      await expect(panel(page).locator('[data-claim-current]')).toContainText('40');
      await panel(page).getByRole('button', { name: 'Mark wrong' }).click();
      await panel(page).getByLabel('Why is this value wrong?').fill('Typed the wrong number.');
      await panel(page).getByRole('button', { name: 'Confirm mark wrong' }).click();
      await expect(panel(page).locator('[data-claim-status="retracted"]')).toContainText('40');
      await expect(panel(page).locator('[data-panel-value]')).toContainText('12');
      await expect(panel(page).locator('[data-claim-current]')).toContainText('Alpha runs twelve sites');
      const [{ status }] = await sql<{ status: string }>(
        `select c.status::text as status from public.claim c join public.predicate p on p.id = c.predicate_id
          where p.workspace_id = $1 and p.key = $2 and c.quote = 'Alpha runs twelve sites'`,
        [w.ws, s.sites],
      );
      expect(status, 'the sourced claim was left alone').toBe('active');
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

  test.describe('Add column, run controls and the bulk bar', () => {
    const runsOf = async (ws: string, key: string) =>
      (
        await sql<{ n: number }>(
          `select count(*)::int as n from public.predicate_run pr join public.predicate p on p.id = pr.predicate_id
            where pr.workspace_id = $1 and p.key = $2 and pr.status in ('queued', 'running')`,
          [ws, key],
        )
      )[0].n;
    const addPanel = (page: Page) => page.getByRole('dialog', { name: 'Add column' });
    const confirmDialog = (page: Page) => page.getByRole('dialog', { name: 'Confirm run' });
    /** Plays the worker's preview job: the Brief's pending preview becomes ready. */
    async function completePreview(brief: string, tier: string, result: unknown, usd: number, minutes: number) {
      await expect
        .poll(async () => (await sql<{ n: number }>(`select count(*)::int as n from public.brief_preview where brief_id = $1 and status = 'pending'`, [brief]))[0].n)
        .toBe(1);
      await sql(
        `select app.complete_brief_preview(p.id, $2::public.edit_tier, false, $3::jsonb, $4, $5, false)
           from public.brief_preview p where p.brief_id = $1 and p.status = 'pending'`,
        [brief, tier, JSON.stringify(result), usd, minutes],
      );
    }
    async function openTable(page: Page, ws: string, brief: string) {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${ws}/accounts?brief=${brief}`);
      await expect(table(page)).toHaveAttribute('data-ready', '');
    }

    test('a formula column: live 5-row preview, publish, run choices at $0.00, then derived cells', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w, { programme: true });
      await openTable(page, w.ws, s.brief);

      await page.getByRole('button', { name: 'Add column' }).click();
      const add = addPanel(page);
      // AI research and provider waterfalls are listed, disabled.
      await expect(add.getByRole('radio', { name: 'AI research' })).toBeDisabled();
      await expect(add.getByRole('radio', { name: 'Provider waterfall' })).toBeDisabled();
      await expect(add).toContainText('Arrives in P6');
      await add.getByRole('radio', { name: 'Formula' }).check();
      await add.getByLabel('Name', { exact: true }).fill('Programme and sites');
      await expect(add).toContainText('programme_and_sites');
      await add.getByLabel('Formula', { exact: true }).fill(`${s.programme} and ${s.sites} > 0`);
      const preview = add.locator('[data-formula-preview]');
      await expect(preview.locator('[data-preview-row]')).toHaveCount(5);
      await expect(preview.locator('[data-preview-row="Alpha Grid"] [data-preview-value]')).toHaveText('Yes');
      await expect(preview.locator('[data-preview-row="Bravo Power"] [data-preview-value]')).toHaveText('Yes');
      await page.screenshot({ path: 'test-results/accounts-add-column.png', fullPage: true });

      await add.getByRole('button', { name: 'Save and preview' }).click();
      await completePreview(s.brief, 'Narrow', { counts: {} }, 0, 0);
      await expect(add.locator('[data-add-column-cost]')).toContainText('Estimated cost $0.00');
      await add.getByRole('button', { name: 'Publish column' }).click();

      const choices = add.locator('[data-run-choices]');
      await expect(choices.locator('[data-run-choice="10"]')).toContainText('est. $0.00');
      await expect(choices.locator('[data-run-choice="all"]')).toContainText('est. $0.00');
      await expect(choices.getByRole('button', { name: 'Save without running' })).toBeVisible();
      // Publishing a formula already queued every empty cell (the P3 backfill), for free.
      await expect(choices.locator('[data-run-backfilled]')).toHaveAttribute('data-run-backfilled', String(ROWS.length));
      expect(await runsOf(w.ws, 'programme_and_sites')).toBe(ROWS.length);

      // The worker's part: each run is worked out (Yes for Alpha and Bravo; no inputs elsewhere).
      await sql(
        `select app.start_run(pr.id) from public.predicate_run pr join public.predicate p on p.id = pr.predicate_id
          where pr.workspace_id = $1 and p.key = 'programme_and_sites' and pr.status = 'queued'`,
        [w.ws],
      );
      await sql(
        `select case when e.display_name in ('Alpha Grid', 'Bravo Power') then app.complete_formula_run(pr.id, 'true'::jsonb, null)
                     else app.finish_run(pr.id, 'no_data') end
           from public.predicate_run pr join public.predicate p on p.id = pr.predicate_id join public.entity e on e.id = pr.entity_id
          where pr.workspace_id = $1 and p.key = 'programme_and_sites' and pr.status = 'running'`,
        [w.ws],
      );
      await choices.getByRole('button', { name: 'Save without running' }).click();
      await page.reload();
      await expect(cell(page, 'Alpha Grid', 'programme_and_sites').locator('[data-state="derived"]')).toHaveText('Yes');
      await expect(cell(page, 'Bravo Power', 'programme_and_sites').locator('[data-state="derived"]')).toHaveText('Yes');
    });

    test('an extract column shows "Fills from N records" and the estimate', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await openTable(page, w.ws, s.brief);
      await page.getByRole('button', { name: 'Add column' }).click();
      const add = addPanel(page);
      await expect(add.getByRole('radio', { name: 'Found in sources' })).toBeChecked();
      await add.getByLabel('Name', { exact: true }).fill('Site operator');
      await add.getByLabel('Value', { exact: true }).selectOption('boolean');
      await add.getByLabel('What to look for').fill('Operates its own industrial sites');
      await add.getByRole('button', { name: 'Save and preview' }).click();
      await completePreview(s.brief, 'Expand', { counts: { reextract_records: 3 } }, 0.42, 30);
      await expect(add.locator('[data-add-column-records]')).toHaveText('Fills from 3 records already collected');
      await expect(add.locator('[data-add-column-cost]')).toContainText('Estimated cost $0.42');
      await add.getByRole('button', { name: 'Publish column' }).click();
      await expect(add.locator('[data-run-choice="all"]')).toContainText(/8 accounts · est\. \$\d+\.\d{2}/);
      await expect(add.locator('[data-run-choice="10"]')).toContainText(/8 accounts · est\. \$\d+\.\d{2}/);
      await add.locator('[data-run-choice="10"]').click();
      await expect(add.getByRole('status').filter({ hasText: 'Queued 8 runs' })).toBeVisible();
      expect(await runsOf(w.ws, 'site_operator')).toBe(8);
      await expect(table(page).locator('thead th', { hasText: 'Site operator' })).toBeVisible();
    });

    test('R9: Add column refuses while the Brief has other unpublished changes, and links to the Brief', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      // Someone's half-finished edit: the draft is the published config with another offer.
      await sql(
        `update public.brief b set draft_config = jsonb_set(v.config, '{offer}', '"A different offer, not yet published."')
           from public.brief_version v where v.id = b.current_version_id and b.id = $1`,
        [s.brief],
      );
      await openTable(page, w.ws, s.brief);
      await page.getByRole('button', { name: 'Add column' }).click();
      const add = addPanel(page);
      const pending = add.locator('[data-add-column-pending]');
      await expect(pending).toContainText('This Brief has unpublished changes. Publish or discard them on the Brief page first.');
      await expect(pending.getByRole('link', { name: 'Open the Brief' })).toHaveAttribute('href', `/briefs/${s.brief}`);
      await expect(add.getByRole('button', { name: 'Save and preview' })).toHaveCount(0);
      const [{ offer }] = await sql<{ offer: string }>(`select draft_config ->> 'offer' as offer from public.brief where id = $1`, [s.brief]);
      expect(offer, 'the draft is untouched').toBe('A different offer, not yet published.');
    });

    test('Run empty shows N accounts · est. $X, then queues N runs; Pause auto-run switches a formula off', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await openTable(page, w.ws, s.brief);
      await page.getByRole('button', { name: 'Column actions for Note' }).click();
      await page.getByRole('menuitem', { name: 'Run empty' }).click();
      const dlg = confirmDialog(page);
      await expect(dlg.locator('[data-run-estimate]')).toHaveText(/^8 accounts · est\. \$\d+\.\d{2}$/);
      expect(await runsOf(w.ws, s.note), 'the estimate queued nothing').toBe(0);
      await page.screenshot({ path: 'test-results/accounts-run-confirm.png', fullPage: true });
      await dlg.getByRole('button', { name: 'Confirm run' }).click();
      await expect(dlg.getByRole('status')).toContainText('Queued 8 runs');
      expect(await runsOf(w.ws, s.note)).toBe(8);
      await dlg.getByRole('button', { name: 'Done' }).click();

      // An extract column has no auto-run; a formula's menu pauses it.
      await page.getByRole('button', { name: 'Column actions for Note' }).click();
      await expect(page.getByRole('menuitem', { name: /auto-run/ })).toHaveCount(0);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Column actions for Double' }).click();
      await page.getByRole('menuitem', { name: 'Pause auto-run' }).click();
      await expect
        .poll(async () => (await sql<{ on: boolean }>(`select auto_run as on from public.predicate where workspace_id = $1 and key = $2`, [w.ws, s.double]))[0].on)
        .toBe(false);
      await page.getByRole('button', { name: 'Column actions for Double' }).click();
      await expect(page.getByRole('menuitem', { name: 'Resume auto-run' })).toBeVisible();
    });

    test('a budget cap below the estimate shows the 55000 message and Run the first N, which queues N', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      const [{ per }] = await sql<{ per: string }>(
        `select app.column_cost_per_entity(p.id, $1)::text as per from public.predicate p where p.workspace_id = $1 and p.key = $2`,
        [w.ws, s.note],
      );
      const cap = Math.max(0.01, Math.ceil(Number(per) * 3 * 100) / 100);
      expect(cap, 'the cap is below the cost of 8 runs').toBeLessThan(Number(per) * 8);
      await sql(`insert into public.budget (org_id, workspace_id, period, cap_usd) values ($1, $2, 'month', $3)`, [w.org, w.ws, cap]);
      const [{ n }] = await sql<{ n: number }>(`select floor($1::numeric / $2::numeric)::int as n`, [cap, per]);

      await openTable(page, w.ws, s.brief);
      await page.getByRole('button', { name: 'Column actions for Note' }).click();
      await page.getByRole('menuitem', { name: 'Run empty' }).click();
      const dlg = confirmDialog(page);
      await expect(dlg.locator('[data-run-estimate]')).toHaveText(/^8 accounts/);
      await dlg.getByRole('button', { name: 'Confirm run' }).click();
      const refusal = dlg.locator('[data-run-refusal]');
      await expect(refusal).toContainText(`run the first ${n} within budget`);
      expect(await runsOf(w.ws, s.note), 'a refused run queues nothing').toBe(0);
      await refusal.getByRole('button', { name: `Run the first ${n}` }).click();
      await expect(dlg.getByRole('status')).toContainText(`Queued ${n} run`);
      expect(await runsOf(w.ws, s.note)).toBe(n);
    });

    test('bulk run: 2 selected rows, Run columns…, confirm queues 2 runs per chosen column', async ({ page }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      await openTable(page, w.ws, s.brief);
      await page.getByLabel('Select Alpha Grid').check();
      await page.getByLabel('Select Hotel Gas').check();
      const bar = page.getByRole('region', { name: 'Selected accounts' });
      await expect(bar).toContainText('2 selected');
      await expect(bar.getByRole('button', { name: /Add to sequence/ })).toBeDisabled();
      await expect(bar.getByRole('button', { name: /Find people/ })).toBeDisabled();
      await expect(bar.getByRole('button', { name: /Merge/ })).toBeDisabled();
      await bar.getByRole('button', { name: 'Run columns…' }).click();
      await bar.getByLabel('Note').check();
      await bar.getByLabel('Sites').check();
      await bar.getByRole('button', { name: 'Review run' }).click();
      const dlg = confirmDialog(page);
      await expect(dlg.locator('[data-run-job="Note"] [data-run-estimate]')).toHaveText(/^2 accounts · est\. \$\d+\.\d{2}$/);
      await expect(dlg.locator('[data-run-job="Sites"] [data-run-estimate]')).toHaveText(/^2 accounts · est\. \$\d+\.\d{2}$/);
      await dlg.getByRole('button', { name: 'Confirm run' }).click();
      await expect(dlg.getByRole('status')).toHaveCount(2);
      expect(await runsOf(w.ws, s.note)).toBe(2);
      const [{ sites }] = await sql<{ sites: number }>(
        `select count(*)::int as sites from public.predicate_run pr join public.predicate p on p.id = pr.predicate_id
           join public.entity e on e.id = pr.entity_id
          where pr.workspace_id = $1 and p.key = $2 and pr.status = 'queued' and e.display_name in ('Alpha Grid', 'Hotel Gas')`,
        [w.ws, s.sites],
      );
      expect(sites).toBe(2);
    });

    test('Viewer (Review Focus 5): no Add column, column menus or bulk Run; All Briefs disables Add column', async ({ page, browser }) => {
      const w = await workspaceOf(userId);
      const s = await seed(w);
      // An Editor under All Briefs: Add column says why it cannot.
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`/workspace/${w.ws}/accounts?brief=all`);
      await expect(page.getByRole('button', { name: 'Add column' })).toBeDisabled();
      await expect(page.locator('[data-add-column="disabled"]')).toContainText('Choose a Brief in the lens to add a column');

      const viewer = await newUser('cols-viewer');
      const [member] = await sql<{ id: string }>('insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id', [w.org, viewer.id, viewer.email]);
      await sql(`insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`, [w.org, member.id, w.ws]);
      const ctx = await browser.newContext();
      await signIn(ctx, viewer.email);
      const vp = await ctx.newPage();
      await openTable(vp, w.ws, s.brief);
      await expect(vp.getByRole('button', { name: 'Add column' })).toHaveCount(0);
      await expect(vp.getByRole('button', { name: /^Column actions for/ })).toHaveCount(0);
      await vp.getByLabel('Select Alpha Grid').check();
      const bar = vp.getByRole('region', { name: 'Selected accounts' });
      await expect(bar).toContainText('1 selected');
      await expect(bar.getByRole('button', { name: 'Export selected' })).toBeVisible();
      await expect(bar.getByRole('button', { name: 'Run columns…' })).toHaveCount(0);
      await ctx.close();
    });
  });
});

test.describe('Filters, views, pins and search', () => {
  let userId = '';
  const aside = (page: Page) => page.getByRole('complementary', { name: 'Sidebar' });
  const viewsNav = (page: Page) => page.getByRole('navigation', { name: 'Views' });
  const saveForm = (page: Page) => page.getByRole('form', { name: 'Save view' });
  const openTable = async (page: Page, ws: string, query: string) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${ws}/accounts?${query}`);
    await expect(table(page)).toHaveAttribute('data-ready', '');
  };
  const rowNames = (page: Page) => table(page).locator('tbody tr').evaluateAll((trs) => trs.map((t) => t.getAttribute('data-row')));

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('filters');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'Filters Org');
  });

  test('chips: Verdict is Confirmed, Likely plus a column is not empty narrows the rows, and f round-trips on reload', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await openTable(page, w.ws, `brief=${s.brief}`);
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);

    await page.getByRole('button', { name: /^Filters/ }).click();
    const builder = page.locator('[data-filter-builder]');
    await builder.getByRole('button', { name: 'Add filter' }).click();
    await builder.getByLabel('Filter field').nth(0).selectOption({ label: 'Verdict' });
    await builder.getByRole('checkbox', { name: 'Confirmed' }).check();
    await builder.getByRole('checkbox', { name: 'Likely' }).check();
    await builder.getByRole('button', { name: 'Add filter' }).click();
    await builder.getByLabel('Filter field').nth(1).selectOption({ label: 'Sites' });
    await builder.getByLabel('Filter operator').nth(1).selectOption({ label: 'is not empty' });
    await page.screenshot({ path: 'test-results/accounts-filter-builder.png', fullPage: true });
    await builder.getByRole('button', { name: 'Apply filter' }).click();

    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    await expect(table(page).locator('tr[data-row="Alpha Grid"]')).toBeVisible();
    const f = new URL(page.url()).searchParams.get('f');
    expect(f).toBeTruthy();
    expect(JSON.parse(Buffer.from(f as string, 'base64url').toString())).toEqual({
      all: [
        { field: { system: 'verdict' }, op: 'one_of', value: ['Confirmed', 'Likely'] },
        { field: { column: s.sites }, op: 'is_not_empty' },
      ],
    });

    // The URL is the state: a reload shows the same rows and the same chips.
    await page.reload();
    await expect(table(page)).toHaveAttribute('data-ready', '');
    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    await expect(page.locator('[data-filter-builder]').getByLabel('Filter field')).toHaveCount(2);
    await expect(page.locator('[data-filter-builder]').getByRole('checkbox', { name: 'Likely' })).toBeChecked();

    // Clear filter returns every row.
    await page.locator('[data-filter-builder]').getByRole('button', { name: 'Clear filter' }).click();
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);
    expect(new URL(page.url()).searchParams.has('f')).toBe(false);
  });

  test('an "any of" group matches when one of its chips does', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await openTable(page, w.ws, `brief=${s.brief}`);
    await page.getByRole('button', { name: /^Filters/ }).click();
    const builder = page.locator('[data-filter-builder]');
    await builder.getByRole('button', { name: /^Add .any of. group$/ }).click();
    await builder.getByLabel('Filter field').nth(0).selectOption({ label: 'Name' });
    await builder.getByLabel('Filter value').nth(0).fill('grid');
    await builder.getByRole('button', { name: 'Add to group' }).click();
    await builder.getByLabel('Filter field').nth(1).selectOption({ label: 'Name' });
    await builder.getByLabel('Filter value').nth(1).fill('hydro');
    await builder.getByRole('button', { name: 'Apply filter' }).click();
    await expect.poll(() => rowNames(page)).toEqual(['Alpha Grid', 'Delta Hydro']);
  });

  test('Save view (personal) shows in the view bar; Pin shows it under Accounts with its count; Unpin removes it', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await openTable(page, w.ws, `brief=${s.brief}&f=${enc({ field: { system: 'name' }, op: 'contains', value: 'grid' })}`);

    await page.getByRole('button', { name: 'Save view' }).click();
    await saveForm(page).getByLabel('View name').fill('Grid only');
    await saveForm(page).getByRole('radio', { name: 'Personal' }).check();
    await saveForm(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page).toHaveURL(/view=[0-9a-f-]{36}/);
    await expect(viewsNav(page).getByRole('link', { name: 'Grid only' })).toBeVisible();
    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    const [saved] = await sql<{ visibility: string }>(`select visibility from public.saved_view where workspace_id = $1 and name = 'Grid only'`, [w.ws]);
    expect(saved.visibility).toBe('personal');

    await expect(aside(page).locator('[data-pinned-view]')).toHaveCount(0);
    await page.getByRole('button', { name: 'Pin to sidebar' }).click();
    const pinned = aside(page).locator('[data-pinned-view]', { hasText: 'Grid only' });
    await expect(pinned).toBeVisible();
    await expect(pinned).toHaveText(/Grid only\s*1$/);
    await expect(pinned).toHaveAttribute('href', /\/accounts\?view=[0-9a-f-]{36}/);

    await page.getByRole('button', { name: 'Unpin from sidebar' }).click();
    await expect(aside(page).locator('[data-pinned-view]')).toHaveCount(0);
  });

  test('R10: a saved view opens under its own Brief whatever the lens; export agrees; a missing view says so', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const [{ other }] = await sql<{ other: string }>(
      `insert into public.brief (org_id, workspace_id, name, state, intake_status) values ($1, $2, 'Other Brief', 'Draft', 'done') returning id as other`,
      [w.org, w.ws],
    );
    const [{ id }] = await sql<{ id: string }>(
      `insert into public.saved_view (org_id, workspace_id, brief_id, name, filter, visibility, created_by)
       values ($1, $2, $3, 'Confirmed here', '{"field": {"system": "verdict"}, "op": "one_of", "value": ["Confirmed"]}', 'personal', $4) returning id`,
      [w.org, w.ws, s.brief, w.member],
    );
    // Opened under the other Brief (as a pinned link or an old URL would), it moves to its own.
    await openTable(page, w.ws, `brief=${other}&view=${id}`);
    await expect(page).toHaveURL(new RegExp(`brief=${s.brief}`));
    await expect.poll(() => rowNames(page)).toEqual(['Alpha Grid']);
    const res = await page.request.get(`/workspace/${w.ws}/accounts/export?brief=${other}&view=${id}`);
    expect(res.status()).toBe(200);
    const lines = (await res.text()).split('\r\n').filter(Boolean);
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatch(/^Alpha Grid,/);

    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}&view=${randomUUID()}`);
    await expect(page.locator('[data-table-state="view-missing"]')).toContainText("This view isn't available");
    expect((await page.request.get(`/workspace/${w.ws}/accounts/export?brief=${s.brief}&view=${randomUUID()}`)).status()).toBe(404);
  });

  test('a Viewer saves and pins a personal view but is not offered a workspace one (Review Focus 5)', async ({ browser }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const viewer = await newUser('filters-viewer');
    const [member] = await sql<{ id: string }>('insert into public.member (org_id, user_id, email) values ($1, $2, $3) returning id', [w.org, viewer.id, viewer.email]);
    await sql(`insert into public.role_assignment (org_id, member_id, workspace_id, role) values ($1, $2, $3, 'Viewer')`, [w.org, member.id, w.ws]);
    const ctx = await browser.newContext();
    await signIn(ctx, viewer.email);
    const vp = await ctx.newPage();
    await openTable(vp, w.ws, `brief=${s.brief}`);
    await vp.getByRole('button', { name: 'Save view' }).click();
    await expect(saveForm(vp).getByRole('radio', { name: 'Personal' })).toBeVisible();
    await expect(saveForm(vp).getByRole('radio', { name: 'Workspace' })).toHaveCount(0);
    await saveForm(vp).getByLabel('View name').fill('My errors');
    await saveForm(vp).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(vp).toHaveURL(/view=[0-9a-f-]{36}/);
    await vp.getByRole('button', { name: 'Pin to sidebar' }).click();
    await expect(aside(vp).locator('[data-pinned-view]', { hasText: 'My errors' })).toBeVisible();
    await ctx.close();
  });

  test('an Editor is offered Personal and Workspace, and a workspace view is saved as one', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await openTable(page, w.ws, `brief=${s.brief}`);
    await page.getByRole('button', { name: 'Save view' }).click();
    await expect(saveForm(page).getByRole('radio', { name: 'Personal' })).toBeVisible();
    await saveForm(page).getByRole('radio', { name: 'Workspace' }).check();
    await saveForm(page).getByLabel('View name').fill('Team view');
    await saveForm(page).getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page).toHaveURL(/view=[0-9a-f-]{36}/);
    const [row] = await sql<{ visibility: string }>(`select visibility from public.saved_view where workspace_id = $1 and name = 'Team view'`, [w.ws]);
    expect(row.visibility).toBe('workspace');
  });

  test('each preset loads its filter', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await openTable(page, w.ws, `brief=${s.brief}`);
    const go = async (name: string, preset: string) => {
      await viewsNav(page).getByRole('link', { name, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`view=${preset}`));
      await expect(page.locator('[data-table-state="error"], [data-table-state="bad-filter"]')).toHaveCount(0);
    };
    await go('Ready to contact', 'ready_to_contact');
    await expect(table(page).locator('tbody tr')).toHaveCount(1);
    await expect(table(page).locator('tr[data-row="Alpha Grid"]')).toBeVisible();
    await go('Needs evidence', 'needs_evidence');
    await expect(page.locator('[data-table-state="no-match"]')).toBeVisible();
    await go('Stale', 'stale');
    await expect(table(page).locator('tr[data-row="Bravo Power"]')).toBeVisible();
    await go('Errored', 'errored');
    await expect(table(page).locator('tr[data-row="Charlie Wind"]')).toBeVisible();
    await go('By verdict', 'by_verdict');
    await expect(table(page).locator('tbody tr')).toHaveCount(ROWS.length);
    // A preset's filter shows as chips.
    await go('Ready to contact', 'ready_to_contact');
    await page.getByRole('button', { name: /^Filters/ }).click();
    await expect(page.locator('[data-filter-builder]').getByLabel('Filter field')).toHaveCount(2);
  });

  test('search: Ctrl+K focuses it, a seeded name shows, Enter opens it; % matches literally', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await sql(`
      begin;
      select set_config('app.writer', 'derivation', true);
      insert into public.account (org_id, workspace_id, entity_id)
      select '${w.org}', '${w.ws}', app.create_entity('${w.org}', 'company', n, 'tenant') from unnest(array['100% Solar Co', '100 Solar Co']) n;
      select set_config('app.writer', '', true);
      commit;
    `);
    await openTable(page, w.ws, `brief=${s.brief}`);
    const box = page.getByRole('combobox', { name: 'Search accounts' });
    await page.keyboard.press('Control+K');
    await expect(box).toBeFocused();
    await box.fill('grid');
    await expect(page.getByRole('option', { name: /Alpha Grid/ })).toBeVisible();
    await box.press('Enter');
    await page.waitForURL(new RegExp(`/workspace/${w.ws}/accounts/[0-9a-f-]{36}`));

    await page.goto(`/workspace/${w.ws}/accounts?brief=${s.brief}`);
    await expect(table(page)).toHaveAttribute('data-ready', '');
    await box.fill('0% S');
    const results = page.getByRole('listbox', { name: 'Search results' });
    await expect(results.getByRole('option', { name: /100% Solar Co/ })).toBeVisible();
    await expect(results.getByRole('option')).toHaveCount(1); // 100 Solar Co is not a match: % is not a wildcard
  });
});

test.describe('Board, By country and export', () => {
  let userId = '';
  const open = async (page: Page, ws: string, query: string) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/workspace/${ws}/accounts?${query}`);
  };
  const lane = (page: Page, name: string) => page.locator(`[data-lane="${name}"]`);
  const csv = async (page: Page, ws: string, query: string) => {
    const res = await page.request.get(`/workspace/${ws}/accounts/export?${query}`);
    expect(res.status()).toBe(200);
    return res.text();
  };
  /** RFC 4180 parse (quoted fields may hold commas, quotes and newlines). The route's UTF-8 BOM (for Excel) is dropped. */
  const parse = (raw: string): string[][] => {
    const text = raw.replace(/^\uFEFF/, '');
    const rows: string[][] = [];
    let row: string[] = [];
    let f = '';
    let q = false;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') {
          f += '"';
          i++;
        } else if (c === '"') q = false;
        else f += c;
      } else if (c === '"') q = true;
      else if (c === ',') {
        row.push(f);
        f = '';
      } else if (c === '\r' && text[i + 1] === '\n') {
        row.push(f);
        rows.push(row);
        row = [];
        f = '';
        i++;
      } else f += c;
    }
    return rows;
  };

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('board');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'Board Org');
  });

  test('Board by stage: every stage has a column with a count, and the seeded cards sit in the right ones', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await sql(
      `begin; select set_config('app.writer', 'derivation', true);
       update public.account a set stage = 'Engaged' from public.entity e where e.id = a.entity_id and e.display_name = 'Bravo Power' and a.workspace_id = '${w.ws}';
       commit;`,
    );
    await open(page, w.ws, `brief=${s.brief}&layout=board`);
    for (const stage of ['Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off', 'Dormant', 'Disqualified']) {
      await expect(lane(page, stage)).toBeVisible();
    }
    await expect(lane(page, 'Untouched').locator('[data-count]')).toHaveText(String(ROWS.length - 1));
    await expect(lane(page, 'Engaged').locator('[data-count]')).toHaveText('1');
    await expect(lane(page, 'Engaged').locator('[data-row="Bravo Power"]')).toBeVisible();
    await expect(lane(page, 'Untouched').locator('[data-row="Alpha Grid"]')).toBeVisible();
    await page.screenshot({ path: 'test-results/accounts-board.png', fullPage: true });
  });

  test('Board by verdict works the same way', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await open(page, w.ws, `brief=${s.brief}&layout=board&group=verdict`);
    await expect(lane(page, 'Confirmed').locator('[data-row="Alpha Grid"]')).toBeVisible();
    await expect(lane(page, 'Confirmed').locator('[data-count]')).toHaveText('1');
    await expect(lane(page, 'none').locator('[data-count]')).toHaveText(String(ROWS.length - 1));
    for (const v of ['Likely', 'Unverified', 'Contradicted']) await expect(lane(page, v)).toBeVisible();
  });

  test('By country groups the seeded accounts (layout=map, as saved views store it, opens the same)', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    for (const layout of ['country', 'map']) {
      await open(page, w.ws, `brief=${s.brief}&layout=${layout}`);
      await expect(lane(page, 'NL').locator('[data-row="Alpha Grid"]')).toBeVisible();
      await expect(lane(page, 'NL').locator('[data-count]')).toHaveText('1');
      await expect(lane(page, 'none').locator('[data-count]')).toHaveText(String(ROWS.length - 1));
    }
    await page.screenshot({ path: 'test-results/accounts-country.png', fullPage: true });
  });

  test('Export: the header is the visible columns and the row count matches the view', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const raw = await csv(page, w.ws, `brief=${s.brief}`);
    expect(raw.charCodeAt(0), 'a UTF-8 BOM, so Excel reads the names right').toBe(0xfeff);
    const rows = parse(raw);
    expect(rows[0]).toEqual(['Account', 'Country', 'Verdict', 'Score', 'Band', 'Stage', 'Owner', 'Last signal', 'Sites', 'Double', 'Note']);
    expect(rows).toHaveLength(ROWS.length + 1);
    const alpha = rows.find((r) => r[0] === 'Alpha Grid');
    expect(alpha?.slice(1, 6)).toEqual(['NL', 'Confirmed', '90', 'High', 'Untouched']);
    expect(alpha?.[8]).toBe('12');
    const filtered = parse(await csv(page, w.ws, `brief=${s.brief}&f=${enc({ field: { system: 'name' }, op: 'contains', value: 'grid' })}`));
    expect(filtered.length).toBeLessThan(ROWS.length + 1);
  });

  test('CSV injection: =HYPERLINK is neutralised with a leading quote; a comma and newline are quoted', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await sql(`
      begin;
      select set_config('app.writer', 'derivation', true);
      insert into public.account (org_id, workspace_id, entity_id)
      select '${w.org}', '${w.ws}', app.create_entity('${w.org}', 'company', n, 'tenant')
        from unnest(array['=HYPERLINK("x")', E'Comma, Co\\nSecond line', '+cmd', '@sum', '-1']) n;
      select set_config('app.writer', '', true);
      commit;
    `);
    const text = await csv(page, w.ws, `brief=${s.brief}`);
    expect(text).toContain(`"'=HYPERLINK(""x"")"`);
    expect(text).toContain(`"Comma, Co\nSecond line"`);
    const names = parse(text).map((r) => r[0]);
    expect(names).toEqual(expect.arrayContaining([`'=HYPERLINK("x")`, "'+cmd", "'@sum", "'-1", 'Comma, Co\nSecond line']));
  });

  test('Evidence: &evidence=1 adds a source URL column per column', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    const rows = parse(await csv(page, w.ws, `brief=${s.brief}&evidence=1`));
    expect(rows[0].slice(8)).toEqual(['Sites', 'Sites source', 'Double', 'Double source', 'Note', 'Note source']);
    const alpha = rows.find((r) => r[0] === 'Alpha Grid');
    expect(alpha?.[9]).toMatch(/^https:\/\/acc\.example\//);
    expect(alpha?.[11]).toBe(''); // derived: no source page
  });

  test('a bad filter is a 400, and the Export button on the page points at the route', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seed(w);
    expect((await page.request.get(`/workspace/${w.ws}/accounts/export?brief=${s.brief}&f=%25%25%25`)).status()).toBe(400);
    await open(page, w.ws, `brief=${s.brief}&layout=board`);
    await expect(page.locator('[data-export]')).toHaveAttribute('href', new RegExp(`/workspace/${w.ws}/accounts/export\\?brief=${s.brief}`));
  });

  test('a workspace of 300 accounts exports in under 10 s', async ({ page }) => {
    test.setTimeout(120_000);
    const w = await workspaceOf(userId);
    const s = await seed(w);
    await sql(`
      begin;
      select set_config('app.writer', 'derivation', true);
      insert into public.account (org_id, workspace_id, entity_id)
      select '${w.org}', '${w.ws}', app.create_entity('${w.org}', 'company', 'Bulk ' || lpad(n::text, 4, '0'), 'tenant') from generate_series(1, 300) n;
      select set_config('app.writer', '', true);
      commit;
    `);
    const t0 = Date.now();
    const rows = parse(await csv(page, w.ws, `brief=${s.brief}`));
    expect(Date.now() - t0).toBeLessThan(10_000);
    expect(rows).toHaveLength(ROWS.length + 300 + 1);
  });
});
