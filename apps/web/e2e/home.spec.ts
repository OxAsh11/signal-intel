import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import { newUser, onboard, signIn, sql } from './support';

const BRIEF = 'Drone inspection, Indian energy';
const QUOTE = 'Tender: drone survey of 3 solar parks, closes 2 Dec';

const main = (page: Page) => page.locator('main');
const region = (page: Page, name: string | RegExp) =>
  main(page).getByRole('region', typeof name === 'string' ? { name, exact: true } : { name });
const lens = (page: Page) => page.getByRole('combobox', { name: 'Brief lens' });

/** The workspace the onboarded user landed in. */
async function workspaceOf(userId: string): Promise<{ ws: string; org: string }> {
  const [row] = await sql<{ ws: string; org: string }>(
    `select w.id as ws, w.org_id as org
       from public.workspace w join public.member m on m.org_id = w.org_id
      where m.user_id = $1
      order by w.created_at limit 1`,
    [userId],
  );
  return row;
}

type Seeded = { brief: string; acme: string; bharat: string; news: string; tenders: string };

/**
 * A workspace with something in every card, as the worker would have left it (the pgTAP fixture
 * of 26_home, cut down): one Active Brief (v1 ten days ago, v2 two days ago) with an extract
 * column; a News radar (active) and a Tenders radar (in error); Acme Energy (Confirmed, High,
 * a quoted tender claim, a stale cell, Likely → Confirmed yesterday) and Bharat Grid (Likely,
 * Medium, an errored cell); one pending Review item, one unclassified reply, one meeting held, one
 * task due today, and $12.40 spent of a $50 monthly cap. No valid contact, so the journey stops
 * at "Contacts found".
 *
 * One script in one transaction (no bind parameters, so node-pg sends it as a simple query):
 * the account, verdict and score writes need `app.writer` set for the transaction. The ids are
 * our own database's uuids, interpolated as literals.
 */
async function seedPopulated({ ws, org }: { ws: string; org: string }): Promise<Seeded> {
  const tag = randomUUID().replace(/-/g, '');
  const id = (k: string) => `(select v from ids where k = '${k}')`;
  await sql(`
    begin;
    create temp table ids (k text primary key, v uuid) on commit drop;
    insert into public.predicate (key, org_id, workspace_id, label, value_type)
    values ('e2e_sites', '${org}', '${ws}', 'Industrial sites', 'number');
    insert into ids select 'p', id from public.predicate where workspace_id = '${ws}' and key = 'e2e_sites';
    insert into ids values ('b', gen_random_uuid()), ('bv1', gen_random_uuid()), ('bv2', gen_random_uuid()),
                           ('r_news', gen_random_uuid()), ('r_tend', gen_random_uuid());

    insert into public.brief (id, org_id, workspace_id, name, state, intake_status)
    values (${id('b')}, '${org}', '${ws}', '${BRIEF}', 'Draft', 'done');
    insert into public.brief_version (id, brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_at)
    values (${id('bv1')}, ${id('b')}, '${org}', '${ws}', 1, '{"predicates": ["e2e_sites"]}', '{}', '{}', 'Pilot', now() - interval '10 days'),
           (${id('bv2')}, ${id('b')}, '${org}', '${ws}', 2, '{"predicates": ["e2e_sites"]}', '{}', '{}', 'Active', now() - interval '2 days');
    update public.brief set state = 'Active', current_version_id = ${id('bv2')}, updated_at = now() where id = ${id('b')};

    insert into public.radar (id, org_id, workspace_id, brief_id, connector_type, name, status)
    values (${id('r_news')}, '${org}', '${ws}', ${id('b')}, 'news', 'News radar', 'active'),
           (${id('r_tend')}, '${org}', '${ws}', ${id('b')}, 'tender', 'Tenders radar', 'error');

    insert into ids select 'e1', app.create_entity('${org}', 'company', 'Acme Energy', 'tenant');
    insert into ids select 'e2', app.create_entity('${org}', 'company', 'Bharat Grid', 'tenant');
    select set_config('app.writer', 'derivation', true);
    insert into public.account (org_id, workspace_id, entity_id, stage)
    values ('${org}', '${ws}', ${id('e1')}, 'Contacted'), ('${org}', '${ws}', ${id('e2')}, 'Untouched');
    select set_config('app.writer', '', true);
    insert into ids select 'a1', id from public.account where workspace_id = '${ws}' and entity_id = ${id('e1')};
    insert into ids select 'a2', id from public.account where workspace_id = '${ws}' and entity_id = ${id('e2')};

    insert into public.extractor_version (capability, prompt_version, model, parser_version)
    values ('extract.e2e-${tag}', '1', 'test', '1');
    insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, extraction_status, radar_id, created_at)
    values ('${org}', '${ws}', 'radar:' || ${id('r_news')}, 'news', 'https://e2e-home.example/${tag}', now(), 'e2e-home-${tag}', 'extracted', ${id('r_news')}, now());
    insert into ids select 's1', id from public.source_record where content_hash = 'e2e-home-${tag}';
    insert into public.mention (org_id, source_record_id, entity_type, raw_name, entity_id, match_method)
    values ('${org}', ${id('s1')}, 'company', 'Acme Energy', ${id('e1')}, 'exact'),
           ('${org}', ${id('s1')}, 'company', 'Bharat Grid', ${id('e2')}, 'exact');
    insert into ids select 'c1', app.create_claim('${org}', ${id('e1')}, ${id('p')}, '3', '${QUOTE}', null, null, ${id('s1')},
        now() - interval '1 day', (select id from public.extractor_version where capability = 'extract.e2e-${tag}'), 80::smallint);

    select set_config('app.writer', 'derivation', true);
    insert into public.verdict (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids)
    values ('${org}', '${ws}', ${id('bv2')}, ${id('e1')}, 'Confirmed', array[${id('c1')}]),
           ('${org}', '${ws}', ${id('bv2')}, ${id('e2')}, 'Likely', '{}');
    insert into public.score (org_id, workspace_id, account_id, brief_version_id, priority, band)
    values ('${org}', '${ws}', ${id('a1')}, ${id('bv2')}, 90, 'High'),
           ('${org}', '${ws}', ${id('a2')}, ${id('bv2')}, 60, 'Medium');
    select set_config('app.writer', '', true);
    insert into ids select 'v1', id from public.verdict where entity_id = ${id('e1')};
    insert into ids select 'v2', id from public.verdict where entity_id = ${id('e2')};

    -- Verdict history: Acme Likely three days ago, Confirmed yesterday; Bharat's first verdict.
    insert into public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload, created_at)
    select 'verdict.changed', '${org}', '${ws}', 'verdict', (select v from ids where k = x.vk),
           jsonb_build_object('verdict_id', (select v from ids where k = x.vk), 'entity_id', (select v from ids where k = x.ek),
                              'brief_version_id', ${id('bv2')}, 'value', x.val),
           now() - x.age
      from (values ('v1', 'e1', 'Likely', interval '3 days'), ('v1', 'e1', 'Confirmed', interval '1 day'),
                   ('v2', 'e2', 'Likely', interval '2 days')) x(vk, ek, val, age);

    -- Acme's cell ran against older inputs (stale); Bharat's last run failed (errored).
    insert into public.predicate_run (org_id, workspace_id, entity_id, predicate_id, predicate_version, status, queued_at, finished_at, inputs_hash, error)
    values ('${org}', '${ws}', ${id('e1')}, ${id('p')}, 1, 'succeeded', now() - interval '5 days', now() - interval '5 days', 'old-hash', null),
           ('${org}', '${ws}', ${id('e2')}, ${id('p')}, 1, 'error', now() - interval '1 day', now() - interval '1 day', null, 'provider timeout');

    insert into public.contact_point (org_id, workspace_id, entity_id, account_id, type, value_encrypted, status)
    values ('${org}', '${ws}', ${id('e1')}, ${id('a1')}, 'email', 'enc-${tag}', 'risky');
    insert into public.next_step (org_id, workspace_id, account_id, suggested_text)
    values ('${org}', '${ws}', ${id('a1')}, 'Send the tender response deck');
    insert into public.review_item (org_id, workspace_id, mention_id, status)
    select '${org}', '${ws}', m.id, 'pending' from public.mention m where m.source_record_id = ${id('s1')} and m.entity_id = ${id('e2')};
    insert into public.activity (org_id, workspace_id, account_id, kind, occurred_at, payload)
    values ('${org}', '${ws}', ${id('a1')}, 'reply_received', now(), '{}');
    insert into public.meeting (org_id, workspace_id, account_id, external_id, scheduled_at, held_at)
    values ('${org}', '${ws}', ${id('a1')}, 'e2e-home-${tag}', now(), now());
    insert into public.task (org_id, workspace_id, account_id, title, due_date, origin, status)
    values ('${org}', '${ws}', ${id('a1')}, 'Prepare the tender call', (now() at time zone 'utc')::date, 'manual', 'open');
    insert into public.budget (org_id, workspace_id, period, cap_usd) values ('${org}', '${ws}', 'month', 50);
    insert into public.usage_ledger (org_id, workspace_id, capability, provider, model, unit, quantity, cost_usd, status)
    values ('${org}', '${ws}', 'judge', 'anthropic', 'e2e', 'ai_input_tokens', 1000, 12.40, 'ok');
    commit;
  `);
  const [row] = await sql<Seeded>(
    `select b.id as brief,
            (select a.id from public.account a join public.entity e on e.id = a.entity_id
              where a.workspace_id = $1 and e.display_name = 'Acme Energy') as acme,
            (select a.id from public.account a join public.entity e on e.id = a.entity_id
              where a.workspace_id = $1 and e.display_name = 'Bharat Grid') as bharat,
            (select r.id from public.radar r where r.workspace_id = $1 and r.name = 'News radar') as news,
            (select r.id from public.radar r where r.workspace_id = $1 and r.name = 'Tenders radar') as tenders
       from public.brief b where b.workspace_id = $1 and b.name = $2`,
    [ws, BRIEF],
  );
  return row;
}

async function noSidewaysScroll(page: Page, what: string) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow, `${what} scrolls sideways`).toBeLessThanOrEqual(1);
}

test.describe('Home', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('home');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Home');
  });

  test('an empty workspace shows only the journey and "Describe who you sell to"', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Home', exact: true })).toBeVisible();
    const journey = region(page, /^Getting started/);
    await expect(journey).toBeVisible();
    await expect(journey.locator('[data-state="done"]')).toHaveCount(0);
    await expect(journey.locator('[data-step="brief"]')).toHaveAttribute('data-state', 'now');
    await expect(journey.locator('[data-step="first_email"]')).toContainText('Comes with the Email engine');

    const start = region(page, 'Describe who you sell to');
    await expect(start).toBeVisible();
    await expect(start.getByRole('link', { name: 'Describe who you sell to' })).toHaveAttribute('href', '/briefs/new');

    // Nothing else: no tiles, no cards, no header line.
    await expect(main(page).getByRole('region')).toHaveCount(2);
    for (const name of ['Key numbers', 'Needs you', 'Accounts to act on', 'What changed', 'Email engine', 'CRM pipeline', 'Sources']) {
      await expect(region(page, name), name).toHaveCount(0);
    }
    await expect(main(page).getByText('This card could not load')).toHaveCount(0);
    await noSidewaysScroll(page, 'empty Home at 390px');
    await page.screenshot({ path: 'test-results/home-phone-empty.png', fullPage: true });
  });

  test('a populated workspace: every card shows its numbers, and each number links to its rows', async ({ page }) => {
    const w = await workspaceOf(userId);
    const s = await seedPopulated(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    await expect(lens(page)).toHaveValue(s.brief);

    // Header: the lens Brief, its version and state, and the sources running.
    const header = main(page).locator('[data-home="header"]');
    await expect(header.getByRole('link', { name: BRIEF })).toHaveAttribute('href', `/briefs/${s.brief}`);
    await expect(header).toContainText('Brief v2');
    await expect(header).toContainText('Active');
    await expect(header).toContainText('Live since');
    await expect(header.getByRole('link', { name: '1 source running' })).toHaveAttribute('href', '/sources');

    // Journey: four steps done, Contacts found is next, the first email comes with the Email engine.
    const journey = region(page, /^Getting started/);
    await expect(journey.getByRole('heading')).toHaveText('Getting started · 4 of 6 done');
    for (const key of ['brief', 'sources', 'first_accounts', 'first_confirmed']) {
      await expect(journey.locator(`[data-step="${key}"]`), key).toHaveAttribute('data-state', 'done');
    }
    await expect(journey.locator('[data-step="contacts"]')).toHaveAttribute('data-state', 'now');
    await expect(journey.locator('[data-step="contacts"]')).toHaveAttribute('href', '/accounts?view=ready_to_contact');
    await expect(journey.locator('[data-step="first_email"]')).toHaveAttribute('data-state', 'todo');
    await expect(journey.locator('[data-step="first_email"]')).toContainText('Comes with the Email engine');

    // Tiles.
    const tiles = region(page, 'Key numbers');
    const tile = (label: string) => tiles.locator('a.si-metric').filter({ hasText: label });
    const num = (label: string) => tile(label).locator('.si-metric-num');
    await expect(num('Accounts found')).toHaveText('2');
    await expect(tile('Accounts found')).toHaveAttribute('href', '/accounts');
    await expect(num('Confirmed')).toHaveText('1');
    await expect(tile('Confirmed')).toHaveAttribute('href', '/accounts?verdict=Confirmed');
    await expect(num('Replies')).toHaveText('1');
    await expect(tile('Replies')).toHaveAttribute('href', `/workspace/${w.ws}/inbox`);
    await expect(num('Meetings held')).toHaveText('1');
    await expect(tile('Meetings held')).toHaveAttribute('href', `/workspace/${w.ws}/meetings`);
    await expect(num('Spend')).toHaveText('$12.40');
    await expect(tile('Spend')).toContainText('of $50.00 this month');
    await expect(tile('Spend')).toHaveAttribute('href', '/settings');

    // Needs you: the Review count, the reply to classify, stale with its estimate, errored.
    const needs = region(page, 'Needs you');
    await expect(needs.locator('[data-need="review"]')).toHaveText(/1 decision in Review/);
    await expect(needs.locator('[data-need="review"]')).toHaveAttribute('href', '/review');
    await expect(needs.locator('[data-need="replies"]')).toHaveText(/1 reply to classify/);
    await expect(needs.locator('[data-need="replies"]')).toHaveAttribute('href', `/workspace/${w.ws}/inbox`);
    await expect(needs.locator('[data-need="stale"]')).toHaveText(/1 account stale · re-run ≈ (< )?\$\d+\.\d{2}/);
    await expect(needs.locator('[data-need="stale"]')).toHaveAttribute('href', '/accounts?view=stale');
    await expect(needs.locator('[data-need="errored"]')).toHaveText(/1 account with errored cells/);
    await expect(needs.locator('[data-need="errored"]')).toHaveAttribute('href', '/accounts?view=errored');

    // Accounts to act on: Acme first, with its verdict, band and the quote behind it.
    const act = region(page, 'Accounts to act on');
    await expect(act.getByRole('link', { name: 'Ready to contact · 2 →' })).toHaveAttribute('href', '/accounts?view=ready_to_contact');
    const top = act.locator('tbody tr').first();
    await expect(top.getByRole('link', { name: 'Acme Energy' })).toHaveAttribute('href', `/workspace/${w.ws}/accounts/${s.acme}`);
    await expect(top.locator('.si-verdict-confirmed')).toHaveText(/Confirmed/);
    await expect(top.locator('.si-band-high')).toHaveText(/High/);
    await expect(top.locator('q')).toHaveText(QUOTE);
    await expect(top).toContainText('No valid contact yet');
    await expect(top).toContainText('Send the tender response deck');
    await expect(act.locator('tbody tr').nth(1)).toContainText('Bharat Grid');

    // What changed: the verdict change with its quote, the Brief version, the new accounts.
    const changes = region(page, 'What changed');
    // Acme's first Likely (three days ago) is its own line; the change is Likely → Confirmed.
    const verdictLine = changes.locator('[data-change="verdict"]').filter({ hasText: 'Likely → Confirmed' });
    await expect(verdictLine).toContainText(`Acme Energy: Likely → Confirmed · "${QUOTE}"`);
    await expect(verdictLine.getByRole('link', { name: 'Acme Energy' })).toHaveAttribute('href', `/workspace/${w.ws}/accounts/${s.acme}`);
    await expect(changes.locator('[data-change="verdict"]').filter({ hasText: 'Bharat Grid' })).toContainText('Bharat Grid: Likely, first verdict');
    await expect(changes.getByRole('link', { name: `${BRIEF} v2` })).toHaveAttribute('href', `/briefs/${s.brief}`);
    await expect(changes.getByRole('link', { name: '2 new accounts' })).toHaveAttribute('href', `/accounts?radar=${s.news}`);

    // The Email engine placeholder: no sample numbers.
    const email = region(page, 'Email engine');
    await expect(email).toContainText('Sending, approvals and replies arrive with the Email engine');
    await expect(email.getByRole('link', { name: 'Open Email engine' })).toHaveAttribute('href', '/email');

    // CRM: every stage with its count, linked to the pipeline; tasks due today.
    const crm = region(page, 'CRM pipeline');
    await expect(crm.locator('[data-stage]')).toHaveCount(9);
    await expect(crm.getByRole('link', { name: 'Contacted: 1' })).toHaveAttribute('href', '/crm/pipeline?stage=Contacted');
    await expect(crm.getByRole('link', { name: 'Untouched: 1' })).toHaveAttribute('href', '/crm/pipeline?stage=Untouched');
    await expect(crm.getByRole('link', { name: 'Handed off: 0' })).toHaveAttribute('href', '/crm/pipeline?stage=Handed%20off');
    await expect(crm.locator('[data-tasks]')).toHaveText('1 task due today');
    await expect(crm.locator('[data-tasks]')).toHaveAttribute('href', `/workspace/${w.ws}/meetings`);

    // Sources: the radar in error is listed first, though it sorts after News by name.
    const sources = region(page, 'Sources');
    await expect(sources.locator('[data-radar]')).toHaveCount(2);
    await expect(sources.locator('[data-radar]').first()).toHaveAttribute('data-radar', 'Tenders radar');
    await expect(sources.locator('[data-radar]').first()).toContainText('Incident');
    await expect(sources.locator('[data-radar="News radar"]')).toContainText('Healthy');
    await expect(sources.locator('[data-radar="News radar"]')).toContainText('1 record, 7 days');

    await expect(main(page).getByText('This card could not load')).toHaveCount(0);
    await noSidewaysScroll(page, 'Home at 1440px');
    await page.screenshot({ path: 'test-results/home-desktop.png', fullPage: true });

    // Phone: the cards stack in one column and nothing scrolls sideways.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await expect(region(page, 'Sources')).toBeVisible();
    const [needsBox, changesBox] = await Promise.all([needs.boundingBox(), changes.boundingBox()]);
    expect(needsBox && changesBox).toBeTruthy();
    expect(Math.abs(needsBox!.x - changesBox!.x), 'cards share one column').toBeLessThanOrEqual(1);
    expect(changesBox!.y, 'What changed sits below Needs you').toBeGreaterThan(needsBox!.y);
    await noSidewaysScroll(page, 'Home at 390px');
    await page.screenshot({ path: 'test-results/home-phone.png', fullPage: true });
  });

  test('switching the lens to "All Briefs" shows the workspace in the header', async ({ page, context }) => {
    const w = await workspaceOf(userId);
    const s = await seedPopulated(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const header = main(page).locator('[data-home="header"]');
    await expect(header.getByRole('link', { name: BRIEF })).toBeVisible();
    await expect(lens(page)).toHaveValue(s.brief);

    await lens(page).selectOption('all');
    await expect.poll(async () => (await context.cookies()).find((c) => c.name === 'si_brief')?.value).toBe(`${w.ws}:all`);
    await expect(header.getByRole('link', { name: 'Default', exact: true })).toHaveAttribute('href', '/briefs');
    await expect(header).toContainText('All Briefs');
    await expect(header.getByRole('link', { name: '1 Pilot or Active Brief' })).toHaveAttribute('href', '/briefs');
    await expect(header).not.toContainText('Brief v2');
    // The cards follow the lens too: the same Brief is the only one, so the numbers hold.
    await expect(region(page, 'Key numbers').locator('a.si-metric').filter({ hasText: 'Confirmed' }).locator('.si-metric-num')).toHaveText('1');
  });

  test('a card whose RPC fails says so with Retry, and the rest of Home renders', async ({ page }) => {
    const w = await workspaceOf(userId);
    await seedPopulated(w);
    await page.setViewportSize({ width: 1440, height: 900 });
    await sql('revoke execute on function public.home_changes(uuid, uuid) from authenticated');
    try {
      await page.goto('/?brief=all');
      const changes = region(page, 'What changed');
      await expect(changes).toContainText('This card could not load.');
      await expect(changes.getByRole('link', { name: 'Retry' })).toHaveAttribute('href', '/?brief=all');
      await expect(changes.locator('[data-change]')).toHaveCount(0);
      // Only that card failed.
      await expect(main(page).locator('[data-card-error]')).toHaveCount(1);
      await expect(region(page, 'Key numbers').locator('a.si-metric')).toHaveCount(5);
      await expect(region(page, 'Needs you').locator('[data-need="review"]')).toHaveText(/1 decision in Review/);
      await expect(region(page, 'Accounts to act on').getByRole('link', { name: 'Acme Energy' })).toBeVisible();
      await expect(region(page, 'Sources').locator('[data-radar]')).toHaveCount(2);
      await page.screenshot({ path: 'test-results/home-desktop-card-error.png', fullPage: true });
    } finally {
      await sql('grant execute on function public.home_changes(uuid, uuid) to authenticated');
    }
  });
});
