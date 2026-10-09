import { defineConfig, devices } from '@playwright/test'

// The servers are started by hand (see docs/UI.md): a production preview that proxies to the API on the real
// pipeline, and a second one that proxies to an API started with QF_PIPELINE_IMPL=fake for the failure states.
const REAL = process.env.QF_E2E_REAL_URL ?? 'http://127.0.0.1:5201'
const FAKE = process.env.QF_E2E_FAKE_URL ?? 'http://127.0.0.1:5200'
const tag = process.env.QF_E2E_TAG ?? 'local'
const CHROMIUM = devices['Desktop Chrome']

export default defineConfig({
  testDir: './e2e',
  outputDir: '../.tmp/pw-results',
  globalSetup: './e2e/global-setup.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['junit', { outputFile: `../evidence/e2e/playwright-${tag}.xml` }], ['json', { outputFile: `../evidence/e2e/playwright-${tag}.json` }]],
  use: { baseURL: REAL, trace: 'retain-on-failure', screenshot: 'only-on-failure', actionTimeout: 15_000, navigationTimeout: 30_000 },
  projects: [
    { name: 'chromium', testMatch: /(journey|views|perf)\.spec\.ts/, use: CHROMIUM },
    { name: 'chromium-states', testMatch: /states\.spec\.ts/, use: { ...CHROMIUM, baseURL: FAKE } },
    { name: 'firefox', testMatch: /journey\.spec\.ts/, use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', testMatch: /journey\.spec\.ts/, use: { ...devices['Desktop Safari'] } },
  ],
})
