import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end tests: the production build of the web app against a local Supabase stack
 * (`supabase start`), signed in as real users. CI runs them on every pull request.
 * Locally: `supabase start`, export the E2E_* variables (see e2e/support.ts), then `pnpm --filter web e2e`.
 */
const PORT = Number(process.env.E2E_PORT ?? 3100);

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  globalSetup: './e2e/global-setup.ts',
  globalTeardown: './e2e/global-teardown.ts',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `pnpm start`,
    url: `http://localhost:${PORT}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    // The People panel's reveal action talks to this fake worker, never a vendor or a real key.
    env: {
      ...process.env,
      PORT: String(PORT),
      WORKER_REVEAL_URL: 'http://127.0.0.1:3101',
      WORKER_STATUS_TOKEN: 'e2e-reveal-token',
    },
  },
});
