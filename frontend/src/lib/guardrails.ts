// Every guardrail in plain words, grouped into six families, for the Guardrails page and the landing summary.
// Limits that the server reports (GET /api/capabilities) are filled in live; the rest are fixed in the backend code.

import type { Capabilities } from '../api/contract.gen'
import { CLOUD } from './cloud'
import { formatInt } from './format'

export interface GuardItem {
  title: string
  body: string
}

export interface GuardFamily {
  id: string
  letter: string
  title: string
  /** One line under the family's title. */
  short: string
  items: GuardItem[]
}

export interface LiveLimits {
  qubits: string
  noiseQubits: string
  memory: string
  exact: string
  queue: string
  shots: string
  finalShots: string
  evaluations: string
  upload: string
  body: string
  experiment: string
  depths: string
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function bytes(v: number | null): string {
  if (v === null) return '…'
  if (v >= 1024 * 1024) return `${formatInt(v / (1024 * 1024))} MB`
  return `${formatInt(v / 1024)} KB`
}

/** The server's limits as short strings; "…" while they load. */
export function liveLimits(c: Capabilities | undefined): LiveLimits {
  const l = c?.limits ?? {}
  const show = (v: number | null, unit = '') => (v === null ? '…' : `${formatInt(v)}${unit}`)
  const expS = num(l.experiment_timeout_s)
  return {
    qubits: show(num(c?.max_qubits)),
    noiseQubits: show(num(c?.max_qubits_noise)),
    memory: show(num(l.memory_limit_mb), ' MB'),
    exact: show(num(c?.max_exact_assets)),
    queue: show(num(c?.queue_capacity)),
    shots: show(num(l.max_shots)),
    finalShots: show(num(l.max_final_shots)),
    evaluations: show(num(l.max_evaluations)),
    upload: bytes(num(l.max_upload_bytes)),
    body: bytes(num(l.max_body_bytes)),
    experiment: expS === null ? '…' : expS % 60 === 0 ? `${formatInt(expS / 60)} min` : `${formatInt(expS)} s`,
    depths: c?.depths?.length ? `${Math.min(...c.depths)} to ${Math.max(...c.depths)}` : '…',
  }
}

export function guardrailFamilies(L: LiveLimits): GuardFamily[] {
  return [
    {
      id: 'preflight',
      letter: 'A',
      title: 'Checks before a run',
      short: 'A run that cannot work is stopped first, with a reason.',
      items: [
        { title: CLOUD ? 'Fits the server' : 'Fits this computer', body: `Up to ${L.qubits} qubits (${L.noiseQubits} with noise) and ${L.memory} of memory.` },
        { title: 'Rules can all be met', body: 'Holdings, sector caps, target return and budget are checked together.' },
        { title: 'Enough price history', body: '95 % coverage per stock, at least 60 trading days and 2 stocks.' },
        { title: 'Valid inputs only', body: 'Unknown stocks, out-of-range dates and unsupported settings are rejected.' },
        { title: 'Exact search where possible', body: `Up to ${L.exact} stocks. Beyond that it is skipped, never faked.` },
      ],
    },
    {
      id: 'fairness',
      letter: 'B',
      title: 'A fair comparison',
      short: 'The quantum answer is its own. Nothing classical is swapped in.',
      items: [
        { title: 'QAOA answers for itself', body: 'Its pick comes from its own measurements only.' },
        { title: 'No silent swaps', body: 'No valid sample? It says so instead of borrowing an answer.' },
        { title: 'Same scoring', body: 'Every answer is re-checked and scored by the same code.' },
        { title: 'An honest gap', body: 'The gap always names what it is measured against.' },
        { title: 'Reproducible', body: 'Seeds, data hashes and versions are saved with every run.' },
        { title: 'No hype', body: 'No claim of quantum advantage. Circuits run on a simulator.' },
      ],
    },
    {
      id: 'data',
      letter: 'C',
      title: 'Data integrity',
      short: 'No peeking at the future. Every fix is shown.',
      items: [
        { title: 'No look-ahead', body: 'Picks use training data only. Later prices test them once.' },
        { title: 'Careful gap filling', body: 'Gaps are filled from the past, for at most 5 days.' },
        { title: 'Stable risk estimates', body: 'Shrinkage keeps the risk model stable. Fixes are reported.' },
        { title: 'Traceable data', body: 'Every dataset is hashed. Stale or synthetic data is flagged.' },
        { title: 'Short tests labelled', body: 'Under 20 days of later prices is marked "insufficient".' },
        { title: 'Strict uploads', body: `UTF-8 CSV, up to ${L.upload} and 50 stocks. No formulas.` },
        { title: 'Live prices are display only', body: 'The ticker and price chart never feed a run. Runs use the stored dataset.' },
      ],
    },
    {
      id: 'runtime',
      letter: 'D',
      title: 'Reliable runs',
      short: 'Queued, time-limited, cancellable and isolated.',
      items: [
        { title: 'A bounded queue', body: `Up to ${L.queue} jobs wait. A full queue says "try again shortly".` },
        { title: 'Time limits', body: `Quick 90 s, Balanced 5 min, Research 20 min, experiments ${L.experiment}.` },
        { title: 'Cancel at any time', body: 'The worker stops within 3 seconds.' },
        { title: 'Crashes stay contained', body: 'Only that run fails. The app keeps working.' },
        { title: 'Restarts and repeats', body: 'Interrupted runs are marked. Repeats replay a labelled cached result.' },
        {
          title: 'Steady progress',
          body: CLOUD ? 'Checked every few seconds. Resumes after a dropped connection.' : 'Resumes after a dropped connection, or falls back to polling.',
        },
      ],
    },
    {
      id: 'security',
      letter: 'E',
      title: 'Security',
      // The hosted demo is public, so it is guarded by daily limits rather than by staying on one machine.
      short: CLOUD ? 'Daily limits, strict inputs, secrets kept out of sight.' : 'Local only, strict inputs, secrets kept out of sight.',
      items: [
        CLOUD
          ? { title: 'Daily limits', body: 'New runs count against a daily limit for the whole demo and for each visitor. Security headers are strict.' }
          : { title: 'Local only', body: 'Listens on 127.0.0.1 with strict security headers.' },
        { title: 'Size limits', body: `${L.body} of JSON, ${L.upload} of CSV. No NaN or infinity.` },
        { title: 'No paths or links', body: 'Inputs never accept file paths, URLs or code.' },
        { title: 'Secrets stay secret', body: 'The IBM token is never logged, stored or sent to the browser.' },
        { title: 'Clean errors', body: 'A code and a plain message. No stack traces.' },
      ],
    },
    {
      id: 'interface',
      letter: 'F',
      title: 'On screen',
      short: 'The interface never hides a problem.',
      items: [
        { title: 'Run blocked on errors', body: 'The Run button says why it is off.' },
        { title: 'Disclaimer everywhere', body: '"Not investment advice" on every page.' },
        { title: 'Data warnings', body: 'Synthetic, stale or uploaded data is flagged.' },
        { title: 'Honest labels', body: 'Cached and example runs are labelled.' },
        { title: 'Nothing lost', body: 'Going offline keeps your inputs. Failed runs offer Retry.' },
        { title: 'Cost shown first', body: 'Experiments show run count and time before they start.' },
      ],
    },
  ]
}
