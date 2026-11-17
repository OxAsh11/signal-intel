import { expect, test } from '@playwright/test';
import { FLYTBASE_DESCRIPTION, newUser, onboard, signIn, sql } from './support';

test('another organisation cannot see or open a Brief', async ({ browser }) => {
  // Owner A creates a Brief.
  const a = await newUser('owner-a');
  const ctxA = await browser.newContext();
  await signIn(ctxA, a.email);
  const pageA = await ctxA.newPage();
  await onboard(pageA, 'Org A');
  await pageA.goto('/briefs/new');
  await pageA.locator('textarea[name="description"]').fill(FLYTBASE_DESCRIPTION);
  await pageA.getByRole('button', { name: /Draft my Brief/ }).click();
  await pageA.waitForURL(/\/briefs\/[0-9a-f-]{36}$/);
  const id = pageA.url().split('/').pop()!;
  const [{ name }] = await sql<{ name: string }>('select name from public.brief where id = $1', [id]);
  await ctxA.close();

  // Owner B, in a different organisation, gets nothing.
  const b = await newUser('owner-b');
  const ctxB = await browser.newContext();
  await signIn(ctxB, b.email);
  const pageB = await ctxB.newPage();
  await onboard(pageB, 'Org B');

  await pageB.goto('/briefs');
  await expect(pageB.getByText('No Briefs yet.')).toBeVisible();
  await expect(pageB.getByText(name)).toHaveCount(0);

  const res = await pageB.goto(`/briefs/${id}`);
  expect(res?.status()).toBe(404);
  await expect(pageB.getByText(name)).toHaveCount(0);
  await ctxB.close();
});
