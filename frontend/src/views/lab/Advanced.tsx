import type { Capabilities, DatasetInfo, RunConfig } from '../../api/contract.gen'
import { Checkbox, Disclosure, Field, NumberInput, Segmented, Select, Toggle } from '../../components/ui'
import { formatDate } from '../../lib/format'
import { markCustom } from '../../state/lab'
import type { FieldErrors } from '../../lib/validate'

type Update = (fn: (d: RunConfig) => void) => void

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="min-w-0 border-t border-line pt-4 first:border-t-0 first:pt-0">
      <legend className="text-sm font-semibold text-ink">{title}</legend>
      <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>
    </fieldset>
  )
}

export function AdvancedPanel({
  config,
  update,
  errors,
  caps,
  datasets,
  forceFresh,
  setForceFresh,
}: {
  config: RunConfig
  update: Update
  errors: FieldErrors
  caps: Capabilities | undefined
  datasets: DatasetInfo[]
  forceFresh: boolean
  setForceFresh: (v: boolean) => void
}) {
  const q = config.qaoa
  const b = config.baselines
  // Edits to QAOA / baseline / timeout settings leave the preset's values, so the run becomes "custom".
  const upd: Update = (fn) =>
    update((d) => {
      fn(d)
      markCustom(d)
    })
  const num = (label: string, key: string, value: number | null, set: (d: RunConfig, v: number) => void, hint?: string, suffix?: string) => (
    <Field label={label} hint={hint} error={errors[key]}>
      {({ id, describedBy, invalid }) => (
        <NumberInput id={id} value={value} suffix={suffix} onCommit={(v) => upd((d) => set(d, v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
      )}
    </Field>
  )
  const initDesc = caps?.init_strategies?.[q.init_strategy]

  return (
    <Disclosure summary="Advanced settings" className="rounded-lg border border-line bg-canvas px-4 shadow-card sm:px-5" aside={<span className="hidden text-xs text-muted sm:inline">QAOA, noise, screening, baselines, limits</span>}>
      <div className="grid grid-cols-1 gap-6 pb-5">
        <Group title="QAOA">
          <Field label="Depth p" error={errors['qaoa.depth']} hint="Layers of cost + mixer unitaries; parameters = 2p">
            {({ id, describedBy }) => (
              <Select id={id} value={q.depth} onChange={(e) => upd((d) => void (d.qaoa.depth = Number(e.target.value)))} aria-describedby={describedBy}>
                {(caps?.depths ?? [1, 2, 3, 4, 5]).map((p) => (
                  <option key={p} value={p}>
                    p = {p}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Classical optimiser">
            {({ id }) => (
              <Select id={id} value={q.optimizer} onChange={(e) => upd((d) => void (d.qaoa.optimizer = e.target.value as RunConfig['qaoa']['optimizer']))}>
                {(caps?.optimizers ?? ['COBYLA', 'SPSA']).map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Initial parameters" hint={initDesc}>
            {({ id, describedBy }) => (
              <Select id={id} value={q.init_strategy} onChange={(e) => upd((d) => void (d.qaoa.init_strategy = e.target.value as RunConfig['qaoa']['init_strategy']))} aria-describedby={describedBy}>
                {Object.keys(caps?.init_strategies ?? { random: '', warm_interp: '' }).map((s) => (
                  <option key={s} value={s}>
                    {s === 'random' ? 'Random (seeded)' : s === 'warm_interp' ? 'Warm start (interpolated)' : s}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          {num('Evaluation budget', 'qaoa.max_evaluations', q.max_evaluations, (d, v) => (d.qaoa.max_evaluations = v), 'Objective evaluations for the optimiser')}
          {num('Shots per evaluation', 'qaoa.shots', q.shots, (d, v) => (d.qaoa.shots = v))}
          {num('Final batch shots', 'qaoa.final_shots', q.final_shots, (d, v) => (d.qaoa.final_shots = v), 'Declared sampling batch decoded into the answer')}
          <Segmented
            name="estimator"
            legend="Energy estimator"
            size="sm"
            value={q.estimator}
            onChange={(v) => upd((d) => void (d.qaoa.estimator = v))}
            options={[
              { value: 'mean', label: 'Mean' },
              { value: 'cvar', label: 'CVaR' },
            ]}
          />
          {q.estimator === 'cvar' ? num('CVaR α', 'qaoa.cvar_alpha', q.cvar_alpha, (d, v) => (d.qaoa.cvar_alpha = v), 'Fraction of best samples averaged, (0, 1]') : null}
          {num('Penalty scale', 'qaoa.penalty_scale', q.penalty_scale, (d, v) => (d.qaoa.penalty_scale = v), 'Multiplier on derived penalty weights')}
          {num('Seed', 'qaoa.seed', q.seed, (d, v) => (d.qaoa.seed = v), 'Fixes initial parameters and sampling')}
        </Group>

        <Group title="Noise">
          <Segmented
            name="noise"
            legend="Simulator"
            size="sm"
            value={q.noise}
            onChange={(v) =>
              upd((d) => {
                d.qaoa.noise = v
                if (v === 'fake_backend' && !d.qaoa.fake_backend) d.qaoa.fake_backend = caps?.fake_backends[0] ?? null
              })
            }
            options={[
              { value: 'ideal', label: 'Ideal' },
              { value: 'fake_backend', label: 'Noisy (fake backend)', disabled: !caps?.fake_backends.length },
            ]}
          />
          {q.noise === 'fake_backend' || config.noise_comparison ? (
            <Field label="Fake backend" error={errors['qaoa.fake_backend']} hint={caps ? `Noisy runs are limited to ${caps.max_qubits_noise} qubits` : undefined}>
              {({ id, describedBy, invalid }) => (
                <Select id={id} value={q.fake_backend ?? ''} onChange={(e) => upd((d) => void (d.qaoa.fake_backend = e.target.value || null))} aria-describedby={describedBy} aria-invalid={invalid}>
                  <option value="">Choose…</option>
                  {(caps?.fake_backends ?? []).map((f) => (
                    <option key={f}>{f}</option>
                  ))}
                </Select>
              )}
            </Field>
          ) : null}
          <div className="sm:col-span-2 lg:col-span-1">
            <Toggle
              checked={config.noise_comparison}
              onChange={(v) =>
                upd((d) => {
                  d.noise_comparison = v
                  if (v && !d.qaoa.fake_backend) d.qaoa.fake_backend = caps?.fake_backends[0] ?? null
                })
              }
              label="Ideal vs noisy comparison"
              description="Re-evaluates the trained parameters under the fake backend's noise model."
            />
          </div>
          {config.noise_comparison ? num('Noise trials', 'noise_trials', config.noise_trials, (d, v) => (d.noise_trials = v), 'Seeds per arm') : null}
        </Group>

        <Group title="Screening">
          <Field label="Pre-screen rule" hint="Uses training data only; every solver sees the same subset">
            {({ id, describedBy }) => (
              <Select
                id={id}
                value={config.screening.rule}
                aria-describedby={describedBy}
                onChange={(e) =>
                  update((d) => {
                    d.screening.rule = e.target.value as RunConfig['screening']['rule']
                    if (d.screening.rule !== 'none' && d.screening.keep === null) d.screening.keep = Math.min(d.symbols.length, 8)
                  })
                }
              >
                <option value="none">None: encode every selected asset</option>
                <option value="top_sharpe_train">Top training Sharpe</option>
                <option value="min_variance_train">Lowest training variance</option>
              </Select>
            )}
          </Field>
          {config.screening.rule !== 'none' ? (
            <Field label="Assets kept" error={errors['screening.keep']}>
              {({ id, describedBy, invalid }) => (
                <NumberInput id={id} value={config.screening.keep} onCommit={(v) => update((d) => void (d.screening.keep = v))} aria-describedby={describedBy} aria-invalid={invalid} />
              )}
            </Field>
          ) : null}
        </Group>

        <Group title="Classical baselines">
          <Checkbox checked={b.exact} onChange={(v) => upd((d) => void (d.baselines.exact = v))} label={`Exact enumeration${caps ? ` (≤ ${caps.max_exact_assets} assets)` : ''}`} />
          <Checkbox checked={b.relaxation_rounding} onChange={(v) => upd((d) => void (d.baselines.relaxation_rounding = v))} label="Relaxation + rounding" />
          <Checkbox checked={b.simulated_annealing} onChange={(v) => upd((d) => void (d.baselines.simulated_annealing = v))} label="Simulated annealing" />
          <Checkbox checked={b.greedy} onChange={(v) => upd((d) => void (d.baselines.greedy = v))} label="Greedy" />
          {b.simulated_annealing ? (
            <>
              {num('Annealing restarts', 'baselines.sa_restarts', b.sa_restarts, (d, v) => (d.baselines.sa_restarts = v))}
              {num('Sweeps per restart', 'baselines.sa_sweeps', b.sa_sweeps, (d, v) => (d.baselines.sa_sweeps = v))}
            </>
          ) : null}
        </Group>

        <Group title="Run limits and data">
          {num('Timeout', 'timeout_seconds', config.timeout_seconds, (d, v) => (d.timeout_seconds = v), 'The run stops and is marked timed out after this', 's')}
          <Field label="Risk-free rate (annual)" error={errors.risk_free_rate} hint="Assumption used for Sharpe ratios">
            {({ id, describedBy, invalid }) => (
              <NumberInput
                id={id}
                value={Number((config.risk_free_rate * 100).toFixed(4))}
                suffix="%"
                onCommit={(v) => update((d) => void (d.risk_free_rate = (v ?? 0) / 100))}
                aria-describedby={describedBy}
                aria-invalid={invalid}
              />
            )}
          </Field>
          <Field label="Dataset">
            {({ id }) => (
              <Select id={id} value={config.dataset_id ?? ''} onChange={(e) => update((d) => void (d.dataset_id = e.target.value || null))}>
                <option value="">Server default</option>
                {datasets.map((ds) => (
                  <option key={ds.dataset_id} value={ds.dataset_id}>
                    {ds.label} · {ds.n_symbols} symbols · to {formatDate(ds.last_date)}
                    {ds.stale ? ' (stale)' : ''}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <div className="sm:col-span-2 lg:col-span-3">
            <Checkbox
              checked={forceFresh}
              onChange={setForceFresh}
              label="Compute fresh even if an identical run is cached (otherwise a cached result is replayed and labelled as such)"
            />
          </div>
        </Group>
      </div>
    </Disclosure>
  )
}
