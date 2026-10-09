import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { fieldErrors, isApiError } from '../../api/client'
import type { RunConfig, RunConfigInput } from '../../api/contract.gen'
import { errorMessage, useAssets, useCapabilities, useDataStatus, useOnline, usePreflight, usePresets, useSubmitJob } from '../../api/hooks'
import { HeroStage } from '../../components/hero/HeroStage'
import { Button, Card, CardHeader, ErrorState, IconArrowRight, LoadingBlock, Notice } from '../../components/ui'
import { useMediaQuery } from '../../lib/media'
import { rememberRunConfig } from '../../lib/storage'
import { validateRunConfig } from '../../lib/validate'
import { useLab } from '../../state/lab'
import { AdvancedPanel } from './Advanced'
import { AssetPicker } from './AssetPicker'
import { CostControls, DateControls, HoldingsControls, ObjectiveControls, SectorControls, TargetControls } from './Controls'
import { PreflightPanel } from './Preflight'
import { PresetPicker } from './Presets'

export default function LabView() {
  const presets = usePresets()
  const assets = useAssets()
  const caps = useCapabilities()
  const ds = useDataStatus()
  const lab = useLab()
  const { config, seed } = lab
  const wide = useMediaQuery('(min-width: 1024px)')

  useEffect(() => {
    if (presets.data) seed(presets.data.default_config)
  }, [presets.data, seed])

  if (presets.isPending || assets.isPending || (!config && !presets.isError))
    return (
      <Shell>
        <Card>
          <LoadingBlock label="Loading assets and presets" rows={6} />
        </Card>
      </Shell>
    )
  if (presets.isError || assets.isError || !config)
    return (
      <Shell>
        <ErrorState
          error={presets.error ?? assets.error}
          title="The Portfolio Lab needs the API"
          onRetry={() => {
            void presets.refetch()
            void assets.refetch()
          }}
        >
          Assets and presets come from the QuantFolio backend; nothing is shown until they load.
        </ErrorState>
      </Shell>
    )

  return <LabBody config={config} wide={wide} presetsData={presets.data} assetsData={assets.data} caps={caps.data} datasets={ds.data?.available ?? []} />
}

function Shell({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-[1320px] px-4 pt-8 sm:px-6">
      <div className="grid grid-cols-1 items-center gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <div>
          <p className="eyebrow">Portfolio Lab</p>
          <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">Build a constrained portfolio</h1>
          <p className="mt-2 max-w-2xl text-muted">
            Choose assets and constraints. The preflight shows how the problem maps to qubits before anything runs. QAOA and the classical baselines then solve exactly the same problem.
          </p>
        </div>
        {aside}
      </div>
      <div className="mt-6">{children}</div>
    </div>
  )
}

function LabBody({
  config,
  wide,
  presetsData,
  assetsData,
  caps,
  datasets,
}: {
  config: RunConfig
  wide: boolean
  presetsData: NonNullable<ReturnType<typeof usePresets>['data']>
  assetsData: NonNullable<ReturnType<typeof useAssets>['data']>
  caps: ReturnType<typeof useCapabilities>['data']
  datasets: NonNullable<ReturnType<typeof useDataStatus>['data']>['available']
}) {
  const { update, applyPreset, toggleSymbol, setSymbols, setHoverSymbol, hoverSymbol } = useLab()
  const navigate = useNavigate()
  const online = useOnline()
  const [forceFresh, setForceFresh] = useState(false)
  const submitGuard = useRef(false)

  const errors = useMemo(() => validateRunConfig(config), [config])
  const nErr = Object.keys(errors).length
  const preflight = usePreflight(nErr ? null : (config as RunConfigInput))
  const submit = useSubmitJob<RunConfigInput>('run')

  const report = preflight.data
  const fresh = !preflight.pending && !!report && !preflight.isError
  const canSubmit = online && nErr === 0 && fresh && report.can_submit && !submit.isPending
  const presetMax = presetsData.presets.find((p) => p.name === config.preset)?.max_assets ?? null
  const heroAssets = useMemo(() => assetsData.assets.map((a) => ({ symbol: a.symbol, sector: a.sector })), [assetsData])

  const onSubmit = () => {
    if (!canSubmit || submitGuard.current) return
    submitGuard.current = true
    const sent = structuredClone(config)
    submit.mutate(
      { config: sent as RunConfigInput, forceFresh },
      {
        onSuccess: ({ job, replayed }) => {
          rememberRunConfig(job.run_id, sent)
          navigate(`/runs/${job.run_id}`, { state: { replayed, submitted: true } })
        },
        onSettled: () => {
          submitGuard.current = false
        },
      },
    )
  }

  const submitError = submit.error
  const fe = fieldErrors(submitError)
  let blockedReason: string | null = null
  if (!online) blockedReason = 'You are offline.'
  else if (nErr) blockedReason = `Fix ${nErr} field${nErr === 1 ? '' : 's'} first.`
  else if (preflight.pending) blockedReason = 'Waiting for the preflight check…'
  else if (preflight.isError) blockedReason = 'The preflight check failed.'
  else if (report && !report.can_submit) blockedReason = 'The preflight found a blocking issue.'

  const submitBlock = (
    <div className="mt-4 border-t border-line pt-4">
      <Button
        size="lg"
        className="w-full"
        onClick={onSubmit}
        disabled={!canSubmit}
        loading={submit.isPending}
        iconRight={submit.isPending ? undefined : <IconArrowRight />}
        aria-describedby="submit-why"
        data-testid="submit-run"
      >
        {submit.isPending ? 'Submitting…' : 'Run optimisation'}
      </Button>
      <p id="submit-why" className="mt-2 min-h-5 text-xs text-muted" aria-live="polite">
        {submit.isPending ? 'Sending the run to the job queue.' : (blockedReason ?? 'QAOA on the simulator, then every enabled baseline. You can cancel at any time.')}
      </p>
      {submitError ? (
        <Notice tone="danger" title="The run was not started" className="mt-2">
          {isApiError(submitError) && submitError.status === 429
            ? `The job queue is full.${submitError.retryAfter ? ` Try again in about ${submitError.retryAfter} s.` : ' Try again shortly.'}`
            : errorMessage(submitError)}
          {fe.length ? (
            <ul className="mt-1 list-disc pl-5">
              {fe.map((f) => (
                <li key={f.loc}>
                  <span className="font-mono text-xs">{f.loc}</span>: {f.msg}
                </li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : null}
    </div>
  )

  return (
    <Shell
      aside={
        wide ? (
          <HeroStage
            compact
            className="h-60 w-full"
            assets={heroAssets}
            selected={config.symbols}
            hover={hoverSymbol}
            caption="Conceptual illustration: your selection joins the core. Not a circuit state."
          />
        ) : null
      }
    >
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="grid grid-cols-1 min-w-0 gap-5">
          <Card>
            <PresetPicker presets={presetsData.presets} value={config.preset} onPick={applyPreset} />
          </Card>
          <Card>
            <CardHeader
              level={2}
              title="Universe"
              description={`Today's NIFTY 50 constituents (${assetsData.dataset.universe_source}${assetsData.dataset.universe_as_of ? `, as of ${assetsData.dataset.universe_as_of}` : ''}). Every selected asset becomes at least one qubit.`}
            />
            <AssetPicker
              assets={assetsData.assets}
              selected={config.symbols}
              onToggle={toggleSymbol}
              onSet={setSymbols}
              onHover={setHoverSymbol}
              error={errors.symbols}
              presetMax={presetMax}
            />
            <p className="mt-2 text-xs text-muted">{assetsData.dataset.survivorship_note}</p>
          </Card>
          <ObjectiveControls config={config} update={update} errors={errors} />
          <HoldingsControls config={config} update={update} errors={errors} />
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <SectorControls config={config} update={update} errors={errors} assets={assetsData.assets} />
            <TargetControls config={config} update={update} errors={errors} />
          </div>
          <CostControls config={config} update={update} errors={errors} />
          <DateControls config={config} update={update} errors={errors} range={{ first: assetsData.dataset.first_date, last: assetsData.dataset.last_date }} />
          <AdvancedPanel config={config} update={update} errors={errors} caps={caps} datasets={datasets} forceFresh={forceFresh} setForceFresh={setForceFresh} />
        </div>
        <aside id="preflight" className="min-w-0 lg:sticky lg:top-20">
          {/* On wide screens the report scrolls inside the card so the submit button below it is always in view. */}
          <Card className="lg:flex lg:max-h-[calc(100dvh-6rem)] lg:flex-col">
            <div className="lg:-mx-1 lg:min-h-0 lg:flex-1 lg:overflow-y-auto lg:px-1">
              <PreflightPanel
                report={report}
                pending={preflight.pending}
                error={preflight.error}
                blockedByForm={nErr}
                onRetry={() => void preflight.refetch()}
                memoryLimitMb={typeof caps?.limits?.memory_limit_mb === 'number' ? caps.limits.memory_limit_mb : null}
              />
            </div>
            <div className="lg:shrink-0">{submitBlock}</div>
          </Card>
        </aside>
      </div>
      {!wide ? (
        <div className="sticky bottom-0 z-30 -mx-4 mt-6 border-t border-line bg-canvas/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="flex items-center gap-3">
            <p className="min-w-0 flex-1 text-sm text-ink-2 tabular" aria-hidden="true">
              {report ? (
                <>
                  <span className="font-semibold text-ink">{report.total_qubits}</span> qubits · {config.symbols.length} assets
                </>
              ) : (
                `${config.symbols.length} assets`
              )}
            </p>
            <a href="#preflight" className="inline-flex min-h-11 items-center rounded-md px-3 text-sm font-medium text-cobalt hover:bg-cobalt-50">
              Review preflight
            </a>
          </div>
        </div>
      ) : null}
    </Shell>
  )
}
