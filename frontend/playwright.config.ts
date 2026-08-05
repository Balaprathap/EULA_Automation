import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration.
 *
 * These specs exercise the real stack, so they need a running API, worker,
 * Postgres, Redis, and Supabase project. They are excluded from the default CI
 * run and are executed against a deployed or docker-compose environment:
 *
 *   E2E_BASE_URL=http://localhost:3000 \
 *   E2E_EMAIL=you@example.com E2E_PASSWORD=... npm run e2e
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
