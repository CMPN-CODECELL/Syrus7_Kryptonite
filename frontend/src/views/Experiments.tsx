import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { fieldErrors } from '../api/client'
import type { ExperimentConfigInput, InitStrategy, Optimizer, RunConfigInput } from '../api/contract.gen'
import { errorMessage, useCapabilities, useOnline, usePreflight, usePresets, useSubmitJob } from '../api/hooks'
import { Button, Card, CardHeader, Checkbox, ErrorState, Field, IconArrowRight, LoadingBlock, Metric, Notice, NumberInput, Segmented, TextInput } from '../components/ui'
import { formatDuration, formatInt } from '../lib/format'
import { GLOSSARY } from '../lib/glossary'
import { rememberRunConfig } from '../lib/storage'
import { validateRunConfig } from '../lib/validate'
import { useLab } from '../state/lab'
import { cellCount, parseIntList, type Design } from '../lib/experiment'

function toggle<T>(list: T[], v: T): T[] {
  return list.includes(v) ? list.filter((x) => x !== v) : [...list, v]
}

export default function Experiments() {
  const presets = usePresets()
  const caps = useCapabilities()
  const { config, seed } = useLab()
  useEffect(() => {
    if (presets.data) seed(presets.data.default_config)
  }, [presets.data, seed])

  return (
    <div className="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <h1 className="text-2xl font-semibold tracking-tight text-ink sm:text-3xl">Study what changes the answer</h1>
      <p className="mt-2 max-w-2xl text-muted">
        Sweep circuit depth, optimiser, initialisation and universe size on the Lab’s current problem. Every cell is a full QAOA run with a visible seed and budget, scored against the exact optimum where it can be computed.
      </p>
      <div className="mt-6">
        {presets.isError ? (
          <ErrorState error={presets.error} title="The Experiment Lab needs the API" onRetry={() => void presets.refetch()} />
        ) : !config ? (
          <Card>
            <LoadingBlock label="Loading the base configuration" rows={5} />
          </Card>
        ) : (
          <ExperimentForm base={config} caps={caps.data} />
        )}
      </div>
    </div>
  )
}

function ExperimentForm({ base, caps }: { base: NonNullable<ReturnType<typeof useLab>['config']>; caps: ReturnType<typeof useCapabilities>['data'] }) {
  const navigate = useNavigate()
  const online = useOnline()
  const submit = useSubmitJob<ExperimentConfigInput>('experiment')
  const guard = useRef(false)
  const [depths, setDepths] = useState<number[]>([1, 2, 3])
  const [optimizers, setOptimizers] = useState<Optimizer[]>([base.qaoa.optimizer])
  const [inits, setInits] = useState<InitStrategy[]>(['random', 'warm_interp'])
  const [sizesText, setSizesText] = useState('')
  const [seedsText, setSeedsText] = useState('1, 2, 3')
  const [design, setDesign] = useState<Design>('one_factor_at_a_time')
  const [maxCells, setMaxCells] = useState<number | null>(60)

  const baseErrors = useMemo(() => validateRunConfig(base), [base])
  const nBase = Object.keys(baseErrors).length
  const preflight = usePreflight(nBase ? null : (base as RunConfigInput))
  const sizes = parseIntList(sizesText)
  const seeds = parseIntList(seedsText)

  const errors: Record<string, string> = {}
  if (!depths.length && !optimizers.length && !inits.length && !sizes.values.length) errors.factors = 'Choose at least one factor level to sweep.'
  if (sizes.bad.length) errors.sizes = `Not whole numbers: ${sizes.bad.join(', ')}`
  else if (sizes.values.some((n) => n < 2 || n > base.symbols.length)) errors.sizes = `Each size must be between 2 and the ${base.symbols.length} assets selected in the Lab.`
  if (seeds.bad.length) errors.seeds = `Not whole numbers: ${seeds.bad.join(', ')}`
  else if (!seeds.values.length) errors.seeds = 'Give at least one seed.'
  else if (seeds.values.length > 20) errors.seeds = 'At most 20 seeds.'
  if (maxCells === null || maxCells < 1 || maxCells > 2000) errors.maxCells = 'Between 1 and 2000.'

  const sweep = { depths, optimizers, init_strategies: inits, universe_sizes: sizes.values, seeds: seeds.values, design }
  const cells = cellCount(sweep)
  if (maxCells !== null && cells > maxCells) errors.cells = `This sweep needs ${cells} cells but the limit is ${maxCells}. Remove levels or seeds, or raise the limit.`
  const perRun = preflight.data?.estimated_runtime_s ?? null
  const evals = cells * base.qaoa.max_evaluations
  const nErr = Object.keys(errors).length
  const canSubmit = online && !nErr && !nBase && !submit.isPending

  const onSubmit = () => {
    if (!canSubmit || guard.current) return
    guard.current = true
    const cfg: ExperimentConfigInput = { base: structuredClone(base) as RunConfigInput, ...sweep, max_cells: maxCells ?? 60 }
    submit.mutate(
      { config: cfg },
      {
        onSuccess: ({ job, replayed }) => {
          rememberRunConfig(job.run_id, cfg)
          navigate(`/runs/${job.run_id}`, { state: { replayed, submitted: true } })
        },
        onSettled: () => {
          guard.current = false
        },
      },
    )
  }

  const fe = fieldErrors(submit.error)
  return (
    <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="grid grid-cols-1 min-w-0 gap-5">
        <Card>
          <CardHeader
            level={2}
            title="Base problem"
            description={
              <>
                Taken from the Portfolio Lab. <Link to="/lab" className="font-medium text-cobalt underline">Change it there</Link>.
              </>
            }
          />
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric label="Stocks" value={base.symbols.length} help="How many stocks the base problem chooses from." />
            <Metric
              label="Mode"
              value={base.mode === 'lots' ? 'Lots' : `K = ${base.constraints.cardinality ?? '?'}`}
              help={base.mode === 'lots' ? GLOSSARY.lots : GLOSSARY.cardinality}
            />
            <Metric label="Evaluations per cell" value={formatInt(base.qaoa.max_evaluations)} help={GLOSSARY.evaluations} />
            <Metric label="Shots per evaluation" value={formatInt(base.qaoa.shots)} help={GLOSSARY.shots} />
          </dl>
          {nBase ? (
            <Notice tone="warning" className="mt-3" title="The base problem has invalid settings">
              Fix {nBase} field{nBase === 1 ? '' : 's'} in the <Link to="/lab" className="underline">Lab</Link> first.
            </Notice>
          ) : null}
        </Card>

        <Card>
          <CardHeader level={2} title="Factors" description="One factor at a time varies each list while the others stay at the base value. Full factorial runs every combination." />
          <Segmented
            name="design"
            legend="Design"
            value={design}
            onChange={setDesign}
            options={[
              { value: 'one_factor_at_a_time', label: 'One factor at a time' },
              { value: 'full_factorial', label: 'Full factorial' },
            ]}
          />
          <div className="mt-5 grid grid-cols-1 gap-5 md:grid-cols-3">
            <fieldset>
              <legend className="text-sm font-medium text-ink">Depth p</legend>
              <div className="mt-2 grid grid-cols-1 gap-1">
                {(caps?.depths ?? [1, 2, 3, 4, 5]).map((p) => (
                  <Checkbox key={p} checked={depths.includes(p)} onChange={() => setDepths((d) => toggle(d, p).sort())} label={`p = ${p}`} />
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="text-sm font-medium text-ink">Optimiser</legend>
              <div className="mt-2 grid grid-cols-1 gap-1">
                {(caps?.optimizers ?? (['COBYLA', 'SPSA'] as Optimizer[])).map((o) => (
                  <Checkbox key={o} checked={optimizers.includes(o)} onChange={() => setOptimizers((d) => toggle(d, o))} label={o} />
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="text-sm font-medium text-ink">Initialisation</legend>
              <div className="mt-2 grid grid-cols-1 gap-1">
                {(Object.keys(caps?.init_strategies ?? { random: '', warm_interp: '' }) as InitStrategy[]).map((s) => (
                  <Checkbox key={s} checked={inits.includes(s)} onChange={() => setInits((d) => toggle(d, s))} label={s === 'random' ? 'Random (seeded)' : s === 'warm_interp' ? 'Warm start (interpolated)' : s} />
                ))}
              </div>
            </fieldset>
          </div>
          {errors.factors ? <p className="mt-3 text-sm text-danger">{errors.factors}</p> : null}
          <div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-3">
            <Field label="Universe sizes (optional)" hint="Encoded asset counts, e.g. 4, 6, 8" error={errors.sizes}>
              {({ id, describedBy, invalid }) => <TextInput id={id} value={sizesText} onChange={(e) => setSizesText(e.target.value)} aria-describedby={describedBy} aria-invalid={invalid} inputMode="numeric" />}
            </Field>
            <Field label="Seeds" hint="Each cell runs once per seed (max 20)" error={errors.seeds}>
              {({ id, describedBy, invalid }) => <TextInput id={id} value={seedsText} onChange={(e) => setSeedsText(e.target.value)} aria-describedby={describedBy} aria-invalid={invalid} inputMode="numeric" />}
            </Field>
            <Field label="Cell limit" hint="The server refuses sweeps above this" error={errors.maxCells}>
              {({ id, describedBy, invalid }) => <NumberInput id={id} value={maxCells} onCommit={setMaxCells} allowEmpty aria-describedby={describedBy} aria-invalid={invalid} />}
            </Field>
          </div>
        </Card>
      </div>

      <aside className="min-w-0 xl:sticky xl:top-6">
        <Card>
          <h2 className="text-base font-semibold text-ink">Cost before you run</h2>
          <dl className="mt-3 grid grid-cols-2 gap-4">
            <Metric
              label="Cells"
              value={<span data-testid="exp-cells">{formatInt(cells)}</span>}
              hint={`${formatInt(seeds.values.length)} seed${seeds.values.length === 1 ? '' : 's'} each`}
              help="One cell is one full run with one combination of the settings you sweep. Every combination is run once per seed."
            />
            <Metric label="Evaluation budget" value={formatInt(evals)} hint="cells × evaluations per cell" help={GLOSSARY.evaluations} />
            <Metric
              label="Runtime estimate"
              value={perRun !== null ? `≈ ${formatDuration(perRun * cells)}` : 'not available'}
              hint={perRun !== null ? `${formatDuration(perRun)} per base run (preflight) × cells; varies with depth and size` : 'The server has no runtime estimate for this problem yet'}
            />
            <Metric label="Qubits (base)" value={preflight.data ? preflight.data.total_qubits : '…'} help={GLOSSARY.qubits} />
          </dl>
          {errors.cells ? (
            <Notice tone="danger" className="mt-3">
              {errors.cells}
            </Notice>
          ) : null}
          <Button size="lg" className="mt-4 w-full" onClick={onSubmit} disabled={!canSubmit} loading={submit.isPending} iconRight={<IconArrowRight />} data-testid="submit-experiment">
            {submit.isPending ? 'Submitting…' : 'Run experiment'}
          </Button>
          <p className="mt-2 text-xs text-muted" aria-live="polite">
            {!online ? 'You are offline.' : nErr || nBase ? 'Fix the highlighted settings first.' : 'Cells run one after another on the job queue. You can cancel at any time.'}
          </p>
          {submit.error ? (
            <Notice tone="danger" className="mt-3" title="The experiment was not started">
              {errorMessage(submit.error)}
              {fe.length ? (
                <ul className="mt-1 list-disc pl-5">
                  {fe.map((f) => (
                    <li key={f.loc}>
                      <span className="font-semibold">{f.loc}</span>: {f.msg}
                    </li>
                  ))}
                </ul>
              ) : null}
            </Notice>
          ) : null}
        </Card>
      </aside>
    </div>
  )
}
