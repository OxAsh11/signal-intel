import { expect, test } from '@playwright/test';
import { newUser, onboard, signIn } from './support';

test('a signed-out visitor is sent to the sign-in page and keeps where they were going', async ({ page }) => {
  await page.goto('/briefs');
  await expect(page).toHaveURL(/\/login\?next=%2Fbriefs/);
  await expect(page.getByPlaceholder('name@company.com')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Email me a sign-in link' })).toBeVisible();
});

test('a new user creates their organisation and lands on Today', async ({ page, context }) => {
  const user = await newUser('onboard');
  await signIn(context, user.email);

  await page.goto('/');
  await expect(page).toHaveURL(/\/onboarding/);
  await onboard(page, 'FlytBase E2E', 'Drones');

  await expect(page.getByText('FlytBase E2E', { exact: false }).first()).toBeVisible();
  await expect(page.getByText(user.email)).toBeVisible();
  await expect(page.getByText('Owner').first()).toBeVisible();
});
