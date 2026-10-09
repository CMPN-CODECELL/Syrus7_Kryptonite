// Every guardrail in plain words, grouped into six families, for the Guardrails page and the landing summary.
// Limits that the server reports (GET /api/capabilities) are filled in live; the rest are fixed in the backend code.

import type { Capabilities } from '../api/contract.gen'
import { formatInt } from './format'

export interface GuardItem {
  title: string
  body: string
  /** The code a check reports, for readers who want to match it in the API or the logs. */
  code?: string
}

export interface GuardFamily {
  id: string
  letter: string
  title: string
  /** One line for the landing page. */
  short: string
  /** Two or three sentences at the top of the family's card. */
  intro: string
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
      short: 'A run that cannot work is stopped before it starts, with a plain reason.',
      intro:
        'Every change in the Portfolio Lab is checked live. An error blocks the run, a warning lets it run with a note, and an info line is just a hint. The Run button stays off while any error remains.',
      items: [
        {
          title: 'The problem fits this computer',
          body: `More than ${L.qubits} qubits (${L.noiseQubits} with simulated hardware noise), or a memory estimate above ${L.memory}, blocks the run.`,
          code: 'too_many_qubits · noise_too_many_qubits · memory_limit',
        },
        {
          title: 'The rules can all be met together',
          body: 'Holdings count, sector caps, target return and lot budget are checked against each other. If no portfolio can satisfy them all, it says so, for example "proven infeasible: 0 of 70 combinations".',
          code: 'sector_caps_infeasible · target_return_unreachable · lot_budget_infeasible',
        },
        {
          title: 'Enough clean price history',
          body: 'Each stock needs at least 95 % price coverage in the training window, and the window needs at least 60 trading days and 2 stocks.',
          code: 'insufficient_history · insufficient_training_data · too_few_assets',
        },
        {
          title: 'Only valid inputs',
          body: 'Unknown stocks or datasets, dates outside the data, and settings a mode does not support are rejected with the reason.',
          code: 'unknown_symbols · dates_outside_dataset · unsupported_constraint_for_mode',
        },
        {
          title: 'Exact search only where it is possible',
          body: `Exact search, which tries every valid option, runs for up to ${L.exact} stocks. Beyond that it is skipped and labelled, never faked.`,
          code: 'exact_unsupported',
        },
      ],
    },
    {
      id: 'fairness',
      letter: 'B',
      title: 'A fair, honest comparison',
      short: 'The quantum answer is its own: nothing classical is swapped in.',
      intro: 'QAOA and the four classical methods solve exactly the same problem and are scored by the same code, so the comparison means something.',
      items: [
        {
          title: 'QAOA answers for itself',
          body: 'Its portfolio is the best valid sample from its own final measurements. The classical methods run afterwards and cannot change it.',
        },
        {
          title: 'No silent substitution',
          body: 'If no measured sample follows every rule, the result says "no feasible sample" instead of borrowing a classical answer.',
          code: 'no_feasible_sample',
        },
        {
          title: 'Every answer is re-checked',
          body: 'Each portfolio is decoded and checked against every rule again, and one shared function scores all methods.',
        },
        {
          title: 'An honest gap',
          body: 'The gap always names its reference: the proven best (exact search) or only the best known answer. Time-limited solvers are labelled.',
          code: 'time_limited',
        },
        {
          title: 'Reproducible',
          body: 'Seeds, SHA-256 hashes of the data and settings, and library versions are stored. Reports are rendered from the stored result.',
        },
        {
          title: 'No hype',
          body: 'There is no claim of quantum advantage. Circuits run on a simulator, and every page says so.',
        },
      ],
    },
    {
      id: 'data',
      letter: 'C',
      title: 'Data integrity',
      short: 'No peeking at the future; gaps and fixes are disclosed.',
      intro: 'Prices are real NSE closes. They are cleaned in ways that cannot leak future information, and every correction is shown.',
      items: [
        {
          title: 'No look-ahead',
          body: 'Risk and return are estimated from the training window only. Later prices are used once, at the end, to test the picks.',
        },
        {
          title: 'Careful gap filling',
          body: 'A missing day is filled from the previous close for at most 5 days and never from the future. Stocks under 95 % coverage are dropped.',
        },
        {
          title: 'Stable risk estimates',
          body: 'Ledoit-Wolf shrinkage keeps the risk matrix well behaved, and any numerical fix is reported in the result.',
        },
        {
          title: 'Traceable data',
          body: 'Each dataset is hashed. Corporate-action corrections, and stale or synthetic data, are flagged on screen.',
        },
        {
          title: 'Short tests are labelled',
          body: 'Fewer than 20 days of later prices is marked "insufficient" rather than reported as a result.',
        },
        {
          title: 'Strict uploads',
          body: `CSV uploads must be UTF-8, up to ${L.upload}, 60 to 20,000 rows and up to 50 stocks with positive prices. Spreadsheet formulas are rejected.`,
          code: 'invalid_upload',
        },
      ],
    },
    {
      id: 'runtime',
      letter: 'D',
      title: 'Reliable runs',
      short: 'Runs are queued, time-limited, cancellable and isolated.',
      intro: 'One worker runs one job at a time in its own process, so a slow or broken run never takes the app down with it.',
      items: [
        {
          title: 'A bounded queue',
          body: `Up to ${L.queue} jobs wait in line. When the queue is full the server answers "try again shortly" instead of overloading.`,
          code: 'queue_full (429)',
        },
        {
          title: 'Time limits',
          body: `Quick runs stop after 90 s, Balanced after 5 min, Research after 20 min, and experiments after ${L.experiment}. A run that hits its limit is marked timed out.`,
        },
        {
          title: 'Cancel at any time',
          body: 'Cancelling stops the worker within a 3-second grace period.',
        },
        {
          title: 'Crashes stay contained',
          body: 'A crash marks only that run as failed; the app keeps working and the next run starts normally.',
          code: 'worker_crashed',
        },
        {
          title: 'Restarts and repeats',
          body: 'Runs cut off by a restart are marked as such. A repeated submission is recognised, and identical settings replay a stored result that is labelled as cached.',
          code: 'interrupted_by_restart · cached_from',
        },
        {
          title: 'Steady live progress',
          body: 'Progress streams at most 4 updates a second with a heartbeat, resumes after a dropped connection, and falls back to polling.',
        },
      ],
    },
    {
      id: 'security',
      letter: 'E',
      title: 'Security',
      short: 'Local only, strict inputs, secrets kept out of sight.',
      intro: 'QuantFolio runs on your own computer and accepts only small, well-formed requests.',
      items: [
        {
          title: 'Local only',
          body: 'The server listens on 127.0.0.1 with a host allow-list, CORS rules and strict security headers, including a content security policy.',
        },
        {
          title: 'Size limits',
          body: `Requests are limited to ${L.body} of JSON and ${L.upload} of CSV. Not-a-number and infinite values are rejected.`,
        },
        {
          title: 'No code, paths or links accepted',
          body: 'Inputs never take file paths, URLs or expressions, and run IDs must match a strict pattern.',
        },
        {
          title: 'Secrets stay secret',
          body: 'The IBM Quantum token is read from the environment only. It is never logged, stored in results or sent to the browser.',
        },
        {
          title: 'Clean errors',
          body: 'Every error has the same shape: a code and a plain message, with no stack traces.',
        },
      ],
    },
    {
      id: 'interface',
      letter: 'F',
      title: 'On screen',
      short: 'The interface never hides a problem.',
      intro: 'What you see always matches what happened, and nothing you typed is lost.',
      items: [
        { title: 'Run blocked on errors', body: 'The Run button stays disabled while the check shows an error, and says why.' },
        { title: 'Disclaimer everywhere', body: '"Not investment advice" appears on every page and in every report.' },
        { title: 'Data warnings', body: 'Amber banners flag synthetic, stale or uploaded data.' },
        { title: 'Honest labels', body: 'Cached replays and the recorded example are labelled as such.' },
        { title: 'Nothing lost', body: 'Going offline keeps your inputs. Failed runs offer Retry and Edit.' },
        { title: 'Cost shown first', body: 'Experiments show how many runs and how long before they start.' },
      ],
    },
  ]
}
