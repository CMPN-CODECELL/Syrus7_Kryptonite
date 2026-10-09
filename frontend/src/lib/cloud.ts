// Cloud mode: the build that Firebase Hosting serves in front of the Cloud Run API.
//
// It is on only in a build made with `npx vite build --mode cloud`. Local development, the local server on :8000 and
// the e2e suite use the other modes, so they keep the event streams and make no extra requests. Neither mode has a
// sign-in: the public API limits new runs with a daily cap for the whole demo plus a cap per visitor.
// This file imports nothing so every layer can read the flag.

export const CLOUD = import.meta.env.MODE === 'cloud'

/** Where the circuits are simulated, for copy that would otherwise say "this computer". */
export const SIMULATOR_PLACE = CLOUD ? 'on our server' : 'on this computer'

function clock(iso: unknown): string | null {
  const t = typeof iso === 'string' ? Date.parse(iso) : NaN
  return Number.isFinite(t) ? new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null
}

/**
 * Plain wording for the cloud API's refusals, keyed by the error code in the envelope (status as a fallback).
 * Null keeps the server's own message.
 */
export function cloudErrorMessage(status: number, code: string, details: unknown, retryAfter: number | null): string | null {
  const d = (details && typeof details === 'object' ? details : {}) as Record<string, unknown>
  const resets = clock(d.resets_at)
  const until = resets ? ` The count resets at ${resets} (midnight UTC).` : ' The count resets at midnight UTC.'
  switch (code) {
    case 'daily_quota_exhausted':
      return `The public demo has used all ${d.limit ?? 20} live runs for today.${until} The prepared example and finished results stay available.`
    case 'user_quota_exhausted':
      return `You have reached your own limit${typeof d.limit === 'number' ? ` of ${d.limit}` : ''} for today.${until}`
    case 'user_active_limit':
      return 'You already have a run in progress. Wait for it to finish or cancel it, then start another.'
    case 'queue_full':
      return `The job queue is full.${retryAfter ? ` Try again in about ${retryAfter} s.` : ' Try again shortly.'}`
    case 'compute_paused':
      return `New runs are paused by the operator${typeof d.reason === 'string' && d.reason ? ` (${d.reason})` : ''}. The prepared example and finished results stay available.`
    default:
      break
  }
  if (code !== `http_${status}`) return null
  if (status === 403) return 'This is not available on the public demo.'
  if (status === 429) return `Too many requests.${retryAfter ? ` Try again in about ${retryAfter} s.` : ' Try again shortly.'}`
  // No JSON body from the API: the request died at the edge, most often while an idle server was starting.
  if (status === 502 || status === 503 || status === 504) return 'The QuantFolio API did not answer. It may be starting after being idle; try again in a few seconds.'
  return null
}
