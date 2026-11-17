import { expect, test } from '@playwright/test';
import { FLYTBASE_DESCRIPTION, newUser, onboard, signIn, sql } from './support';

const CSV = [
  'url,company,headline',
  'https://example.com/a,Dominion Energy,Utility plans over 100 docked drones',
  'https://example.com/b,Greeley Police Department,Greeley launches Drone as First Responder',
].join('\n');

test.describe('CSV radar upload', () => {
  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('csv');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Radars');
  });

  test('a CSV upload stores the file, creates the radar and queues the fetch', async ({ page }) => {
    await page.goto('/briefs/new');
    await page.locator('textarea[name="description"]').fill(FLYTBASE_DESCRIPTION);
    await page.getByRole('button', { name: /Draft my Brief/ }).click();
    await page.waitForURL(/\/briefs\/[0-9a-f-]{36}$/);
    const briefId = page.url().split('/').pop()!;

    await page.goto(`/briefs/${briefId}/radars/upload`);
    await page.getByLabel('Radar name').fill('Prospect list');
    await page.getByLabel('CSV file').setInputFiles({ name: 'prospects.csv', mimeType: 'text/csv', buffer: Buffer.from(CSV) });
    await page.getByRole('button', { name: /Upload/ }).click();
    await page.waitForURL(new RegExp(`/briefs/${briefId}$`));

    const [radar] = await sql<{ id: string; path: string }>(
      `select id, config->>'storage_path' as path from public.radar
        where brief_id = $1 and connector_type = 'csv_upload'`,
      [briefId],
    );
    expect(radar.path).toMatch(/^uploads\/[0-9a-f-]{36}\/\d+_prospects\.csv$/);
    const [stored] = await sql<{ n: number }>(
      `select count(*)::int as n from storage.objects where bucket_id = 'signal-uploads' and name = $1`,
      [radar.path],
    );
    expect(stored.n).toBe(1);
    const [queued] = await sql<{ n: number }>(
      `select count(*)::int as n from public.outbox where event = 'radar.fetch_queued' and aggregate_id = $1`,
      [radar.id],
    );
    expect(queued.n).toBe(1);
  });

  test('a rejected upload says why instead of doing nothing', async ({ page }) => {
    await page.goto('/briefs/new');
    await page.locator('textarea[name="description"]').fill(FLYTBASE_DESCRIPTION);
    await page.getByRole('button', { name: /Draft my Brief/ }).click();
    await page.waitForURL(/\/briefs\/[0-9a-f-]{36}$/);
    const briefId = page.url().split('/').pop()!;

    await page.goto(`/briefs/${briefId}/radars/upload`);
    await page.getByLabel('Radar name').fill('Prospect list');
    await page.getByLabel('CSV file').setInputFiles({ name: 'prospects.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4') });
    await page.getByRole('button', { name: /Upload/ }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'Upload a .csv file' })).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/briefs/${briefId}/radars/upload`));
    const [n] = await sql<{ n: number }>(
      `select count(*)::int as n from public.radar where brief_id = $1 and connector_type = 'csv_upload'`,
      [briefId],
    );
    expect(n.n).toBe(0);
  });
});
