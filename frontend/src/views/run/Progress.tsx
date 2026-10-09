import { useState } from 'react'
import type { JobKind, JobStatus, Stage } from '../../api/contract.gen'
import type { RunStreamState } from '../../api/events'
import { errorMessage, useCancelRun } from '../../api/hooks'
import { ConvergenceChart } from '../../components/charts/charts'
import { Badge, Button, Card, CardHeader, Dialog, Disclosure, IconCheck, IconClock, IconRefresh, IconSpinner, IconStop, IconWifiOff, Notice } from '../../components/ui'
import { STAGE_LABEL, formatDuration, formatInt } from '../../lib/format'

const RUN_STAGES: Stage[] = ['queued', 'loading_data', 'estimating', 'screening', 'building_qubo', 'transpiling', 'optimising', 'final_sampling', 'baselines', 'frontier', 'noise', 'reporting', 'done']
const EXP_STAGES: Stage[] = ['queued', 'loading_data', 'estimating', 'experiment', 'noise', 'reporting', 'done']
const OPTIONAL = new Set<Stage>(['screening', 'noise'])

function seconds(a: string, b: string): number {
  return Math.max(0, (Date.parse(b) - Date.parse(a)) / 1000)
}

export function ConnectionBadge({ stream }: { stream: RunStreamState | null }) {
  if (!stream) return null
  const c = stream.connection
  if (c === 'offline') return <Badge tone="amber" icon={<IconWifiOff size={13} />}>Offline · will resume</Badge>
  if (c === 'reconnecting') return <Badge tone="amber" icon={<IconRefresh size={13} />}>Reconnecting…</Badge>
  if (c === 'connecting') return <Badge tone="neutral" icon={<IconSpinner size={13} />}>Connecting…</Badge>
  if (c === 'closed') return null
  return (
    <Badge
      tone="teal"
      icon={<span className="qf-pulse-dot inline-block h-1.5 w-1.5 rounded-full bg-teal" aria-hidden="true" />}
      title={stream.transport === 'polling' ? 'Live updates via polling (event stream unavailable)' : 'Live updates via server-sent events'}
    >
      {stream.transport === 'polling' ? 'Live (polling)' : 'Live'}
    </Badge>
  )
}

export function LiveProgress({ runId, kind, status, stream, noiseExpected }: { runId: string; kind: JobKind; status: JobStatus; stream: RunStreamState | null; noiseExpected?: boolean }) {
  const [confirm, setConfirm] = useState(false)
  const cancel = useCancelRun(runId)
  const entries = stream?.stages ?? []
  const seen = new Set(entries.map((s) => s.stage))
  const all = kind === 'experiment' ? EXP_STAGES : RUN_STAGES
  const p = status.progress
  const iterations = stream?.iterations ?? []
  // The status snapshot is polled and can trail the event stream; whichever is further along wins.
  const latest = entries[entries.length - 1]
  const streamAhead = latest !== undefined && all.indexOf(latest.stage) > all.indexOf(p.stage)
  const current = streamAhead ? latest.stage : p.stage
  const message = streamAhead ? latest.message : p.message
  const curIdx = all.indexOf(current)
  const stages = all.filter((s) => !OPTIONAL.has(s) || seen.has(s) || (s === 'noise' && noiseExpected && all.indexOf(s) > curIdx))
  const lastEval = kind === 'run' ? (iterations[iterations.length - 1]?.evaluation ?? 0) : 0
  const evaluationsDone = Math.min(Math.max(p.evaluations_done, lastEval), p.evaluation_budget || Infinity)

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      <Card>
        <CardHeader
          level={2}
          title={status.status === 'queued' ? 'Waiting in the queue' : 'Running'}
          description={
            status.status === 'queued'
              ? status.queue_position !== null
                ? `Position ${status.queue_position + 1} in the queue.`
                : 'Waiting for a worker.'
              : (message ?? STAGE_LABEL[current])
          }
        />
        <ol className="grid grid-cols-1 gap-0.5" aria-label="Pipeline stages" data-testid="stage-list">
          {stages.map((s) => {
            const idx = all.indexOf(s)
            const done = idx < curIdx || status.status === 'succeeded'
            const active = s === current && !done
            // A stage can log several messages; time it from its first entry to the first entry of a later stage.
            const first = entries.findIndex((e) => e.stage === s)
            const entry = first < 0 ? undefined : entries[first]
            const next = entry ? entries.slice(first + 1).find((e) => e.stage !== s) : undefined
            const took = entry && next && done ? seconds(entry.ts, next.ts) : null
            return (
              <li key={s} className={`flex min-h-9 items-center gap-2.5 rounded-md px-2 text-sm ${active ? 'bg-cobalt-50 font-medium text-ink' : done ? 'text-ink-2' : 'text-faint'}`} aria-current={active ? 'step' : undefined}>
                <span className="flex h-5 w-5 shrink-0 items-center justify-center" aria-hidden="true">
                  {done ? <IconCheck size={15} className="text-teal" /> : active ? <IconSpinner size={15} className="text-cobalt" /> : <span className="h-1.5 w-1.5 rounded-full bg-line-strong" />}
                </span>
                <span className="min-w-0 flex-1">{STAGE_LABEL[s] ?? s}</span>
                <span className="sr-only">{done ? 'done' : active ? 'in progress' : 'not started'}</span>
                {took !== null ? <span className="text-xs text-muted tabular">{took < 0.001 ? '< 1 ms' : formatDuration(took)}</span> : null}
              </li>
            )
          })}
        </ol>
        {p.evaluation_budget ? (
          <p className="mt-3 text-sm text-ink-2 tabular" data-testid="eval-count">
            <span className="font-semibold text-ink">{formatInt(evaluationsDone)}</span> of {formatInt(p.evaluation_budget)} objective evaluations
          </p>
        ) : null}
        {status.timeout_seconds ? (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted">
            <IconClock size={13} /> Stops after {formatDuration(status.timeout_seconds)} if not finished.
          </p>
        ) : null}
        <div className="mt-4 border-t border-line pt-4">
          {status.cancel_requested ? (
            <p className="flex items-center gap-2 text-sm text-ink-2" role="status">
              <IconSpinner size={15} /> Cancelling — the worker stops at its next checkpoint.
            </p>
          ) : (
            <Button variant="danger" icon={<IconStop />} onClick={() => setConfirm(true)} data-testid="cancel-run">
              Cancel run
            </Button>
          )}
          {cancel.error ? (
            <Notice tone="danger" className="mt-2" title="Cancel failed">
              {errorMessage(cancel.error)}
            </Notice>
          ) : null}
        </div>
        <Dialog
          open={confirm}
          onClose={() => setConfirm(false)}
          title="Cancel this run?"
          footer={
            <>
              <Button variant="secondary" onClick={() => setConfirm(false)}>
                Keep running
              </Button>
              <Button
                variant="danger"
                loading={cancel.isPending}
                data-testid="confirm-cancel"
                onClick={() => cancel.mutate(undefined, { onSettled: () => setConfirm(false) })}
              >
                Cancel run
              </Button>
            </>
          }
        >
          The worker stops at its next checkpoint. Partial results are not kept, and you can run the same settings again.
        </Dialog>
      </Card>

      <div className="grid grid-cols-1 min-w-0 gap-5">
        {kind === 'run' ? (
          <Card>
            <ConvergenceChart points={iterations} budget={p.evaluation_budget} live />
          </Card>
        ) : (
          <Card>
            <CardHeader level={2} title="Experiment in progress" description="Cells run one after another with the seeds and budgets shown in the configuration. Results appear when every cell has finished." />
            {p.evaluation_budget ? (
              <p className="text-sm text-ink-2 tabular">
                {formatInt(p.evaluations_done)} of {formatInt(p.evaluation_budget)} evaluations across cells
              </p>
            ) : null}
          </Card>
        )}
        {stream?.logs.length ? (
          <Card>
            <Disclosure summary={`Worker log (${stream.logs.length} lines)`}>
              <pre className="max-h-64 overflow-auto rounded-md bg-mist p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-2">{stream.logs.slice(-200).join('\n')}</pre>
            </Disclosure>
          </Card>
        ) : null}
      </div>
    </div>
  )
}
