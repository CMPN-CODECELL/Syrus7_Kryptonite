import { readFileSync } from 'node:fs'
import { asset, configure, expect, holdings, runIdOf, test } from './helpers'

// Runs against the API on the real pipeline (downloaded NIFTY 50 history, Aer simulator). Nothing is mocked.
// The guard fixture (helpers.ts) fails the test on any console error or warning, uncaught error or failed request.
test('primary journey: select, constrain, preflight, run, compare, export, reopen from History', async ({ page }) => {
  test.setTimeout(300_000)

  await test.step('the overview leads to the Lab', async () => {
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await page.getByTestId('cta-optimiser').click()
    await expect(page).toHaveURL(/\/lab$/)
    await expect(page.getByTestId('asset-list')).toBeVisible()
  })

  const qubits = page.getByTestId('pf-qubits')
  let qubitsBefore = ''
  await test.step('select assets', async () => {
    await expect(page.getByTestId('selected-count')).toContainText('8 of 50 selected')
    await expect(qubits).toHaveText(/\d+/)
    qubitsBefore = (await qubits.textContent()) ?? ''
    await asset(page, 'TITAN').check()
    await expect(asset(page, 'TITAN')).toBeChecked()
    await expect(page.getByTestId('selected-count')).toContainText('9 of 50 selected')
  })

  await test.step('set constraints', async () => {
    await expect(holdings(page)).toHaveValue('4')
    // "fresh" asks the server to compute instead of replaying a cached result, so the live progress is real.
    await configure(page, { k: 3, fresh: true })
  })

  await test.step('the preflight reflects the new problem', async () => {
    await expect(page.getByTestId('preflight')).toContainText('9 selected')
    await expect(qubits).not.toHaveText(qubitsBefore)
    await expect(qubits).toHaveText(/\d+/)
    await expect(page.getByTestId('submit-run')).toBeEnabled()
  })

  let runId = ''
  await test.step('submit and watch live progress', async () => {
    await page.getByTestId('submit-run').click()
    await page.waitForURL(/\/runs\/run-[^/]+$/)
    runId = runIdOf(page)
    await expect(page.getByTestId('stage-list')).toBeVisible()
    await expect(page.getByTestId('eval-count')).toContainText('objective evaluations')
    await expect(page.getByTestId('stage-list').locator('[aria-current="step"]')).toHaveCount(1)
  })

  await test.step('results', async () => {
    await expect(page.getByTestId('run-result')).toBeVisible({ timeout: 180_000 })
    await expect(page.getByTestId('portfolio-metrics')).toBeVisible()
  })

  await test.step('compare QAOA with the classical baselines', async () => {
    await page.getByRole('tab', { name: 'Comparison' }).click()
    const panel = page.getByRole('tabpanel')
    await expect(panel).toContainText('QAOA (sampled)')
    await expect(panel).toContainText('Exact enumeration')
  })

  await test.step('export the JSON report', async () => {
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByTestId('downloads').getByRole('link', { name: 'JSON', exact: true }).click()])
    expect(download.suggestedFilename()).toMatch(/\.json$/)
    const text = readFileSync(await download.path(), 'utf8')
    expect(() => JSON.parse(text) as unknown).not.toThrow()
    expect(text).toContain(runId)
  })

  await test.step('reopen the run from History', async () => {
    await page.getByRole('link', { name: 'History' }).first().click()
    await expect(page).toHaveURL(/\/history$/)
    await page.getByTestId('history-open').and(page.locator(`[href="/runs/${runId}"]`)).click()
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}$`))
    await expect(page.getByTestId('run-result')).toBeVisible()
  })

})
