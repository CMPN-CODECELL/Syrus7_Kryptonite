import type { ConflictKind, Issue, PreflightReport } from '../../api/contract.gen'
import { Badge, Disclosure, ErrorState, IconAlert, IconCheck, IconInfo, IconX, Skeleton, Tooltip } from '../../components/ui'
import { formatDuration, formatInt, formatMemoryEstimate } from '../../lib/format'
import { GLOSSARY } from '../../lib/glossary'

const CONFLICT_LABEL: Record<ConflictKind, string> = {
  proven_infeasible: 'Proven infeasible',
  preflight_conflict: 'Settings conflict',
  resource_limit: 'Over a resource limit',
}

const SEVERITY = {
  error: { tone: 'danger', icon: <IconX size={14} />, label: 'Blocking' },
  warning: { tone: 'amber', icon: <IconAlert size={14} />, label: 'Warning' },
  info: { tone: 'cobalt', icon: <IconInfo size={14} />, label: 'Note' },
} as const

function Row({ label, value, hint, help }: { label: string; value: React.ReactNode; hint?: React.ReactNode; help?: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <dt className="text-sm text-muted">
        <span className="inline-flex items-center gap-1">
          {label}
          {help ? <Tooltip content={help} label={`What does "${label}" mean?`} /> : null}
        </span>
        {hint ? <span className="block text-xs text-faint">{hint}</span> : null}
      </dt>
      <dd className="text-right text-sm font-medium text-ink tabular">{value}</dd>
    </div>
  )
}

export function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null
  const order = { error: 0, warning: 1, info: 2 }
  return (
    <ul className="grid grid-cols-1 gap-2" aria-label="Preflight issues" data-testid="preflight-issues">
      {[...issues]
        .sort((a, b) => order[a.severity] - order[b.severity])
        .map((i, n) => {
          const s = SEVERITY[i.severity]
          return (
            <li
              key={`${i.code}-${n}`}
              className={`rounded-md border p-3 text-sm ${i.severity === 'error' ? 'border-danger-200 bg-danger-50' : i.severity === 'warning' ? 'border-amber-200 bg-amber-50' : 'border-line bg-mist'}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={s.tone} icon={s.icon}>
                  {s.label}
                </Badge>
                {i.constraints.map((c) => (
                  <span key={c} className="text-xs text-muted tabular">
                    {c}
                  </span>
                ))}
              </div>
              <p className="mt-1.5 text-ink">{i.message}</p>
              {i.suggestions.length ? (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-ink-2">
                  {i.suggestions.map((sg) => (
                    <li key={sg}>{sg}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          )
        })}
    </ul>
  )
}

/** Live resource and feasibility check from POST /api/preflight. Only server numbers are shown. */
export function PreflightPanel({
  report,
  pending,
  error,
  blockedByForm,
  onRetry,
  memoryLimitMb = null,
}: {
  report: PreflightReport | undefined
  pending: boolean
  error: unknown
  blockedByForm: number
  onRetry: () => void
  /** Server memory limit (capabilities), used to phrase estimates far above it. */
  memoryLimitMb?: number | null
}) {
  let status: React.ReactNode
  if (blockedByForm) status = <Badge tone="amber" icon={<IconAlert size={13} />}>Fix {blockedByForm} field{blockedByForm === 1 ? '' : 's'}</Badge>
  else if (pending && !report) status = <Badge tone="neutral">Checking…</Badge>
  else if (error && !report) status = <Badge tone="danger" icon={<IconX size={13} />}>Check failed</Badge>
  else if (report?.can_submit) status = <Badge tone="teal" icon={<IconCheck size={13} />}>Ready to run</Badge>
  else if (report) status = <Badge tone="danger" icon={<IconX size={13} />}>{report.conflict_kind ? CONFLICT_LABEL[report.conflict_kind] : 'Blocked'}</Badge>

  const r = report
  const qubitFrac = r ? Math.min(1, r.total_qubits / Math.max(1, r.max_qubits)) : 0
  return (
    <section aria-labelledby="pf-h" data-testid="preflight" aria-busy={pending || undefined}>
      <div className="flex items-center gap-2">
        <h2 id="pf-h" className="text-base font-semibold text-ink">
          Preflight
        </h2>
        <span className="ml-auto flex items-center gap-2" aria-live="polite">
          {pending && report ? <span className="text-xs text-muted">Updating…</span> : null}
          {status}
        </span>
      </div>
      <p className="mt-1 text-xs leading-relaxed text-muted">A check before anything runs: how big the quantum problem is and whether your rules can all be met.</p>
      {blockedByForm ? <p className="mt-2 text-sm text-muted">The preflight runs once the highlighted fields are valid.</p> : null}
      {error && !blockedByForm ? <ErrorState className="mt-3" error={error} title="Preflight could not run" onRetry={onRetry} /> : null}
      {!r && !error && !blockedByForm ? (
        <div className="mt-3 grid grid-cols-1 gap-2">
          <Skeleton className="h-5" />
          <Skeleton className="h-5" />
          <Skeleton className="h-5" />
        </div>
      ) : null}
      {r ? (
        <div className={`transition-opacity ${pending || blockedByForm ? 'opacity-60' : ''}`}>
          {r.issues.length ? (
            <div className="mt-3">
              <IssueList issues={r.issues} />
            </div>
          ) : null}
          <div className="mt-3">
            <div className="flex items-baseline justify-between text-sm">
              <span className="inline-flex items-center gap-1 text-muted">
                Total qubits
                <Tooltip content={GLOSSARY.qubits} label='What does "Total qubits" mean?' />
              </span>
              <span className="tabular">
                <span className="text-2xl font-semibold text-ink" data-testid="pf-qubits">
                  {r.total_qubits}
                </span>
                <span className="text-muted"> / {r.max_qubits} max</span>
              </span>
            </div>
            <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-mist-2" aria-hidden="true">
              <div className={`h-full rounded-full ${r.total_qubits > r.max_qubits ? 'bg-danger' : 'bg-cobalt'}`} style={{ width: `${qubitFrac * 100}%` }} />
            </div>
            <p className="mt-1 text-xs text-muted tabular">
              {r.decision_bits} decision + {r.slack_bits} slack bits
            </p>
          </div>
          <dl className="mt-3 divide-y divide-line border-y border-line">
            <Row
              label="Universe"
              value={`${r.requested_symbols.length} selected → ${r.encoded_symbols.length} encoded`}
              hint={r.screened_out.length ? `${r.screened_out.length} screened out (${r.screening_rule})` : undefined}
              help="Stocks you selected, and how many made it into the problem. A stock is left out only when its price history is too short for the dates chosen."
            />
            <Row label="Holdings K" value={r.portfolio_size ?? 'lots mode'} help={r.portfolio_size === null ? GLOSSARY.lots : GLOSSARY.cardinality} />
            <Row label="Circuit depth p" value={r.depth} help={GLOSSARY.depth} />
            <Row label="Shots per evaluation" value={formatInt(r.shots)} help={GLOSSARY.shots} />
            <Row label="Final batch shots" value={formatInt(r.final_shots)} help="After tuning, the circuit is measured this many times; the best valid portfolio among them is QAOA's answer." />
            <Row label="Evaluation budget" value={formatInt(r.evaluation_budget)} help={GLOSSARY.evaluations} />
            <Row label="Timeout" value={formatDuration(r.timeout_seconds)} help="The run stops after this long, keeping whatever it has found so far." />
            <Row
              label="Memory estimate"
              value={formatMemoryEstimate(r.memory_estimate_mb, memoryLimitMb)}
              help="Statevector simulator memory for the encoded qubits, plus overhead. Estimated, not measured."
            />
            <Row
              label="Runtime estimate"
              value={r.estimated_runtime_s !== null ? `≈ ${formatDuration(r.estimated_runtime_s)}` : 'not available'}
              help="A rough guess for the whole run from a simple timing model of the simulator. Real runs can be faster or slower."
            />
            {r.dataset_label ? <Row label="Dataset" value={r.dataset_label} help="Where the prices come from: daily closing prices downloaded from Yahoo Finance for NSE stocks." /> : null}
          </dl>
          {r.issues.length ? null : (
            <p className="mt-3 flex items-center gap-1.5 text-sm text-teal">
              <IconCheck size={15} /> No issues found.
            </p>
          )}
          {r.constraints.length ? (
            <Disclosure summary={`How constraints are encoded (${r.constraints.filter((c) => c.active).length} active)`} className="mt-2">
              <ul className="grid grid-cols-1 gap-2 text-sm">
                {r.constraints.map((c) => (
                  <li key={c.name} className="rounded-md border border-line p-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-ink">{c.name}</span>
                      <Badge tone={c.active ? 'teal' : 'neutral'}>{c.active ? 'active' : 'inactive'}</Badge>
                      <span className="text-xs text-muted">{c.kind}</span>
                      <span className="ml-auto text-xs text-muted tabular">
                        {c.slack_bits} slack bit{c.slack_bits === 1 ? '' : 's'}
                        {c.penalty_weight !== null ? ` · λ = ${c.penalty_weight.toPrecision(3)}` : ''}
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-relaxed text-muted">{c.explanation}</p>
                    {c.binding_note ? <p className="mt-1 text-xs text-amber">{c.binding_note}</p> : null}
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
