import type { Page } from '@playwright/test'
import { aborted, asset, axeScan, capture, configure, evidencePath, expect, holdings, openLab, runIdOf, submitRun, test, type LabOptions } from './helpers'

// These states need failures on demand, so this file runs against an API started with QF_PIPELINE_IMPL=fake
// (dataset label "synthetic test"). Its magic seeds trigger the failure paths; no result here is a real optimisation.
// Where a response is mocked in the browser instead, the test title says so.

const shot = (page: Page, name: string) => capture(page, evidencePath('screenshots', 'states', `${name}.png`))

// The fake default configuration has no K, so every run sets one. "fresh" skips the result cache.
async function startRun(page: Page, o: LabOptions): Promise<string> {
  await openLab(page)
  await configure(page, { k: 3, fresh: true, ...o })
  return submitRun(page)
}

const stopped = (page: Page) => page.getByTestId('run-stopped')

test('infeasible: the preflight blocks a problem that is too large', async ({ page }) => {
  await openLab(page)
  await configure(page, { k: 3 })
  const submit = page.getByTestId('submit-run')
  await expect(submit).toBeEnabled()
  await page.getByRole('button', { name: 'Select all shown' }).click()
  await expect(page.getByTestId('selected-count')).toContainText('50 of 50 selected')
  await expect(submit).toBeDisabled()
  await expect(page.locator('#submit-why')).toContainText(/blocking issue|Fix \d+ field/)
  await shot(page, 'lab-blocked-by-preflight')
})

test('infeasible: the server rejects the configuration (fake seed 999)', async ({ page }) => {
  await startRun(page, { seed: 999 })
  await expect(stopped(page)).toContainText('The run failed', { timeout: 30_000 })
  await expect(stopped(page)).toContainText('code: infeasible_config')
  await expect(page.getByTestId('retry-run')).toBeVisible()
  await shot(page, 'run-infeasible-config')
})

test('failed run (fake seed 777): a sanitised error and a retry', async ({ page }) => {
  await startRun(page, { seed: 777 })
  await expect(stopped(page)).toContainText('The run failed', { timeout: 30_000 })
  await expect(stopped(page)).toContainText('code: internal_error')
  await expect(page.getByTestId('retry-run')).toBeEnabled()
  // The fake exception text carries a token and a local path; neither may reach the page.
  const text = await page.locator('body').innerText()
  expect(text).not.toContain('sk-test-secret-value')
  expect(text).not.toContain('someone')
  await shot(page, 'run-failed')
  expect(await axeScan(page, 'run-stopped', 'Failed run on the fake pipeline (seed 777)')).toEqual([])
})

test('timeout (fake seed 888 hangs, 10 s limit)', async ({ page }) => {
  await startRun(page, { seed: 888, timeout: 10 })
  await expect(page.getByTestId('stage-list')).toBeVisible()
  await expect(stopped(page)).toContainText('The run hit its time limit', { timeout: 60_000 })
  await expect(page.getByTestId('retry-run')).toBeVisible()
  await shot(page, 'run-timed-out')
})

test('cancel a run, then run the same settings again', async ({ page }) => {
  // A seed nobody used before: the retry is sent without "fresh" and must not be answered from the result cache.
  const first = await startRun(page, { budget: 1500, seed: 1000 + (Date.now() % 900_000) })
  await expect(page.getByTestId('eval-count')).toBeVisible()
  await shot(page, 'run-in-progress')
  await page.getByTestId('cancel-run').click()
  await expect(page.getByRole('dialog')).toContainText('Cancel this run?')
  await page.getByTestId('confirm-cancel').click()
  await expect(stopped(page)).toContainText('The run was cancelled', { timeout: 30_000 })
  await shot(page, 'run-cancelled')

  await page.getByTestId('retry-run').click()
  await expect.poll(() => runIdOf(page)).not.toBe(first)
  await expect(page.getByTestId('stage-list')).toBeVisible()
  // Stop the second run as well so the single worker is free for the next test.
  await page.request.post(`/api/runs/${runIdOf(page)}/cancel`)
  await expect(stopped(page)).toContainText('The run was cancelled', { timeout: 30_000 })
})

test('a page refresh in the middle of a run resumes the live view', async ({ page }) => {
  const id = await startRun(page, { budget: 400 })
  await expect(page.getByTestId('eval-count')).toBeVisible()
  await page.reload()
  await expect(page).toHaveURL(new RegExp(`/runs/${id}$`))
  await expect(page.getByTestId('stage-list').or(page.getByTestId('run-result')).first()).toBeVisible()
  await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 60_000 })
})

test('a lost event stream reconnects, falls back to polling and still shows the result', async ({ page, guard }) => {
  guard.allow(...aborted(/\/api\/runs\/[^/]+\/events\?after=\d+$/))
  await openLab(page)
  await configure(page, { k: 3, fresh: true, budget: 500 })
  // Abort the SSE endpoint only; the snapshot endpoint used for polling stays reachable.
  await page.route(/\/api\/runs\/[^/]+\/events(\?|$)/, (route) => route.abort())
  await submitRun(page)
  await expect(page.getByText(/Reconnecting/).first()).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 90_000 })
})

test('a double-click on submit starts exactly one run', async ({ page }) => {
  await openLab(page)
  await configure(page, { k: 3, fresh: true, seed: 4242 })
  const posts: string[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/runs(\?|$)/.test(r.url())) posts.push(r.url())
  })
  const submit = page.getByTestId('submit-run')
  await expect(submit).toBeEnabled()
  await submit.dblclick()
  await page.waitForURL(/\/runs\/[^/]+$/)
  await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 30_000 })
  expect(posts).toHaveLength(1)
})

test('offline: the app says so, blocks submit and recovers', async ({ page, context }) => {
  await openLab(page)
  await configure(page, { k: 3 })
  const submit = page.getByTestId('submit-run')
  await expect(submit).toBeEnabled()
  await context.setOffline(true)
  await expect(page.getByText('You are offline', { exact: true })).toBeVisible()
  await expect(submit).toBeDisabled()
  await expect(page.locator('#submit-why')).toHaveText('You are offline.')
  await shot(page, 'lab-offline')
  await context.setOffline(false)
  await expect(page.getByText('You are offline', { exact: true })).toBeHidden()
  await expect(submit).toBeEnabled()
})

test('API unreachable (requests aborted in the browser): an error state with a working retry', async ({ page, guard }) => {
  guard.allow(...aborted(/^https?:\/\/[^/]+\/api\//))
  await page.route('**/api/**', (route) => route.abort())
  await page.goto('/lab')
  await expect(page.getByRole('alert').filter({ hasText: 'The Portfolio Lab needs the API' })).toBeVisible({ timeout: 40_000 })
  await expect(page.getByText('API unreachable')).toBeVisible()
  await shot(page, 'lab-api-unreachable')
  await page.unroute('**/api/**')
  await page.getByRole('alert').filter({ hasText: 'The Portfolio Lab needs the API' }).getByRole('button').click()
  await expect(page.getByTestId('asset-list')).toBeVisible()
})

test('keyboard only: skip link, asset, K, submit and result tabs', async ({ page }) => {
  await openLab(page)
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => !!document.activeElement && !!document.querySelector('#main')?.contains(document.activeElement))).toBe(true)

  const titan = asset(page, 'TITAN')
  await titan.focus()
  await page.keyboard.press('Space')
  await expect(titan).toBeChecked()
  await holdings(page).focus()
  await page.keyboard.press('Control+A')
  await page.keyboard.type('3')
  const submit = page.getByTestId('submit-run')
  await expect(submit).toBeEnabled()
  await submit.focus()
  await page.keyboard.press('Enter')
  await page.waitForURL(/\/runs\/[^/]+$/)
  await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 30_000 })

  await page.getByRole('tab', { name: 'Portfolio', exact: true }).focus()
  await page.keyboard.press('ArrowRight')
  const comparison = page.getByRole('tab', { name: 'Comparison' })
  await expect(comparison).toHaveAttribute('aria-selected', 'true')
  await expect(comparison).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.getByRole('tab', { name: 'Data & provenance' })).toHaveAttribute('aria-selected', 'true')
})

test('reduced motion: the hero stays a still illustration', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  const hero = page.getByTestId('hero-stage').first()
  await expect(hero).toHaveAttribute('data-hero-reason', 'reduced-motion')
  await expect(hero).toHaveAttribute('data-hero-mode', 'svg')
  await expect(hero.locator('canvas')).toHaveCount(0)
  await shot(page, 'overview-reduced-motion')
})

test('WebGL unavailable: the hero falls back to the SVG without errors', async ({ page }) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext as (this: HTMLCanvasElement, type: string, ...rest: unknown[]) => unknown
    HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement, type: string, ...rest: unknown[]) {
      return /webgl/i.test(type) ? null : original.call(this, type, ...rest)
    } as typeof HTMLCanvasElement.prototype.getContext
  })
  await page.goto('/')
  const hero = page.getByTestId('hero-stage').first()
  await expect(hero).toHaveAttribute('data-hero-reason', 'no-webgl')
  await expect(hero).toHaveAttribute('data-hero-mode', 'svg')
  await expect(page.getByTestId('cta-optimiser')).toBeVisible()
  await shot(page, 'overview-no-webgl')
})

test('no feasible QAOA sample (result mocked in the browser)', async ({ page }) => {
  await page.route(/\/api\/runs\/[^/]+\/result$/, async (route) => {
    const res = await route.fetch()
    const body = (await res.json()) as { solvers: Array<Record<string, unknown>> }
    const q = body.solvers.find((s) => s.solver === 'qaoa')
    if (q) Object.assign(q, { status: 'no_feasible_sample', portfolio: null, gap: null, bitstring: null, status_detail: 'Mocked in the e2e test: no sampled bitstring met every constraint.' })
    await route.fulfill({ response: res, json: body })
  })
  await startRun(page, { seed: 31 })
  await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('No feasible sample in the final batch')).toBeVisible()
  await shot(page, 'run-no-feasible-sample-mocked')
})

test('loading, empty and stale states (responses delayed or mocked in the browser)', async ({ page }) => {
  let release: () => void = () => undefined
  const gate = new Promise<void>((r) => (release = r))
  await page.route('**/api/presets', async (route) => {
    await gate
    await route.continue()
  })
  await page.goto('/lab')
  await expect(page.getByText('Loading assets and presets')).toBeAttached()
  await shot(page, 'lab-loading')
  release()
  await expect(page.getByTestId('asset-list')).toBeVisible()

  await page.route(/\/api\/runs\?/, (route) => route.fulfill({ json: [], headers: { 'X-Total-Count': '0' } }))
  await page.goto('/history')
  await expect(page.getByText('No runs yet')).toBeVisible()
  await shot(page, 'history-empty-mocked')

  await page.route('**/api/data/status', async (route) => {
    const res = await route.fetch()
    const body = (await res.json()) as { active: { stale: boolean } }
    body.active.stale = true
    await route.fulfill({ response: res, json: body })
  })
  await page.goto('/lab')
  await expect(page.getByText('Market data is stale')).toBeVisible()
  await shot(page, 'lab-stale-mocked')
})

test('an experiment sweep runs and shows its cells', async ({ page }) => {
  await openLab(page)
  await configure(page, { k: 3 })
  await page.getByRole('link', { name: 'Experiments' }).first().click()
  await expect(page).toHaveURL(/\/experiments$/)
  await page.getByLabel('Seeds', { exact: true }).fill('1')
  await expect(page.getByTestId('exp-cells')).toHaveText(/^\d+$/)
  const submit = page.getByTestId('submit-experiment')
  await expect(submit).toBeEnabled()
  await submit.click()
  await page.waitForURL(/\/runs\/exp-[^/]+$/)
  await expect(page.getByTestId('experiment-result')).toBeVisible({ timeout: 90_000 })
  await shot(page, 'experiment-result')
})
