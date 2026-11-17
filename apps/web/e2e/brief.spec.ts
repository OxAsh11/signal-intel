import { expect, test, type Page } from '@playwright/test';
import { FLYTBASE_DESCRIPTION, FLYTBASE_DRAFT, newUser, onboard, signIn, sql } from './support';

/** Step 1 of Brief Studio through the real form; returns the new Brief's id. */
async function createBrief(page: Page): Promise<string> {
  await page.goto('/briefs/new');
  await page.locator('textarea[name="description"]').fill(FLYTBASE_DESCRIPTION);
  await page.locator('input[name="website"]').fill('https://flytbase.com/');
  await page.getByRole('button', { name: /Draft my Brief/ }).click();
  await page.waitForURL(/\/briefs\/[0-9a-f-]{36}$/);
  return page.url().split('/').pop()!;
}

test.describe('Brief Studio', () => {
  test.beforeEach(async ({ page, context }) => {
    const user = await newUser('brief');
    await signIn(context, user.email);
    await onboard(page, 'FlytBase Briefs');
  });

  test('the description, website and name reach the AI intake request', async ({ page }) => {
    const id = await createBrief(page);
    await expect(page.getByText('The AI is drafting this Brief from your description.')).toBeVisible();

    const [brief] = await sql<{ name: string; intake_status: string }>('select name, intake_status from public.brief where id = $1', [id]);
    expect(brief.intake_status).toBe('pending');
    expect(brief.name.length).toBeGreaterThan(0);

    const [event] = await sql<{ description: string }>(
      `select payload->>'description' as description from public.outbox
        where event = 'brief.intake_requested' and aggregate_id = $1`,
      [id],
    );
    expect(event.description).toContain('drone autonomy platform');
    expect(event.description).toContain('Our website: https://flytbase.com/');
  });

  test('a drafted Brief shows every part and starts a pilot', async ({ page }) => {
    const id = await createBrief(page);
    // Play the worker: save the draft exactly as the AI intake job does.
    await sql('select app.save_brief_draft($1, $2::jsonb)', [id, JSON.stringify(FLYTBASE_DRAFT)]);

    await page.reload();
    await expect(page.getByText('Drafted by AI from your description.')).toBeVisible();
    await expect(page.getByText(FLYTBASE_DRAFT.hypothesis)).toBeVisible();
    await expect(page.getByText('Head of Security')).toBeVisible();

    const start = page.getByRole('button', { name: 'Start pilot' });
    await expect(start).toBeEnabled();
    await start.click();
    await expect(page.getByText('Brief · Pilot')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Pilot results' })).toBeVisible();

    const [row] = await sql<{ state: string; versions: number; radars: number }>(
      `select b.state::text as state,
              (select count(*) from public.brief_version v where v.brief_id = b.id)::int as versions,
              (select count(*) from public.radar r where r.brief_id = b.id)::int as radars
         from public.brief b where b.id = $1`,
      [id],
    );
    expect(row).toEqual({ state: 'Pilot', versions: 1, radars: expect.any(Number) });
  });

  test('a failed AI draft says so and blocks the pilot', async ({ page }) => {
    const id = await createBrief(page);
    await sql('select app.fail_brief_intake($1)', [id]);

    await page.reload();
    await expect(page.getByText('The AI draft failed.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Start pilot' })).toBeDisabled();
  });
});
