import { useState } from 'react'
import { Link } from 'react-router'
import { downloadUrl } from '../api/client'
import type { RunSummary } from '../api/contract.gen'
import { useRuns } from '../api/hooks'
import { Badge, Button, ButtonLink, Card, EmptyState, ErrorState, Field, IconArrowLeft, IconArrowRight, IconDatabase, LoadingBlock, Select, StatusChip, Table } from '../components/ui'
import { DASH, SOLVER_LABEL, STATUS_LABEL, formatDateTime, formatDuration, formatNum, formatRelative, shortId } from '../lib/format'

const PAGE = 20
const STATUSES = ['', 'queued', 'running', 'succeeded', 'failed', 'cancelled', 'timed_out'] as const

export default function History() {
  const [status, setStatus] = useState('')
  const [kind, setKind] = useState('')
  const [page, setPage] = useState(0)
  const q = useRuns({ status: status || undefined, kind: kind || undefined, limit: PAGE, offset: page * PAGE })

  const items = q.data?.items ?? []
  const total = q.data?.total ?? null
  const pages = total !== null ? Math.max(1, Math.ceil(total / PAGE)) : null

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Runs and reports</h1>
      <p className="mt-1 max-w-2xl text-sm text-muted sm:text-base">Every stored run, including failed ones.</p>

      <Card className="mt-5">
        <div className="flex flex-wrap items-end gap-4">
          <Field label="Status" className="w-full sm:w-48">
            {({ id }) => (
              <Select
                id={id}
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value)
                  setPage(0)
                }}
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s ? STATUS_LABEL[s] : 'Any status'}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Kind" className="w-full sm:w-60">
            {({ id }) => (
              <Select
                id={id}
                value={kind}
                onChange={(e) => {
                  setKind(e.target.value)
                  setPage(0)
                }}
              >
                <option value="">Runs and experiments</option>
                <option value="run">Runs</option>
                <option value="experiment">Experiments</option>
              </Select>
            )}
          </Field>
          <p className="text-sm text-muted tabular sm:ml-auto" aria-live="polite">
            {q.isFetching && !q.isPending ? 'Refreshing… ' : ''}
            {total !== null ? `${total} total` : ''}
          </p>
        </div>

        <div className="mt-4">
          {q.isPending ? (
            <LoadingBlock label="Loading runs" rows={6} />
          ) : q.isError ? (
            <ErrorState error={q.error} title="Could not load the run history" onRetry={() => void q.refetch()} />
          ) : !items.length ? (
            <EmptyState
              title={status || kind ? 'No runs match these filters' : 'No runs yet'}
              action={
                status || kind ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setStatus('')
                      setKind('')
                    }}
                  >
                    Clear filters
                  </Button>
                ) : (
                  <ButtonLink to="/lab" iconRight={<IconArrowRight />}>
                    Open the optimiser
                  </ButtonLink>
                )
              }
            >
              {status || kind ? 'Try another status or kind.' : 'Runs you submit from the Portfolio Lab or Experiment Lab appear here.'}
            </EmptyState>
          ) : (
            <RunTable rows={items} />
          )}
        </div>

        {pages !== null && pages > 1 ? (
          <nav className="mt-4 flex items-center justify-between gap-3" aria-label="Pages">
            <Button variant="secondary" size="sm" icon={<IconArrowLeft />} disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
              Newer
            </Button>
            <span className="text-sm text-muted tabular">
              Page {page + 1} of {pages}
            </span>
            <Button variant="secondary" size="sm" iconRight={<IconArrowRight />} disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>
              Older
            </Button>
          </nav>
        ) : null}
      </Card>
    </div>
  )
}

function RunTable({ rows }: { rows: RunSummary[] }) {
  return (
    <Table
      caption="Stored runs, newest first"
      captionHidden
      rows={rows}
      rowKey={(r) => r.run_id}
      columns={[
        {
          key: 'run',
          header: 'Run',
          rowHeader: true,
          className: 'min-w-44 whitespace-normal!',
          cell: (r) => (
            <span className="flex flex-col gap-0.5">
              <Link to={`/runs/${r.run_id}`} className="font-medium text-cobalt underline-offset-2 hover:underline" data-testid="history-open">
                {r.label ?? (r.kind === 'experiment' ? 'Experiment' : 'Run')} <span className="text-xs text-muted tabular" title={r.run_id}>{shortId(r.run_id)}</span>
              </Link>
              <span className="flex flex-wrap gap-1">
                {r.is_example ? <Badge tone="cobalt">Recorded example</Badge> : null}
                {r.cached_from ? (
                  <Badge tone="neutral" icon={<IconDatabase size={12} />} title={`Replay of ${r.cached_from}`}>
                    Cached replay
                  </Badge>
                ) : null}
                {r.kind === 'experiment' ? <Badge tone="neutral">Experiment</Badge> : null}
              </span>
            </span>
          ),
        },
        { key: 'st', header: 'Status', cell: (r) => <StatusChip status={r.status} /> },
        {
          key: 'q',
          header: 'QAOA',
          cell: (r) =>
            r.qaoa_status ? <StatusChip status={r.qaoa_status} label={`${SOLVER_LABEL.qaoa.split(' ')[0]}: ${STATUS_LABEL[r.qaoa_status] ?? r.qaoa_status}`} /> : r.error ? <span className="text-xs text-danger">{r.error.code}</span> : DASH,
        },
        { key: 'n', header: 'Assets', align: 'right', cell: (r) => r.n_symbols },
        { key: 'qb', header: 'Qubits', align: 'right', cell: (r) => r.total_qubits ?? DASH },
        { key: 'cfg', header: 'Circuit', className: 'whitespace-nowrap', cell: (r) => (r.depth ? `p = ${r.depth}${r.optimizer ? `, ${r.optimizer}` : ''}` : DASH) },
        { key: 'obj', header: 'Best objective', align: 'right', cell: (r) => formatNum(r.best_objective, 5) },
        {
          key: 'when',
          header: 'Submitted',
          cell: (r) => (
            <time dateTime={r.created_at} title={formatDateTime(r.created_at)} className="whitespace-nowrap">
              {formatRelative(r.created_at)}
            </time>
          ),
        },
        { key: 'dur', header: 'Duration', align: 'right', className: 'whitespace-nowrap', cell: (r) => (r.finished_at ? formatDuration((Date.parse(r.finished_at) - Date.parse(r.created_at)) / 1000) : DASH) },
        {
          key: 'dl',
          header: 'Reports',
          cell: (r) =>
            r.status === 'succeeded' ? (
              <span className="flex gap-2 text-xs whitespace-nowrap">
                <a className="text-cobalt underline" href={downloadUrl.html(r.run_id)} download>
                  HTML
                </a>
                <a className="text-cobalt underline" href={downloadUrl.json(r.run_id)} download>
                  JSON
                </a>
                <a className="text-cobalt underline" href={downloadUrl.csv(r.run_id)} download>
                  CSV
                </a>
              </span>
            ) : (
              DASH
            ),
        },
      ]}
    />
  )
}
