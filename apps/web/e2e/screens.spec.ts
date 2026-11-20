import { expect, test } from '@playwright/test';
import { allRoutes } from '../src/lib/nav';
import { newUser, onboard, signIn } from './support';

// The widths the product must fit without sideways scrolling: desktop and phone.
const WIDTHS = [
  { width: 1440, height: 900 },
  { width: 390, height: 844 },
];

test.describe('every screen', () => {
  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('screens');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Screens');
  });

  for (const viewport of WIDTHS) {
    test(`loads without errors and fits ${viewport.width}px`, async ({ page }) => {
      await page.setViewportSize(viewport);
      const errors: string[] = [];
      page.on('pageerror', (e) => errors.push(`${page.url()}: ${e.message}`));
      page.on('console', (m) => {
        if (m.type() === 'error') errors.push(`${page.url()}: ${m.text()}`);
      });

      for (const s of [...allRoutes(), '/briefs/new']) {
        const res = await page.goto(s);
        expect(res?.status(), `${s} status`).toBeLessThan(400);
        await expect(page, `${s} stays signed in`).not.toHaveURL(/\/(login|onboarding)/);
        await expect(page.locator('main').first(), `${s} renders`).toBeVisible();
        await expect(page.getByText('Application error'), `${s} has no error page`).toHaveCount(0);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        expect(overflow, `${s} scrolls sideways at ${viewport.width}px`).toBeLessThanOrEqual(1);
      }
      expect(errors, 'browser errors').toEqual([]);
    });
  }
});
