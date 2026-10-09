import { writeFileSync } from 'node:fs'
import type { Browser } from '@playwright/test'
import { evidencePath, expect, test, type Guard } from './helpers'

// Lab-style measurement of the production build in Chromium. The throttling is applied through the DevTools protocol
// and uses the numbers of Lighthouse's mobile "Slow 4G" preset.
const PROFILE = {
  name: 'Mobile 390x844 @3x, Slow 4G (150 ms RTT, 1.6 Mbit/s down, 750 kbit/s up), 4x CPU slowdown, cold cache',
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 3,
  latencyMs: 150,
  downKbps: 1600,
  upKbps: 750,
  cpuSlowdown: 4,
}
const BUDGET = { lcpMs: 2500, cls: 0.1, interactionMs: 100 }
const RUNS = 3

// "preview-gzip" is `vite preview` with QF_PREVIEW_GZIP=1 (what a compressing static host sends) and is held to the
// budget; "api-served" is the build as the QuantFolio API serves it (gzip since backend/quantfolio/api/app.py added
// GZipMiddleware), recorded for comparison. Both use the same real-pipeline API.
// The API sends Cross-Origin-Opener-Policy: same-origin, so a load that starts from about:blank in a fresh headless
// context first swaps to a new renderer process (about 2.4 s here, also without any throttling). Desktop Chrome
// usually has a spare renderer ready, so "api-served-same-origin-start" also records loads that start from a
// same-origin page (/favicon.svg, cache still disabled) to separate that swap from the page itself.
const API_BASE = process.env.QF_PERF_API_URL ?? 'http://127.0.0.1:8101'
const TARGETS = [
  { id: 'preview-gzip', base: process.env.QF_PERF_GZIP_URL ?? 'http://127.0.0.1:5202', enforced: true, sameOriginStart: false },
  { id: 'api-served', base: API_BASE, enforced: false, sameOriginStart: false },
  { id: 'api-served-same-origin-start', base: API_BASE, enforced: false, sameOriginStart: true },
]
const ROUTES = ['/', '/lab']

interface Vitals {
  lcpMs: number
  lcpElement: string
  cls: number
  fcpMs: number | null
  transferKb: number
}

const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]

async function measure(browser: Browser, guard: Guard, url: string, sameOriginStart: boolean): Promise<Vitals> {
  const context = await browser.newContext({ viewport: PROFILE.viewport, deviceScaleFactor: PROFILE.deviceScaleFactor, isMobile: true, hasTouch: true })
  const page = await context.newPage()
  await guard.watch(page)
  if (sameOriginStart) await page.goto(new URL('/favicon.svg', url).href)
  const cdp = await context.newCDPSession(page)
  await cdp.send('Network.enable')
  await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
  await cdp.send('Network.emulateNetworkConditions', {
    offline: false,
    latency: PROFILE.latencyMs,
    downloadThroughput: (PROFILE.downKbps * 1000) / 8,
    uploadThroughput: (PROFILE.upKbps * 1000) / 8,
  })
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: PROFILE.cpuSlowdown })
  await page.addInitScript(() => {
    const w = window as unknown as { __qf: { lcp: number; el: string; cls: number } }
    w.__qf = { lcp: 0, el: '', cls: 0 }
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as Array<PerformanceEntry & { element?: Element | null }>) {
        w.__qf.lcp = e.startTime
        w.__qf.el = e.element ? `${e.element.tagName.toLowerCase()}: ${(e.element.textContent ?? '').trim().slice(0, 60)}` : ''
      }
    }).observe({ type: 'largest-contentful-paint', buffered: true })
    new PerformanceObserver((list) => {
      for (const e of list.getEntries() as Array<PerformanceEntry & { value: number; hadRecentInput: boolean }>) if (!e.hadRecentInput) w.__qf.cls += e.value
    }).observe({ type: 'layout-shift', buffered: true })
  })
  await page.goto(url, { waitUntil: 'load', timeout: 90_000 })
  await page.waitForLoadState('networkidle', { timeout: 60_000 }).catch(() => undefined)
  await page.waitForTimeout(3_000)
  const v = await page.evaluate(() => {
    const w = window as unknown as { __qf: { lcp: number; el: string; cls: number } }
    const fcp = performance.getEntriesByName('first-contentful-paint')[0]
    const bytes = (performance.getEntriesByType('resource') as PerformanceResourceTiming[]).reduce((n, r) => n + r.transferSize, 0)
    return { lcpMs: Math.round(w.__qf.lcp), lcpElement: w.__qf.el, cls: Number(w.__qf.cls.toFixed(4)), fcpMs: fcp ? Math.round(fcp.startTime) : null, transferKb: Math.round(bytes / 1024) }
  })
  await context.close()
  return v
}

/** Click an asset checkbox and time until the selection count has changed and a frame was produced. */
async function interaction(browser: Browser, guard: Guard, base: string, cpuSlowdown: number): Promise<number[]> {
  const context = await browser.newContext({ viewport: cpuSlowdown > 1 ? PROFILE.viewport : { width: 1280, height: 800 } })
  const page = await context.newPage()
  await guard.watch(page)
  if (cpuSlowdown > 1) await (await context.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: cpuSlowdown })
  await page.goto(`${base}/lab`)
  await expect(page.getByTestId('pf-qubits')).toBeVisible()
  const samples = await page.evaluate(async () => {
    const count = document.querySelector('[data-testid="selected-count"]')
    const box = [...document.querySelectorAll<HTMLInputElement>('[data-testid="asset-list"] input[type=checkbox]')].find((i) => !i.checked)
    if (!count || !box) return []
    const out: number[] = []
    for (let i = 0; i < 7; i++) {
      const t0 = performance.now()
      const done = new Promise<number>((res) => {
        const mo = new MutationObserver(() => {
          mo.disconnect()
          requestAnimationFrame(() => res(performance.now() - t0))
        })
        mo.observe(count, { subtree: true, childList: true, characterData: true })
      })
      box.click()
      out.push(Math.round((await done) * 10) / 10)
      await new Promise((r) => setTimeout(r, 150))
    }
    return out
  })
  await context.close()
  return samples
}

test('mobile LCP and CLS under the documented throttling profile, and control response time', async ({ browser, guard }) => {
  test.setTimeout(600_000)
  const results = []
  for (const target of TARGETS)
    for (const route of ROUTES) {
      const runs: Vitals[] = []
      for (let i = 0; i < RUNS; i++) runs.push(await measure(browser, guard, target.base + route, target.sameOriginStart))
      const lcpMs = median(runs.map((r) => r.lcpMs))
      const cls = Math.max(...runs.map((r) => r.cls))
      results.push({ target: target.id, base: target.base, route, enforced: target.enforced, sameOriginStart: target.sameOriginStart, lcpMsMedian: lcpMs, clsMax: cls, withinBudget: lcpMs <= BUDGET.lcpMs && cls <= BUDGET.cls, runs })
    }
  const fast = await interaction(browser, guard, TARGETS[0].base, 1)
  const slow = await interaction(browser, guard, TARGETS[0].base, PROFILE.cpuSlowdown)
  const controls = { what: 'asset checkbox click until the selection count is updated and painted', desktopMs: fast, desktopMedianMs: median(fast), mobile4xCpuMs: slow, mobile4xCpuMedianMs: median(slow) }
  writeFileSync(
    evidencePath('e2e', 'perf.json'),
    JSON.stringify({ measuredAt: new Date().toISOString(), browser: `chromium ${browser.version()}`, profile: PROFILE, budget: BUDGET, runsPerPage: RUNS, results, controls }, null, 2),
  )
  for (const r of results.filter((x) => x.enforced)) {
    expect.soft(r.lcpMsMedian, `LCP on ${r.route} (${r.target})`).toBeLessThanOrEqual(BUDGET.lcpMs)
    expect.soft(r.clsMax, `CLS on ${r.route} (${r.target})`).toBeLessThanOrEqual(BUDGET.cls)
  }
  expect.soft(controls.desktopMedianMs, 'control response on desktop').toBeLessThan(BUDGET.interactionMs)
})
