import type { Page } from '@playwright/test'
import { VIEWPORTS, axeScan, capture, evidencePath, expect, expectNoHorizontalOverflow, settle, test } from './helpers'

// Real pipeline. The run view shows the recorded example run that the API serves at /api/example.
interface View {
  name: string
  path: (exampleId: string) => string
  ready: (page: Page) => Promise<void>
}

const VIEWS: View[] = [
  { name: 'landing', path: () => '/', ready: (p) => expect(p.getByTestId('cta-dashboard')).toBeVisible() },
  {
    name: 'dashboard',
    path: () => '/dashboard',
    ready: async (p) => {
      await expect(p.getByTestId('cta-optimiser')).toBeVisible()
      await expect(p.getByTestId('recent-runs')).toBeVisible()
    },
  },
  {
    name: 'lab',
    path: () => '/lab',
    ready: async (p) => {
      await expect(p.getByTestId('asset-list')).toBeVisible()
      await expect(p.getByTestId('pf-qubits')).toBeVisible()
    },
  },
  { name: 'run', path: (id) => `/runs/${id}`, ready: (p) => expect(p.getByTestId('run-result')).toBeVisible() },
  { name: 'experiments', path: () => '/experiments', ready: (p) => expect(p.getByTestId('submit-experiment')).toBeVisible() },
  { name: 'history', path: () => '/history', ready: (p) => expect(p.getByTestId('history-open').first()).toBeVisible() },
  { name: 'guardrails', path: () => '/guardrails', ready: (p) => expect(p.getByTestId('guard-family').first()).toBeVisible() },
  { name: 'not-found', path: () => '/no-such-page', ready: (p) => expect(p.getByRole('heading', { level: 1 })).toBeVisible() },
]

const OTHER_TABS = [
  ['comparison', 'Comparison'],
  ['qaoa', 'QAOA details'],
  ['provenance', 'Data & provenance'],
] as const

let exampleId = ''
test.beforeAll(async ({ request }) => {
  const r = await request.get('/api/example')
  exampleId = ((await r.json()) as { run_id: string | null }).run_id ?? ''
  expect(exampleId, 'the API serves a recorded example run').not.toBe('')
})

for (const view of VIEWS) {
  test(`${view.name}: screenshots at ${VIEWPORTS.join('/')} px without horizontal overflow`, async ({ page }) => {
    test.setTimeout(180_000)
    for (const width of VIEWPORTS) {
      await page.setViewportSize({ width, height: width < 768 ? 800 : 900 })
      await page.goto(view.path(exampleId))
      await view.ready(page)
      await settle(page)
      await expectNoHorizontalOverflow(page, `${view.name} at ${width}px`)
      await capture(page, evidencePath('screenshots', `${view.name}-${width}.png`))
      if (view.name !== 'run' || (width !== 390 && width !== 1280)) continue
      for (const [slug, label] of OTHER_TABS) {
        await page.getByRole('tab', { name: label }).click()
        await settle(page)
        await expectNoHorizontalOverflow(page, `run / ${label} at ${width}px`)
        await capture(page, evidencePath('screenshots', `run-${slug}-${width}.png`))
      }
    }
  })

  test(`${view.name}: axe finds no serious or critical issues`, async ({ page }) => {
    await page.goto(view.path(exampleId))
    await view.ready(page)
    await settle(page)
    expect(await axeScan(page, view.name, 'Real pipeline, desktop 1280 px')).toEqual([])
    if (view.name === 'run')
      for (const [slug, label] of OTHER_TABS) {
        await page.getByRole('tab', { name: label }).click()
        await settle(page)
        expect(await axeScan(page, `run-${slug}`, `Real pipeline, desktop 1280 px, tab "${label}"`)).toEqual([])
      }
    await page.setViewportSize({ width: 390, height: 800 })
    await page.goto(view.path(exampleId))
    await view.ready(page)
    await settle(page)
    expect(await axeScan(page, `${view.name}-mobile`, 'Real pipeline, mobile 390 px')).toEqual([])
  })
}

test('sidebar: a rail of icons that the arrow button opens and closes, remembered across pages', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/dashboard')
  await expect(page.getByTestId('cta-optimiser')).toBeVisible()
  const rail = page.getByTestId('sidebar')
  const toggle = page.getByTestId('sidebar-toggle')
  const main = page.locator('#main')
  await expect(rail).toHaveAttribute('data-open', 'false')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  const closed = await main.boundingBox()

  // Hovering does nothing; the button opens the menu and the page makes room instead of being covered.
  await rail.hover()
  await expect(rail).toHaveAttribute('data-open', 'false')
  await toggle.click()
  await expect(rail).toHaveAttribute('data-open', 'true')
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await expect(rail.getByRole('link', { name: 'Portfolio Lab' })).toBeVisible()
  await expect.poll(async () => (await main.boundingBox())?.x).toBeGreaterThan((closed?.x ?? 0) + 150)

  // The choice survives navigation and a reload.
  await rail.getByRole('link', { name: 'History' }).click()
  await expect(page).toHaveURL(/\/history$/)
  await page.reload()
  await expect(rail).toHaveAttribute('data-open', 'true')

  // Keyboard: the button works with Enter too.
  await toggle.focus()
  await page.keyboard.press('Enter')
  await expect(rail).toHaveAttribute('data-open', 'false')
  await expect.poll(async () => (await main.boundingBox())?.x).toBe(closed?.x)
})
