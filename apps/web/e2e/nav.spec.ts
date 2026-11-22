import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import { NAV } from '../src/lib/nav';
import { FLYTBASE_DRAFT, lensCookie, newUser, onboard, signIn, sql } from './support';

const sidebar = (page: Page) => page.getByRole('navigation', { name: 'Main' });
const aside = (page: Page) => page.getByRole('complementary', { name: 'Sidebar' });
const lens = (page: Page) => page.getByRole('combobox', { name: 'Brief lens' });
const applyLens = (page: Page) => page.getByRole('button', { name: 'Apply Brief lens' });
/** Chooses in the lens the keyboard way: selecting moves the choice, Apply submits it. */
async function chooseLens(page: Page, value: string) {
  await lens(page).selectOption(value);
  await applyLens(page).click();
}
/** The next Server Action request's response (the lens submits one). */
const lensAction = (page: Page) =>
  page.waitForResponse((r) => r.request().method() === 'POST' && r.request().headers()['next-action'] !== undefined);
const TOP_LABELS = NAV.map((i) => i.label);
const SUB_LABELS = NAV.flatMap((i) => (i.children ?? []).map((c) => [i.label, c.label] as const));

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

test.describe('journey navigation', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('nav');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Nav');
  });

  test('the sidebar shows the four groups in order and every top-level item', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const nav = sidebar(page);
    await expect(nav.locator('span.si-label')).toHaveText(['Find', 'Work', 'Engage', 'Measure']);
    // Each group is a named group for assistive tech, holding its own items.
    await expect(nav.getByRole('group', { name: 'Find', exact: true }).getByRole('link', { name: 'Briefs', exact: true })).toBeVisible();
    await expect(nav.getByRole('group', { name: 'Work', exact: true }).getByRole('link', { name: 'Review', exact: true })).toBeVisible();
    for (const label of TOP_LABELS) {
      await expect(nav.getByRole('link', { name: label, exact: true }), label).toBeVisible();
    }
    await expect(nav.getByRole('link', { name: 'Home', exact: true })).toHaveAttribute('aria-current', 'page');
    await page.screenshot({ path: 'test-results/shell-desktop-home.png', fullPage: true });
  });

  test('on desktop every Email engine and CRM sub-item is visible', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const nav = sidebar(page);
    expect(SUB_LABELS).toHaveLength(7);
    for (const [, label] of SUB_LABELS) {
      await expect(nav.getByRole('link', { name: label, exact: true }), label).toBeVisible();
    }
    // A sub-item is the current page on its route; its parent is marked active too.
    await page.goto('/email/approvals');
    await expect(nav.getByRole('link', { name: 'Approvals', exact: true })).toHaveAttribute('aria-current', 'page');
    await expect(nav.getByRole('link', { name: 'Email engine', exact: true })).toHaveAttribute('aria-current', 'true');
  });

  test('old routes redirect to their new names', async ({ page }) => {
    const cases = [
      ['/discovery', '/sources', 'Sources'],
      ['/campaigns', '/email/sequences', 'Sequences'],
      ['/inbox', '/email/inbox', 'Inbox'],
      ['/meetings', '/crm/meetings', 'Meetings'],
    ] as const;
    for (const [from, to, heading] of cases) {
      await page.goto(from);
      await expect(page, from).toHaveURL(new RegExp(`${to}$`));
      await expect(page.getByRole('heading', { level: 1, name: heading, exact: true }), from).toBeVisible();
    }
    // /accounts sends the query on to the workspace's table (P5 honours the filters).
    const { ws } = await workspaceOf(userId);
    await page.goto('/accounts?view=stale&verdict=Confirmed&brief=all');
    await expect(page).toHaveURL(new RegExp(`/workspace/${ws}/accounts\\?view=stale&verdict=Confirmed&brief=all$`));
    await page.goto('/accounts');
    await expect(page).toHaveURL(new RegExp(`/workspace/${ws}/accounts$`));
    // Sources is today's Discovery page, moved: its funnel is there.
    await page.goto('/discovery');
    await expect(page.getByRole('heading', { name: 'What happened to every page' })).toBeVisible();
  });

  test('placeholders say when they arrive and keep today’s screens reachable', async ({ page }) => {
    const { ws } = await workspaceOf(userId);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/email/inbox');
    const main = page.locator('main');
    await expect(main.getByText('Coming with the Email engine', { exact: true })).toBeVisible();
    await expect(main.getByRole('link', { name: "Open today's inbox" })).toHaveAttribute('href', `/workspace/${ws}/inbox`);
    // One highlighted row: the Inbox sub-item is the page; its parent is not highlighted on desktop.
    const nav = sidebar(page);
    const inbox = nav.getByRole('link', { name: 'Inbox', exact: true });
    await expect(inbox).toHaveAttribute('aria-current', 'page');
    await expect(inbox).toHaveClass(/si-nav-on/);
    const parent = nav.getByRole('link', { name: 'Email engine', exact: true });
    await expect(parent).toHaveAttribute('aria-current', 'true');
    await expect(parent).not.toHaveClass(/si-nav-on/);
    await expect(nav.locator('.si-nav-on')).toHaveCount(1);
    await page.screenshot({ path: 'test-results/shell-desktop-email-inbox.png', fullPage: true });

    await page.goto('/people');
    await expect(main.getByText('Coming in P6', { exact: true })).toBeVisible();

    await page.goto('/crm');
    await expect(main.getByText('Coming with the CRM', { exact: true })).toBeVisible();
    for (const label of ['Pipeline', 'Meetings', 'Tasks']) {
      await expect(main.getByRole('heading', { level: 2, name: label, exact: true })).toBeVisible();
    }
    await page.screenshot({ path: 'test-results/shell-desktop-crm.png', fullPage: true });
  });

  test('at 390px the nav is one row of top-level items and nothing scrolls sideways', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const path of ['/', '/email', '/email/inbox', '/people']) {
      await page.goto(path);
      // The nav streams in behind the shell: wait for it before reading what it renders.
      await expect(sidebar(page).getByRole('link', { name: 'Home', exact: true })).toBeVisible();
      const links = await sidebar(page)
        .getByRole('link')
        .evaluateAll((els) =>
          els
            .filter((el) => el.getClientRects().length > 0)
            .map((el) => ({ text: (el as HTMLElement).innerText.trim(), top: Math.round(el.getBoundingClientRect().top) })),
        );
      expect(links.map((l) => l.text), path).toEqual(TOP_LABELS);
      expect(new Set(links.map((l) => l.top)).size, `${path} nav is one row`).toBe(1);
      await expect(sidebar(page).locator('span.si-label').first()).toBeHidden();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${path} scrolls sideways`).toBeLessThanOrEqual(1);
    }
    // Sub-items move into the page's tab row: every Email engine page, sibling to sibling.
    await page.goto('/email/inbox');
    const tabs = page.getByRole('navigation', { name: 'Email engine pages' });
    await expect(tabs.getByRole('link')).toHaveText(['Sequences', 'Approvals', 'Inbox', 'Mailboxes']);
    await expect(tabs.getByRole('link', { name: 'Inbox', exact: true })).toHaveAttribute('aria-current', 'page');
    await page.screenshot({ path: 'test-results/shell-phone-email-inbox.png', fullPage: true });
    await tabs.getByRole('link', { name: 'Approvals', exact: true }).click();
    await expect(page).toHaveURL(/\/email\/approvals$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Approvals', exact: true })).toBeVisible();
    await expect(tabs.getByRole('link', { name: 'Approvals', exact: true })).toHaveAttribute('aria-current', 'page');
    const tabOverflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(tabOverflow, '/email/approvals scrolls sideways').toBeLessThanOrEqual(1);
    // The CRM pages carry their own row, and on desktop the row is hidden (the sidebar lists them).
    await page.goto('/crm');
    await expect(page.getByRole('navigation', { name: 'CRM pages' }).getByRole('link')).toHaveText(['Pipeline', 'Meetings', 'Tasks']);
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole('navigation', { name: 'CRM pages' })).toBeHidden();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.screenshot({ path: 'test-results/shell-phone-home.png', fullPage: true });
  });

  test('at 390px the page header sits right under the sidebar, with no gap', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const path of ['/', '/email/inbox']) {
      await page.goto(path);
      await expect(sidebar(page).getByRole('link', { name: 'Home', exact: true })).toBeVisible();
      await expect(lens(page)).toBeVisible();
      const [side, header] = await Promise.all([aside(page).boundingBox(), page.locator('header').first().boundingBox()]);
      expect(side && header, path).toBeTruthy();
      const gap = header!.y - (side!.y + side!.height);
      expect(gap, `${path}: header top vs sidebar bottom`).toBeGreaterThanOrEqual(-1);
      expect(gap, `${path}: header top vs sidebar bottom`).toBeLessThanOrEqual(16);
      await page.screenshot({ path: `test-results/shell-phone-${path === '/' ? 'home' : 'email-inbox'}-top.png` });
    }
  });

  test('the sidebar counts pending review decisions, and shows nothing at zero', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const nav = sidebar(page);
    await expect(nav.getByRole('link', { name: 'Review', exact: true })).toBeVisible();
    await expect(nav.getByText('pending')).toHaveCount(0);

    const { ws, org } = await workspaceOf(userId);
    await sql(
      `with sr as (
         insert into public.source_record (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash)
         values ($1, $2, 'radar:e2e', 'news', 'https://example.com/nav-review', now(), $3)
         returning id
       ), men as (
         insert into public.mention (org_id, source_record_id, entity_type, raw_name)
         select $1, sr.id, 'company', 'Acme Ports' from sr
         returning id
       )
       insert into public.review_item (org_id, workspace_id, mention_id, match_method, match_confidence)
       select $1, $2, men.id, 'name_country', 70 from men`,
      [org, ws, randomUUID().replace(/-/g, '')],
    );
    await page.reload();
    const review = nav.getByRole('link', { name: /^Review\s*1\s*pending$/ });
    await expect(review).toBeVisible();
    await expect(review).toContainText('1');
  });
});

/** A Brief in the workspace; `publishedHoursAgo` publishes version 1 that long ago (as Start pilot writes it). */
async function seedBrief(
  ws: { ws: string; org: string },
  name: string,
  state: 'Draft' | 'Pilot' | 'Active' | 'Paused' | 'Archived',
  publishedHoursAgo: number | null,
): Promise<string> {
  const [{ id }] = await sql<{ id: string }>(
    `insert into public.brief (org_id, workspace_id, name, state) values ($1, $2, $3, 'Draft') returning id`,
    [ws.org, ws.ws, name],
  );
  if (publishedHoursAgo == null) {
    if (state !== 'Draft') await sql(`update public.brief set state = $2 where id = $1`, [id, state]);
    return id;
  }
  const [v] = await sql<{ id: string }>(
    `insert into public.brief_version (brief_id, org_id, workspace_id, version, config, rubric, weights, change_note, published_at)
     values ($1, $2, $3, 1, $4::jsonb, '{}', '{}', 'Pilot', now() - make_interval(hours => $5::int))
     returning id`,
    [id, ws.org, ws.ws, JSON.stringify(FLYTBASE_DRAFT), publishedHoursAgo],
  );
  await sql(`update public.brief set state = $2, current_version_id = $3, updated_at = now() where id = $1`, [id, state, v.id]);
  return id;
}

/** A second workspace in the same organisation (the Owner sees every workspace of the org). */
async function secondWorkspace(org: string, name = 'Second'): Promise<string> {
  const [{ id }] = await sql<{ id: string }>(`insert into public.workspace (org_id, name) values ($1, $2) returning id`, [org, name]);
  return id;
}

test.describe('Brief lens', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('lens');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Lens');
    await page.setViewportSize({ width: 1440, height: 900 });
  });

  test('with no cookie it shows the most recently published Pilot or Active Brief', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    await seedBrief(ws, 'Older Brief', 'Active', 48);
    const newer = await seedBrief(ws, 'Newer Brief', 'Pilot', 2);
    await seedBrief(ws, 'Draft Brief', 'Draft', null);
    // Archived, though published last: never the lens and never listed.
    await seedBrief(ws, 'Archived Brief', 'Archived', 1);

    expect(await lensCookie(context)).toBeUndefined();
    await page.goto('/');
    await expect(lens(page)).toHaveValue(newer);
    await expect(lens(page).locator('option:checked')).toHaveText('Newer Brief · Pilot');
    await expect(lens(page).locator('option')).toHaveText(['All Briefs', 'Draft Brief', 'Newer Brief · Pilot', 'Older Brief']);
    // Top of the sidebar, under the brand and above the nav; reachable by keyboard.
    const brand = await aside(page).getByRole('link', { name: 'Signal Intel' }).boundingBox();
    const select = await lens(page).boundingBox();
    const home = await sidebar(page).getByRole('link', { name: 'Home', exact: true }).boundingBox();
    expect(select!.y).toBeGreaterThan(brand!.y);
    expect(select!.y).toBeLessThan(home!.y);
    await lens(page).focus();
    await expect(lens(page)).toBeFocused();
    await page.screenshot({ path: 'test-results/shell-desktop-lens.png', fullPage: true });
  });

  test('choosing "All Briefs" persists across navigation', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    await seedBrief(ws, 'Ports Brief', 'Active', 5);
    const latest = await seedBrief(ws, 'Mines Brief', 'Active', 1);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(latest);

    await chooseLens(page, 'all');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${ws.ws}:all`);
    const cookie = (await lensCookie(context))!;
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe('Lax');
    const days = (cookie.expires - Date.now() / 1000) / 86400;
    expect(days).toBeGreaterThan(89);
    expect(days).toBeLessThan(91);

    for (const path of ['/briefs', '/sources', '/email/inbox', '/']) {
      await page.goto(path);
      await expect(lens(page), path).toHaveValue('all');
      await expect(lens(page).locator('option:checked'), path).toHaveText('All Briefs');
    }
  });

  test('opening a Brief makes it the lens', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    const older = await seedBrief(ws, 'Harbour Brief', 'Active', 30);
    const latest = await seedBrief(ws, 'Quarry Brief', 'Active', 1);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(latest);

    await page.goto(`/briefs/${older}`);
    await expect(page.getByText('Brief · Active')).toBeVisible();
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${ws.ws}:${older}`);
    await expect(lens(page)).toHaveValue(older);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(older);
    await expect(lens(page).locator('option:checked')).toHaveText('Harbour Brief');
  });

  test('a URL or cookie naming an archived, deleted or foreign Brief falls back without error', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    const latest = await seedBrief(ws, 'Live Brief', 'Active', 1);
    const other = await seedBrief(ws, 'Other Brief', 'Active', 10);
    const archived = await seedBrief(ws, 'Retired Brief', 'Archived', 0);
    const wsB = await secondWorkspace(ws.org);
    const foreign = await seedBrief({ ws: wsB, org: ws.org }, 'Elsewhere Brief', 'Active', 0);

    // A valid ?brief= outranks everything.
    await page.goto(`/?brief=${other}`);
    await expect(lens(page)).toHaveValue(other);

    for (const bad of [archived, foreign, randomUUID(), 'not-a-brief']) {
      await page.goto(`/?brief=${bad}`);
      await expect(sidebar(page).getByRole('link', { name: 'Home', exact: true }), bad).toBeVisible();
      await expect(lens(page), bad).toHaveValue(latest);
      await expect(page.getByText(/Application error|could not be found/), bad).toHaveCount(0);
    }

    // An old cookie naming an archived Brief, or another workspace's Brief, is skipped too.
    // Both the encoded form the app writes and a raw `:` are read the same way.
    for (const value of [
      encodeURIComponent(`${ws.ws}:${archived}`),
      encodeURIComponent(`${ws.ws}:${foreign}`),
      `${ws.ws}:${archived}`,
    ]) {
      await context.addCookies([{ name: 'si_brief', value, domain: 'localhost', path: '/', sameSite: 'Lax', httpOnly: true }]);
      await page.goto('/briefs');
      await expect(lens(page), value).toHaveValue(latest);
    }

    // A tampered `next` that points off-site is refused: the choice is saved, the action answers
    // with no redirect, nothing leaves localhost, and the page stays exactly where it was.
    const offsite: string[] = [];
    await page.route(/evil\.example/, (route) => {
      offsite.push(route.request().url());
      return route.abort();
    });
    const tampered = [
      '/\t/evil.example',
      '//evil.example/x',
      'https://evil.example/',
      String.raw`/\evil.example`,
      'javascript:alert(1)',
      '/.//evil.example',
      '/a/..//evil.example',
    ];
    for (const next of tampered) {
      const label = JSON.stringify(next);
      await context.addCookies([
        { name: 'si_brief', value: encodeURIComponent(`${ws.ws}:${latest}`), domain: 'localhost', path: '/', sameSite: 'Lax', httpOnly: true },
      ]);
      await page.goto(`/briefs?brief=${other}`);
      await expect(lens(page)).toHaveValue(other);
      // Select first (it re-renders the form), then tamper, then submit.
      await lens(page).selectOption('all');
      await page.locator('input[name="next"]').evaluate((el: HTMLInputElement, v) => (el.value = v), next);
      const action = lensAction(page);
      await applyLens(page).click();
      const res = await action;
      expect(res.headers()['x-action-redirect'], label).toBeUndefined();
      await expect.poll(async () => (await lensCookie(context))?.value, label).toBe(`${ws.ws}:all`);
      // A redirect, even to a local path, would have dropped `?brief=`: it is still there.
      const url = new URL(page.url());
      expect(`${url.host}${url.pathname}${url.search}`, label).toBe(`${new URL(res.url()).host}/briefs?brief=${other}`);
      expect(offsite, label).toEqual([]);
    }

    // Choosing on a page whose URL names a Brief lands on the same page without it.
    await context.addCookies([
      { name: 'si_brief', value: encodeURIComponent(`${ws.ws}:${latest}`), domain: 'localhost', path: '/', sameSite: 'Lax', httpOnly: true },
    ]);
    await page.goto(`/briefs?brief=${other}`);
    await expect(lens(page)).toHaveValue(other);
    const action = lensAction(page);
    await chooseLens(page, 'all');
    // Control for the checks above: a real redirect does show in the action's response.
    expect((await action).headers()['x-action-redirect']).toMatch(/^\/briefs(;|$)/);
    await expect(page).toHaveURL(/\/briefs$/);
    await expect(lens(page)).toHaveValue('all');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${ws.ws}:all`);
    expect(offsite).toEqual([]);
  });

  test('from the keyboard, moving through the lens applies nothing until Enter or Apply', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    await seedBrief(ws, 'Ports Brief', 'Active', 5);
    const latest = await seedBrief(ws, 'Mines Brief', 'Active', 1);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(latest);
    const posts: string[] = [];
    page.on('request', (r) => {
      if (r.method() === 'POST' && r.headers()['next-action'] !== undefined) posts.push(r.url());
    });

    // Arrowing changes the selection (Windows and Linux fire a change per key): nothing is sent.
    await lens(page).focus();
    await page.keyboard.press('ArrowUp');
    await page.keyboard.press('ArrowUp');
    await page.waitForTimeout(1000);
    expect(posts, 'arrow keys submit nothing').toEqual([]);
    expect(await lensCookie(context)).toBeUndefined();
    await expect(lens(page)).toBeFocused();

    // The Apply button is next in the tab order, and Enter on the select applies too.
    await page.keyboard.press('Tab');
    await expect(applyLens(page)).toBeFocused();
    await lens(page).focus();
    const chosen = await lens(page).inputValue();
    await page.keyboard.press('Enter');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${ws.ws}:${chosen}`);
    expect(posts).toHaveLength(1);
  });

  test('a pointer choice applies in one step', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    const latest = await seedBrief(ws, 'Mines Brief', 'Active', 1);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(latest);
    // A click opens the native list (not scriptable); the pointerdown it starts with marks the
    // change that follows as a pointer choice.
    await lens(page).dispatchEvent('pointerdown');
    await lens(page).selectOption('all');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${ws.ws}:all`);
    await expect(lens(page)).toHaveValue('all');
  });

  test('a choice the server refuses shows the server value again', async ({ page, context }) => {
    const ws = await workspaceOf(userId);
    const older = await seedBrief(ws, 'Harbour Brief', 'Active', 30);
    const latest = await seedBrief(ws, 'Quarry Brief', 'Active', 1);
    await page.goto('/');
    await expect(lens(page)).toHaveValue(latest);

    await lens(page).selectOption(older);
    // Archived in the meantime (another tab, another user): the action refuses it.
    await sql(`update public.brief set state = 'Archived' where id = $1`, [older]);
    const action = lensAction(page);
    await applyLens(page).click();
    await action;
    await expect(lens(page)).toHaveValue(latest);
    await expect(lens(page).locator('option:checked')).toHaveText('Quarry Brief');
    await expect(lens(page).locator('option')).toHaveText(['All Briefs', 'Quarry Brief']);
    expect(await lensCookie(context)).toBeUndefined();
  });

  test('a user in two workspaces gets a lens resolved within each one', async ({ page, context }) => {
    const a = await workspaceOf(userId);
    const b = await secondWorkspace(a.org);
    const alpha = await seedBrief(a, 'Alpha Brief', 'Active', 3);
    const bravo = await seedBrief({ ws: b, org: a.org }, 'Bravo Brief', 'Pilot', 2);
    const workspace = page.getByRole('combobox', { name: 'Workspace' });

    await page.goto('/');
    await expect(lens(page)).toHaveValue(alpha);
    await chooseLens(page, 'all');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${a.ws}:all`);

    // In B the cookie set in A is ignored: B resolves to its own latest Brief.
    await workspace.selectOption({ label: 'FlytBase Lens · Second' });
    await expect(lens(page)).toHaveValue(bravo);
    await expect(lens(page).locator('option')).toHaveText(['All Briefs', 'Bravo Brief · Pilot']);
    expect((await lensCookie(context))?.value).toBe(`${a.ws}:all`);

    await chooseLens(page, 'all');
    await expect.poll(async () => (await lensCookie(context))?.value).toBe(`${b}:all`);

    // And back in A, B's choice does not apply.
    await workspace.selectOption({ label: 'FlytBase Lens · Default' });
    await expect(lens(page)).toHaveValue(alpha);
    await expect(lens(page).locator('option')).toHaveText(['All Briefs', 'Alpha Brief']);
  });
});

test.describe('budget meter', () => {
  let userId = '';

  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('meter');
    userId = user.id;
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Meter');
  });

  const spend = (ws: { ws: string; org: string }, usd: number) =>
    sql(
      `insert into public.usage_ledger (org_id, workspace_id, capability, provider, model, unit, quantity, cost_usd, status)
       values ($1, $2, 'judge', 'anthropic', 'e2e', 'ai_input_tokens', 1000, $3, 'ok')`,
      [ws.org, ws.ws, usd],
    );

  test('85% of the monthly cap shows in mustard, 100% in lava; at 390px it moves to Settings', async ({ page }) => {
    const ws = await workspaceOf(userId);
    await sql(`insert into public.budget (org_id, workspace_id, period, cap_usd) values ($1, $2, 'month', 100)`, [ws.org, ws.ws]);
    await spend(ws, 85);

    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const meter = aside(page).getByRole('link', { name: /\$85\.00 \/ \$100\.00/ });
    await expect(meter).toBeVisible();
    await expect(meter).toHaveAttribute('href', '/settings');
    await expect(meter).toHaveClass(/si-fam-mustard/);
    await expect(meter).toHaveAttribute('data-level', 'warn');
    await page.screenshot({ path: 'test-results/shell-desktop-budget.png', fullPage: true });

    // Hidden from the phone row, and shown on Settings instead.
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(meter).toBeHidden();
    await page.goto('/settings');
    await expect(page.locator('main').getByRole('link', { name: /\$85\.00 \/ \$100\.00/ })).toBeVisible();
    await expect(aside(page).getByRole('link', { name: /\$85\.00/ })).toBeHidden();
    await page.screenshot({ path: 'test-results/shell-phone-settings.png', fullPage: true });

    await spend(ws, 20);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const over = aside(page).getByRole('link', { name: /\$105\.00 \/ \$100\.00/ });
    await expect(over).toBeVisible();
    await expect(over).toHaveClass(/si-fam-lava/);
    await expect(over).toHaveAttribute('data-level', 'over');
  });

  test('the colour follows the exact share of the cap: 79.96% is not yet mustard', async ({ page }) => {
    const ws = await workspaceOf(userId);
    await sql(`insert into public.budget (org_id, workspace_id, period, cap_usd) values ($1, $2, 'month', 100)`, [ws.org, ws.ws]);
    await spend(ws, 79.96);
    // The rounded pct the RPC returns is 80.0.
    const [{ pct }] = await sql<{ pct: string }>(`select (app.home_spend($1) ->> 'pct') as pct`, [ws.ws]);
    expect(Number(pct)).toBe(80);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const meter = aside(page).getByRole('link', { name: /\$79\.96 \/ \$100\.00/ });
    await expect(meter).toBeVisible();
    await expect(meter).toHaveAttribute('data-level', 'ok');
    await expect(meter).not.toHaveClass(/si-fam-/);

    await spend(ws, 0.04);
    await page.reload();
    const at80 = aside(page).getByRole('link', { name: /\$80\.00 \/ \$100\.00/ });
    await expect(at80).toHaveAttribute('data-level', 'warn');
    await expect(at80).toHaveClass(/si-fam-mustard/);
  });

  test('with no monthly cap it shows the spend and "no cap"', async ({ page }) => {
    const ws = await workspaceOf(userId);
    await spend(ws, 12.5);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/');
    const meter = aside(page).getByRole('link', { name: /\$12\.50 · no cap/ });
    await expect(meter).toBeVisible();
    await expect(meter).toHaveAttribute('data-level', 'none');
    await expect(meter).not.toHaveClass(/si-fam-/);
  });
});
