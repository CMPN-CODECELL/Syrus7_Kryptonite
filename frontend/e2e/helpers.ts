import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { AxeBuilder } from '@axe-core/playwright'
import { test as base, expect, type Locator, type Page } from '@playwright/test'

export { expect }

const here = dirname(fileURLToPath(import.meta.url))
const TAG = process.env.QF_E2E_TAG ?? 'local'

export const VIEWPORTS = [360, 390, 768, 1280, 1440] as const

/** A path under the repo's evidence/ directory; parent folders are created. */
export function evidencePath(...parts: string[]): string {
  const p = resolve(here, '..', '..', 'evidence', ...parts)
  mkdirSync(dirname(p), { recursive: true })
  return p
}

// ---- Console and network guard -------------------------------------------------------------------------

export type FindingKind = 'console-error' | 'console-warning' | 'pageerror' | 'requestfailed' | 'http-error'

export interface Finding {
  kind: FindingKind
  text: string
  url?: string
  status?: number
}

/** A failure a test provokes on purpose. Every field that is given must match the finding. */
export interface Allowance {
  kind?: FindingKind
  url?: RegExp
  status?: number
  text?: RegExp
}

export interface Guard {
  /** Declare the failures this test provokes; anything else still fails it. */
  allow(...allowances: Allowance[]): void
  /** Also watch a page the test opened itself (call before its first navigation). */
  watch(page: Page): Promise<void>
}

// A cancelled request is not a failure: the browser cancels in-flight fetches when a test navigates away, and the
// app closes a run's event stream on purpose once the run ends. These are counted separately in the log.
const CANCELLED = /ERR_ABORTED|NS_BINDING_ABORTED|NS_ERROR_ABORT|cancelled|Load request cancelled/i

// Diagnostics a browser itself prints, not errors in the page. None are allowed today; add an entry per browser only
// with a reason.
const BROWSER_NOISE: Record<string, Allowance[]> = {}

/** What Chromium reports for a request the test aborts with `route.abort()`: the failed request and its console line. */
export const aborted = (url: RegExp): Allowance[] => [
  { kind: 'requestfailed', url, text: /^net::ERR_FAILED / },
  { kind: 'console-error', url, text: /^Failed to load resource: net::ERR_FAILED$/ },
]

const matches = (a: Allowance, f: Finding) =>
  (!a.kind || a.kind === f.kind) &&
  (!a.url || (f.url !== undefined && a.url.test(f.url))) &&
  (a.status === undefined || a.status === f.status) &&
  (!a.text || a.text.test(f.text))

/**
 * Every spec imports `test` from here. The automatic `guard` fixture records console errors and warnings,
 * uncaught exceptions, unhandled promise rejections, failed requests and HTTP responses >= 400, and fails the
 * test on any the test did not allow. Each test's counts are appended to evidence/e2e/console-guard-<tag>.jsonl.
 */
export const test = base.extend<{ guard: Guard }>({
  guard: [
    async ({ page, browserName }, use, testInfo) => {
      const findings: Finding[] = []
      const allowed: Allowance[] = [...(BROWSER_NOISE[browserName] ?? [])]
      let cancelled = 0
      const watched = new WeakSet<Page>()
      const watch = async (p: Page) => {
        if (watched.has(p)) return
        watched.add(p)
        p.on('console', (m) => {
          const t = m.type()
          if (t === 'error' || t === 'warning') findings.push({ kind: t === 'error' ? 'console-error' : 'console-warning', text: m.text(), url: m.location().url || undefined })
        })
        p.on('pageerror', (e) => findings.push({ kind: 'pageerror', text: `${e.name}: ${e.message}` }))
        p.on('requestfailed', (r) => {
          const why = r.failure()?.errorText ?? 'failed'
          if (CANCELLED.test(why)) cancelled++
          else findings.push({ kind: 'requestfailed', text: `${why} ${r.method()} ${r.url()}`, url: r.url() })
        })
        p.on('response', (r) => {
          if (r.status() >= 400) findings.push({ kind: 'http-error', text: `${r.status()} ${r.request().method()} ${r.url()}`, url: r.url(), status: r.status() })
        })
        // Firefox and WebKit do not report unhandled rejections as page errors; make them visible everywhere.
        await p.addInitScript(() => {
          window.addEventListener('unhandledrejection', (e) => {
            const r = e.reason as { stack?: string } | undefined
            console.error(`Unhandled promise rejection: ${r?.stack ?? String(e.reason)}`)
          })
        })
      }
      await watch(page)
      await use({ allow: (...a) => void allowed.push(...a), watch })
      const unexpected = findings.filter((f) => !allowed.some((a) => matches(a, f)))
      const count = (k: FindingKind) => findings.filter((f) => f.kind === k).length
      const record = {
        project: testInfo.project.name,
        test: testInfo.titlePath.slice(1).join(' › '),
        status: testInfo.status,
        consoleErrors: count('console-error'),
        consoleWarnings: count('console-warning'),
        pageErrors: count('pageerror'),
        failedRequests: count('requestfailed'),
        httpErrors: count('http-error'),
        cancelledRequests: cancelled,
        allowed: findings.length - unexpected.length,
        unexpected,
      }
      appendFileSync(evidencePath('e2e', `console-guard-${TAG}.jsonl`), JSON.stringify(record) + '\n')
      if (findings.length) await testInfo.attach('console-guard', { body: JSON.stringify(findings, null, 2), contentType: 'application/json' })
      expect(unexpected, 'browser errors, warnings or failed requests that this test did not provoke').toEqual([])
    },
    { auto: true },
  ],
})

/** Full-page screenshot taken from the top of the page, so the sticky header sits where it belongs. */
export async function capture(page: Page, path: string) {
  await page.evaluate(
    () =>
      new Promise<void>((done) => {
        window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
        requestAnimationFrame(() => requestAnimationFrame(() => done()))
      }),
  )
  await page.evaluate(() => document.fonts.ready.then(() => undefined))
  // A full-page capture paints bottom-sticky bars where the viewport ended, over the middle of the page. Show them
  // where they rest at the end of the flow instead (inline style, so the page CSP is not involved).
  const sticky = await page.evaluate(() => {
    const els = [...document.querySelectorAll<HTMLElement>('.sticky.bottom-0')]
    for (const el of els) el.style.position = 'static'
    return els.length
  })
  await page.screenshot({ path, fullPage: true })
  if (sticky) await page.evaluate(() => document.querySelectorAll<HTMLElement>('.sticky.bottom-0').forEach((el) => el.style.removeProperty('position')))
}

/** The checkbox of one asset row, matched on the exact symbol. */
export function asset(page: Page, symbol: string): Locator {
  return page
    .getByTestId('asset-list')
    .locator(`li[data-symbol="${symbol}"]`)
    .getByRole('checkbox')
}

export const holdings = (page: Page) => page.getByLabel('Number of holdings (K)', { exact: true })

export async function openLab(page: Page) {
  await page.goto('/lab')
  await expect(page.getByTestId('asset-list')).toBeVisible()
  await expect(page.getByTestId('preflight')).toBeVisible()
}

export interface LabOptions {
  k?: number
  seed?: number
  budget?: number
  timeout?: number
  fresh?: boolean
}

/** Fill the Lab form the way a user would. The advanced settings are opened only when one of them is needed. */
export async function configure(page: Page, o: LabOptions) {
  if (o.k !== undefined) await holdings(page).fill(String(o.k))
  if (o.seed === undefined && o.budget === undefined && o.timeout === undefined && !o.fresh) return
  await page.getByText('Advanced settings', { exact: true }).click()
  if (o.budget !== undefined) await page.getByLabel('Evaluation budget', { exact: true }).fill(String(o.budget))
  if (o.seed !== undefined) await page.getByLabel('Seed', { exact: true }).fill(String(o.seed))
  if (o.timeout !== undefined) await page.getByLabel('Timeout', { exact: true }).fill(String(o.timeout))
  if (o.fresh) await page.getByRole('checkbox', { name: /Compute fresh even if an identical run is cached/ }).check()
}

export const runIdOf = (page: Page) => decodeURIComponent(new URL(page.url()).pathname.split('/').pop() ?? '')

/** Submit from the Lab and return the new run id once the run page is open. */
export async function submitRun(page: Page): Promise<string> {
  const submit = page.getByTestId('submit-run')
  await expect(submit).toBeEnabled()
  await submit.click()
  await page.waitForURL(/\/runs\/[^/]+$/)
  return runIdOf(page)
}

/** Fonts loaded and the network quiet, so screenshots and scans see the final layout. */
export async function settle(page: Page) {
  await page.evaluate(() => document.fonts.ready.then(() => undefined))
  await page.waitForLoadState('networkidle', { timeout: 10_000 }).catch(() => undefined)
}

export async function expectNoHorizontalOverflow(page: Page, what: string) {
  const o = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }))
  expect.soft(o.sw, `${what}: the page is ${o.sw}px wide in a ${o.cw}px viewport`).toBeLessThanOrEqual(o.cw)
}

const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']

/** Run axe on the current page, save the findings under evidence/a11y/, and return the serious and critical ones. */
export async function axeScan(page: Page, name: string, note: string) {
  const r = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze()
  const violations = r.violations.map((v) => ({
    id: v.id,
    impact: v.impact ?? null,
    help: v.help,
    nodes: v.nodes.map((n) => ({ target: n.target, summary: n.failureSummary ?? '' })),
  }))
  writeFileSync(
    evidencePath('a11y', `${name}.json`),
    JSON.stringify({ name, note, url: page.url(), viewport: page.viewportSize(), tags: AXE_TAGS, passes: r.passes.length, incomplete: r.incomplete.length, violations }, null, 2),
  )
  return violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
}
