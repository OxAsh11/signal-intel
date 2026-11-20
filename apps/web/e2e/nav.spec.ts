import { randomUUID } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
import { NAV } from '../src/lib/nav';
import { newUser, onboard, signIn, sql } from './support';

const sidebar = (page: Page) => page.getByRole('navigation', { name: 'Main' });
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
    await page.goto('/email/inbox');
    await page.screenshot({ path: 'test-results/shell-phone-email-inbox.png', fullPage: true });
    await page.goto('/');
    await page.screenshot({ path: 'test-results/shell-phone-home.png', fullPage: true });
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
