import type { ReactNode } from 'react'
import { downloadUrl } from '../../api/client'
import type { DatasetInfo, PeriodMetrics } from '../../api/contract.gen'
import { AnchorButton, Badge, Card, CardHeader, Disclosure, IconAlert, IconDownload, Notice, Tooltip } from '../../components/ui'
import { DASH, formatDate, formatDateTime, formatInt, formatNum, formatPct } from '../../lib/format'
import { formatUnknown, humanKey, numericEntries } from '../../lib/result'

export function Downloads({ runId, counts = true }: { runId: string; counts?: boolean }) {
  const items: Array<[string, string, string]> = [
    ['HTML report', downloadUrl.html(runId), 'Self-contained report for sharing'],
    ['JSON', downloadUrl.json(runId), 'Full result, machine readable'],
    ['CSV', downloadUrl.csv(runId), 'Holdings and solver comparison'],
  ]
  if (counts) items.push(['Counts CSV', downloadUrl.counts(runId), 'Raw final-batch counts'])
  return (
    <nav aria-label="Downloads" className="flex flex-wrap gap-2" data-testid="downloads">
      {items.map(([label, href, title]) => (
        <AnchorButton key={label} href={href} download size="sm" icon={<IconDownload size={15} />} title={title}>
          {label}
        </AnchorButton>
      ))}
    </nav>
  )
}

export function KV({ rows, className = '' }: { rows: Array<[ReactNode, ReactNode]>; className?: string }) {
  return (
    <dl className={`grid grid-cols-1 gap-x-4 gap-y-0 text-sm sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)] ${className}`}>
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="pt-2 text-muted sm:border-t sm:border-line sm:py-2">{k}</dt>
          <dd className="min-w-0 border-b border-line pb-2 break-words text-ink sm:border-t sm:border-b-0 sm:py-2">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

/** A literal value such as an id, a hash, a bitstring or a version: even-width digits, and long values may wrap anywhere. */
export function Literal({ children }: { children: ReactNode }) {
  return <span className="break-all tabular">{children}</span>
}

export function DatasetCard({ dataset }: { dataset: DatasetInfo }) {
  return (
    <Card>
      <CardHeader
        level={2}
        title="Data"
        aside={
          <Badge tone={dataset.stale || dataset.label === 'synthetic test' ? 'amber' : 'neutral'} icon={dataset.stale ? <IconAlert size={13} /> : undefined}>
            {dataset.label}
            {dataset.stale ? ' (stale)' : ''}
          </Badge>
        }
      />
      <KV
        rows={[
          ['Source', dataset.source],
          ['Period', `${formatDate(dataset.first_date)} to ${formatDate(dataset.last_date)}`],
          ['Symbols', dataset.n_symbols],
          ['Downloaded', dataset.downloaded_at ? formatDateTime(dataset.downloaded_at) : DASH],
          ['Universe', `${dataset.universe_source}${dataset.universe_as_of ? ` (as of ${dataset.universe_as_of})` : ''}`],
          ['Missing data', dataset.missing_data_policy],
          ['Dataset id', <Literal key="id">{dataset.dataset_id}</Literal>],
          ['SHA-256', <Literal key="sha">{dataset.sha256}</Literal>],
        ]}
      />
      {dataset.stale_reason ? (
        <Notice tone="warning" className="mt-3" title="Stale data">
          {dataset.stale_reason}
        </Notice>
      ) : null}
      {dataset.issues.length ? (
        <Disclosure summary={`Data issues (${dataset.issues.length})`} className="mt-2">
          <ul className="list-disc space-y-1 pl-5 text-sm text-ink-2">
            {dataset.issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
      <p className="mt-3 text-xs leading-relaxed text-muted">{dataset.survivorship_note}</p>
    </Card>
  )
}

export function SoftwareCard({ software, timings, extra }: { software: Record<string, string>; timings: Record<string, number>; extra: Array<[ReactNode, ReactNode]> }) {
  const t = numericEntries(timings)
  const total = t.reduce((a, [, v]) => a + v, 0)
  return (
    <Card>
      <CardHeader level={2} title="Provenance" />
      <KV rows={[...extra, ...Object.entries(software).map(([k, v]): [ReactNode, ReactNode] => [humanKey(k), <Literal key={k}>{v}</Literal>])]} />
      {t.length ? (
        <>
          <h3 className="mt-5 text-sm font-semibold text-ink">Wall-clock timings</h3>
          <ul className="mt-2 grid grid-cols-1 gap-1.5" aria-label="Runtime breakdown">
            {t.map(([k, v]) => (
              <li key={k} className="grid grid-cols-[minmax(0,10rem)_minmax(0,1fr)_4.5rem] items-center gap-3 text-sm">
                <span className="leading-snug break-words text-ink-2">{humanKey(k)}</span>
                <span className="h-2 overflow-hidden rounded-full bg-mist-2" aria-hidden="true">
                  <span className="block h-full rounded-full bg-cobalt/70" style={{ width: `${total ? (v / Math.max(...t.map((x) => x[1]))) * 100 : 0}%` }} />
                </span>
                <span className="text-right text-ink tabular">{v < 1 ? `${(v * 1000).toFixed(0)} ms` : `${v.toFixed(2)} s`}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Card>
  )
}

export function ConfigDump({ config }: { config: unknown }) {
  return (
    <Disclosure summary="Exact configuration (JSON)">
      <pre className="max-h-96 overflow-auto rounded-md bg-mist p-3 font-mono text-xs leading-relaxed text-ink-2">{JSON.stringify(config, null, 2)}</pre>
    </Disclosure>
  )
}

export function DictTable({ dict, caption }: { dict: Record<string, unknown>; caption: string }) {
  const rows = Object.entries(dict)
  if (!rows.length) return null
  return <KV rows={rows.map(([k, v]): [ReactNode, ReactNode] => [humanKey(k), typeof v === 'object' && v !== null ? <Literal key={k}>{formatUnknown(v)}</Literal> : formatUnknown(v)])} className="mt-1" aria-label={caption} />
}

/** Validation / held-out test window metrics. Insufficient windows say so instead of showing numbers. */
export function PeriodBlock({ title, m, help }: { title: string; m: PeriodMetrics | null; help?: ReactNode }) {
  const tip = help ? <Tooltip content={help} label={`What is the ${title.toLowerCase()}?`} /> : null
  if (!m)
    return (
      <div>
        <h3 className="text-sm font-semibold text-ink">
          {title} {tip}
        </h3>
        <p className="mt-1 text-sm text-muted">Not evaluated.</p>
      </div>
    )
  return (
    <div>
      <h3 className="text-sm font-semibold text-ink">
        {title} {tip}{' '}
        <span className="font-normal text-muted">
          {formatDate(m.start)} to {formatDate(m.end)} · {formatInt(m.n_obs)} days
        </span>
      </h3>
      {m.insufficient ? (
        <p className="mt-1 text-sm text-amber">{m.note ?? 'Too few observations to report this window.'}</p>
      ) : (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
          <PeriodItem label="Total return" value={formatPct(m.total_return, 2, { sign: true })} loss={(m.total_return ?? 0) < 0} />
          <PeriodItem label="After costs" value={formatPct(m.net_total_return, 2, { sign: true })} loss={(m.net_total_return ?? 0) < 0} />
          <PeriodItem label="Annualised return" value={formatPct(m.annualised_return, 2, { sign: true })} loss={(m.annualised_return ?? 0) < 0} />
          <PeriodItem label="Annualised volatility" value={formatPct(m.annualised_volatility, 2)} />
          <PeriodItem label="Max drawdown" value={formatPct(m.max_drawdown, 2)} loss={(m.max_drawdown ?? 0) < 0} />
          <PeriodItem label="Sharpe" value={formatNum(m.sharpe, 2)} />
        </dl>
      )}
      {m.note && !m.insufficient ? <p className="mt-1 text-xs text-muted">{m.note}</p> : null}
    </div>
  )
}

function PeriodItem({ label, value, loss }: { label: string; value: string; loss?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`font-medium tabular ${loss ? 'text-danger' : 'text-ink'}`}>{value}</dd>
    </div>
  )
}
