import { useEffect } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { isApiError, isExperimentResult, isTerminal } from '../../api/client'
import type { ExperimentConfig, ExperimentConfigInput, JobStatus, RunConfig, RunConfigInput } from '../../api/contract.gen'
import { errorMessage, useOnline, useRunConfig, useRunResult, useRunStatus, useRunStream, useSubmitJob } from '../../api/hooks'
import { Badge, Button, ButtonLink, Card, CopyButton, ErrorState, IconArrowLeft, IconClock, IconDatabase, IconRefresh, LoadingBlock, Notice, StatusChip } from '../../components/ui'
import { STATUS_LABEL, formatDateTime, formatDuration, shortId } from '../../lib/format'
import { recallRunConfig, rememberRunConfig } from '../../lib/storage'
import { useLab } from '../../state/lab'
import { ExperimentResultView } from './ExperimentResultView'
import { ConnectionBadge, LiveProgress } from './Progress'
import { RunResultView } from './RunResultView'

interface NavState {
  replayed?: boolean
  submitted?: boolean
}

export default function RunView() {
  const { runId = '' } = useParams()
  const location = useLocation()
  const nav = (location.state ?? {}) as NavState
  const statusQ = useRunStatus(runId)
  const stream = useRunStream(runId)
  const job: JobStatus | undefined = stream?.status ?? statusQ.data
  const done = job?.status === 'succeeded'
  const resultQ = useRunResult(runId, done)

  useEffect(() => {
    const label = job ? `${job.kind === 'experiment' ? 'Experiment' : 'Run'} ${shortId(runId)} · ${STATUS_LABEL[job.status] ?? job.status}` : `Run ${shortId(runId)}`
    document.title = `${label} · QuantFolio`
  }, [job, runId])

  if (!job) {
    if (statusQ.isError)
      return (
        <Page runId={runId}>
          <ErrorState
            error={statusQ.error}
            title={isApiError(statusQ.error) && statusQ.error.status === 404 ? 'No run with this id' : 'Could not load this run'}
            onRetry={() => void statusQ.refetch()}
          >
            {isApiError(statusQ.error) && statusQ.error.status === 404 ? 'It may have been removed when the server store was cleared. History lists every run the server still has.' : null}
          </ErrorState>
        </Page>
      )
    return (
      <Page runId={runId}>
        <Card>
          <LoadingBlock label="Loading run status" rows={4} />
        </Card>
      </Page>
    )
  }

  return (
    <Page runId={runId} job={job} aside={!isTerminal(job.status) ? <ConnectionBadge stream={stream} /> : null}>
      {nav.replayed && !job.cached_from ? (
        <Notice tone="info" className="mb-5" title="Same request as before">
          This click matched a submission the server had already accepted, so you are looking at that run rather than a duplicate.
        </Notice>
      ) : null}
      {job.cached_from ? (
        <Notice tone="info" icon={<IconDatabase size={16} />} className="mb-5" title="Cached replay">
          An identical configuration was already computed as run{' '}
          <Link className="text-cobalt underline tabular" to={`/runs/${job.cached_from}`} title={job.cached_from}>
            {shortId(job.cached_from)}
          </Link>
          {job.cached_original_time ? ` on ${formatDateTime(job.cached_original_time)}` : ''}. The result below is that stored result, not a new computation. Tick “Compute fresh” in the Lab’s advanced settings to recompute.
        </Notice>
      ) : null}

      {job.status === 'queued' || job.status === 'running' ? <LiveProgress runId={runId} kind={job.kind} status={job} stream={stream} noiseExpected={false} /> : null}

      {job.status === 'failed' || job.status === 'cancelled' || job.status === 'timed_out' ? <Stopped job={job} /> : null}

      {done ? (
        resultQ.data ? (
          isExperimentResult(resultQ.data) ? (
            <ExperimentResultView result={resultQ.data} />
          ) : (
            <RunResultView result={resultQ.data} actions={<EditInLab config={resultQ.data.config} />} />
          )
        ) : resultQ.isError ? (
          <ErrorState error={resultQ.error} title="The run finished but its result could not be loaded" onRetry={() => void resultQ.refetch()} />
        ) : (
          <Card>
            <LoadingBlock label="Loading result" rows={6} />
          </Card>
        )
      ) : null}
    </Page>
  )
}

function Page({ runId, job, aside, children }: { runId: string; job?: JobStatus; aside?: React.ReactNode; children: React.ReactNode }) {
  const kind = job?.kind === 'experiment' ? 'Experiment' : 'Run'
  return (
    <div className="mx-auto max-w-[1440px] px-4 pt-4 pb-4 sm:px-6 lg:px-8">
      <Link to="/history" className="inline-flex min-h-11 items-center gap-1.5 text-sm font-medium text-muted hover:text-ink pointer-fine:min-h-9">
        <IconArrowLeft size={15} /> History
      </Link>
      <div className={`mt-1 flex flex-wrap items-center gap-x-3 gap-y-2 ${job ? '' : 'mb-5'}`}>
        <h1 className="min-w-0 text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          {job?.label ?? kind}{' '}
          <span className="text-lg font-normal text-muted tabular sm:text-xl" title={runId}>
            {shortId(runId)}
          </span>
        </h1>
        {job ? <StatusChip status={job.status} /> : null}
        {job?.is_example ? <Badge tone="cobalt">Recorded example</Badge> : null}
        {job?.cached_from ? <Badge tone="neutral" icon={<IconDatabase size={13} />}>Cached replay</Badge> : null}
        <span className="ml-auto">{aside}</span>
      </div>
      {job ? (
        <p className="mt-1 mb-5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {/* The copy button sits by the full id: next to a balanced, wrapping title it drifted far from the short id. */}
          <span className="inline-flex items-center gap-1">
            Id <span className="select-all tabular">{runId}</span>
            <CopyButton value={runId} label="Copy full run id" className="-my-3" />
          </span>
          <span>Submitted {formatDateTime(job.created_at)}</span>
          {job.started_at ? <span>Started {formatDateTime(job.started_at)}</span> : null}
          {job.finished_at ? <span>Finished {formatDateTime(job.finished_at)}</span> : null}
          {job.started_at && job.finished_at ? <span>Wall time {formatDuration((Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000)}</span> : null}
        </p>
      ) : null}
      {children}
    </div>
  )
}

function EditInLab({ config }: { config: RunConfig }) {
  const lab = useLab()
  const navigate = useNavigate()
  return (
    <Button
      variant="secondary"
      size="sm"
      onClick={() => {
        lab.replace(config)
        navigate('/lab')
      }}
    >
      Open these settings in the Lab
    </Button>
  )
}

/** Failed, cancelled or timed-out: say why, then offer the same settings again. */
function Stopped({ job }: { job: JobStatus }) {
  const navigate = useNavigate()
  const lab = useLab()
  const online = useOnline()
  const submit = useSubmitJob<RunConfigInput | ExperimentConfigInput>(job.kind)
  const local = recallRunConfig<RunConfig | ExperimentConfig>(job.run_id)
  const remote = useRunConfig(job.run_id, !local)
  const saved = local ?? remote.data ?? null

  const retry = () => {
    if (!saved || submit.isPending) return
    submit.mutate(
      { config: saved as RunConfigInput | ExperimentConfigInput },
      {
        onSuccess: ({ job: next, replayed }) => {
          rememberRunConfig(next.run_id, saved)
          navigate(`/runs/${next.run_id}`, { state: { replayed, submitted: true } })
        },
      },
    )
  }

  const title = job.status === 'timed_out' ? 'The run hit its time limit' : job.status === 'cancelled' ? 'The run was cancelled' : 'The run failed'
  const body =
    job.status === 'timed_out'
      ? `It stopped after ${formatDuration(job.timeout_seconds)}. Reduce the evaluation budget, depth or number of assets, or raise the timeout in the advanced settings.`
      : job.status === 'cancelled'
        ? 'It was stopped at your request before finishing. Nothing was saved.'
        : (job.error?.message ?? 'The worker reported an error.')

  return (
    <Card data-testid="run-stopped">
      <Notice tone={job.status === 'failed' ? 'danger' : job.status === 'timed_out' ? 'warning' : 'info'} icon={job.status === 'timed_out' ? <IconClock size={16} /> : undefined} title={title}>
        {body}
        {job.error?.code ? <span className="mt-1 block text-xs tabular">code: {job.error.code}</span> : null}
      </Notice>
      <div className="mt-4 flex flex-wrap gap-2">
        {saved ? (
          <Button icon={<IconRefresh />} onClick={retry} loading={submit.isPending} disabled={!online} data-testid="retry-run">
            Run the same settings again
          </Button>
        ) : null}
        {saved && job.kind === 'run' ? (
          <Button
            variant="secondary"
            onClick={() => {
              lab.replace(saved as RunConfig)
              navigate('/lab')
            }}
          >
            Edit settings in the Lab
          </Button>
        ) : (
          <ButtonLink variant="secondary" to={job.kind === 'experiment' ? '/experiments' : '/lab'}>
            {job.kind === 'experiment' ? 'Open the Experiment Lab' : 'Open the Lab'}
          </ButtonLink>
        )}
      </div>
      {!saved && remote.isPending ? <p className="mt-2 text-xs text-muted">Loading the settings this run was submitted with…</p> : null}
      {!saved && remote.isError ? <p className="mt-2 text-xs text-muted">The server could not return the settings for this run ({errorMessage(remote.error)}), so it cannot be resubmitted from here.</p> : null}
      {submit.error ? (
        <Notice tone="danger" className="mt-3" title="Could not resubmit">
          {errorMessage(submit.error)}
        </Notice>
      ) : null}
    </Card>
  )
}
