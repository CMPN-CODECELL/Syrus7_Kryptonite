import { useMemo } from 'react'
import { BOUNDS, type AssetInfo, type RunConfig } from '../../api/contract.gen'
import { Card, CardHeader, Field, NumberInput, Segmented, Slider, TextInput, Toggle } from '../../components/ui'
import { formatInr, formatInrCompact, sectorShort } from '../../lib/format'
import type { FieldErrors } from '../../lib/validate'

type Update = (fn: (d: RunConfig) => void) => void

// Risk appetite slider works on a log scale of q (risk aversion): left = cautious (q=100), right = return-seeking (q=0.01).
const qToPos = (q: number) => (q <= 0 ? 100 : Math.max(0, Math.min(100, Math.round(50 - 25 * Math.log10(q)))))
const posToQ = (p: number) => Number((10 ** ((50 - p) / 25)).toPrecision(2))

function appetiteText(q: number): string {
  if (q <= 0) return 'return only (variance ignored)'
  if (q < 0.3) return 'return-seeking'
  if (q <= 3) return 'balanced'
  if (q <= 15) return 'cautious'
  return 'very cautious'
}

const pct = (v: number | null | undefined) => (v === null || v === undefined ? null : Number((v * 100).toFixed(4)))

export function ObjectiveControls({ config, update, errors }: { config: RunConfig; update: Update; errors: FieldErrors }) {
  const q = config.risk_aversion
  return (
    <Card>
      <CardHeader level={2} title="Objective and budget" description="Minimise q·risk − expected return + trading costs, all annualised from the training window." />
      <div className="grid grid-cols-1 gap-5">
        <Field
          label="Risk appetite"
          hint="Sets q, the weight on portfolio variance. Moving right accepts more risk for expected return."
          error={errors.risk_aversion}
          labelAside={<span className="text-sm text-ink tabular" aria-hidden="true">q = {q}</span>}
        >
          {({ id, describedBy }) => (
            <div>
              <Slider
                id={id}
                min={0}
                max={100}
                step={1}
                value={qToPos(q)}
                onChange={(p) => update((d) => void (d.risk_aversion = posToQ(p)))}
                describedBy={describedBy}
                valueText={`q = ${q}, ${appetiteText(q)}`}
              />
              <div className="mt-1 flex justify-between text-xs text-muted" aria-hidden="true">
                <span>Cautious</span>
                <span className="font-medium text-ink-2">{appetiteText(q)}</span>
                <span>Return-seeking</span>
              </div>
            </div>
          )}
        </Field>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Risk aversion q (exact)" error={null} hint={`${BOUNDS.RunConfig.risk_aversion.minimum} to ${BOUNDS.RunConfig.risk_aversion.maximum}`}>
            {({ id, describedBy, invalid }) => (
              <NumberInput id={id} value={q} onCommit={(v) => update((d) => void (d.risk_aversion = v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
            )}
          </Field>
          <Field label="Budget" hint={`Indicative capital: ${formatInrCompact(config.budget_inr)}`} error={errors.budget_inr}>
            {({ id, describedBy, invalid }) => (
              <NumberInput id={id} value={config.budget_inr} onCommit={(v) => update((d) => void (d.budget_inr = v ?? 0))} suffix="₹" aria-describedby={describedBy} aria-invalid={invalid} />
            )}
          </Field>
        </div>
        <Segmented
          name="mode"
          legend="Encoding"
          value={config.mode}
          onChange={(m) =>
            update((d) => {
              d.mode = m
              // K is required in selection mode and unsupported in lots mode (server rule)
              d.constraints.cardinality = m === 'lots' ? null : (d.constraints.cardinality ?? Math.max(1, Math.round(d.symbols.length / 2)))
              if (m === 'lots' && !d.constraints.lots)
                d.constraints.lots = {
                  total_lots: BOUNDS.LotConfig.total_lots.default,
                  max_lots_per_asset: BOUNDS.LotConfig.max_lots_per_asset.default,
                  allow_cash_remainder: BOUNDS.LotConfig.allow_cash_remainder.default,
                }
            })
          }
          options={[
            { value: 'selection', label: 'Equal-weight selection' },
            { value: 'lots', label: 'Bounded lots' },
          ]}
        />
      </div>
    </Card>
  )
}

export function HoldingsControls({ config, update, errors }: { config: RunConfig; update: Update; errors: FieldErrors }) {
  const n = config.symbols.length
  const k = config.constraints
  if (config.mode === 'lots') {
    const L = k.lots
    return (
      <Card>
        <CardHeader
          level={2}
          title="Lots"
          description="Each asset gets 0 to m lots, encoded with binary bits. A lot is a fraction of the budget chosen for modelling, not an exchange lot size."
        />
        {L ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Total lots" hint={`Weight per lot = 1 / total (${BOUNDS.LotConfig.total_lots.minimum}–${BOUNDS.LotConfig.total_lots.maximum})`} error={errors['constraints.lots.total_lots']}>
              {({ id, describedBy, invalid }) => (
                <NumberInput id={id} value={L.total_lots} onCommit={(v) => update((d) => void (d.constraints.lots!.total_lots = v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
              )}
            </Field>
            <Field label="Max lots per asset" hint={`${BOUNDS.LotConfig.max_lots_per_asset.minimum}–${BOUNDS.LotConfig.max_lots_per_asset.maximum}; sets bits per asset`} error={errors['constraints.lots.max_lots_per_asset']}>
              {({ id, describedBy, invalid }) => (
                <NumberInput id={id} value={L.max_lots_per_asset} onCommit={(v) => update((d) => void (d.constraints.lots!.max_lots_per_asset = v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
              )}
            </Field>
            <div className="sm:col-span-2">
              <Toggle
                checked={L.allow_cash_remainder}
                onChange={(v) => update((d) => void (d.constraints.lots!.allow_cash_remainder = v))}
                label="Allow uninvested cash"
                description="Budget becomes ‘at most’ the total lots, which adds slack bits."
              />
            </div>
          </div>
        ) : (
          <p className="text-sm text-danger">{errors['constraints.lots']}</p>
        )}
      </Card>
    )
  }
  return (
    <Card>
      <CardHeader level={2} title="Holdings" description="Hold exactly K of the selected assets, each at weight 1/K." />
      <Field
        label="Number of holdings (K)"
        hint={n >= 2 ? `1 to ${n} with the current selection` : 'Select assets first'}
        error={errors['constraints.cardinality']}
      >
        {({ id, describedBy, invalid }) => (
          <div className="flex items-center gap-3">
            <div className="w-28">
              <NumberInput id={id} allowEmpty value={k.cardinality} onCommit={(v) => update((d) => void (d.constraints.cardinality = v))} required aria-describedby={describedBy} aria-invalid={invalid} />
            </div>
            <div className="min-w-0 flex-1">
              <Slider
                min={1}
                max={Math.max(2, n)}
                value={Math.min(k.cardinality ?? 1, Math.max(2, n))}
                onChange={(v) => update((d) => void (d.constraints.cardinality = v))}
                ariaLabel="Number of holdings"
                valueText={`${k.cardinality ?? 'any'} holdings`}
                disabled={n < 2}
              />
            </div>
          </div>
        )}
      </Field>
    </Card>
  )
}

export function SectorControls({ config, update, errors, assets }: { config: RunConfig; update: Update; errors: FieldErrors; assets: AssetInfo[] }) {
  const sc = config.constraints.sector_caps
  const sectors = useMemo(() => {
    const bySym = new Map(assets.map((a) => [a.symbol, a.sector]))
    const m = new Map<string, number>()
    for (const s of config.symbols) {
      const sec = bySym.get(s)
      if (sec) m.set(sec, (m.get(sec) ?? 0) + 1)
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  }, [assets, config.symbols])
  const weight = sc?.basis === 'weight'
  return (
    <Card>
      <CardHeader level={2} title="Sector caps" description="Limit how much of the portfolio any one sector can take. Caps become penalty terms with slack bits." />
      <Toggle
        checked={!!sc}
        onChange={(on) => update((d) => void (d.constraints.sector_caps = on ? { basis: 'count', default_max: 2, per_sector: {} } : null))}
        label="Apply sector caps"
      />
      {sc ? (
        <div className="mt-4 grid grid-cols-1 gap-4">
          <div className="grid grid-cols-1 gap-4">
            <Segmented
              name="cap-basis"
              legend="Cap basis"
              size="sm"
              value={sc.basis}
              onChange={(b) =>
                update((d) => {
                  const c = d.constraints.sector_caps!
                  c.basis = b
                  c.default_max = b === 'weight' ? 0.4 : 2
                  c.per_sector = {}
                })
              }
              options={[
                { value: 'count', label: 'Holdings count' },
                { value: 'weight', label: 'Weight' },
              ]}
            />
            <Field label={weight ? 'Default cap (% of portfolio)' : 'Default cap (holdings)'} error={errors['constraints.sector_caps.default_max']} hint="Applies to every sector without its own cap">
              {({ id, describedBy, invalid }) => (
                <NumberInput
                  id={id}
                  allowEmpty
                  value={weight ? pct(sc.default_max) : sc.default_max}
                  suffix={weight ? '%' : undefined}
                  onCommit={(v) => update((d) => void (d.constraints.sector_caps!.default_max = v === null ? null : weight ? v / 100 : v))}
                  aria-describedby={describedBy}
                  aria-invalid={invalid}
                />
              )}
            </Field>
          </div>
          {sectors.length ? (
            <fieldset>
              <legend className="text-sm font-medium text-ink-2">Per-sector overrides for your selection</legend>
              <div className="mt-2 grid grid-cols-1 gap-x-4 gap-y-3 sm:grid-cols-2">
                {sectors.map(([sec, count]) => (
                  <Field key={sec} label={`${sectorShort(sec)} · ${count} selected`} error={errors[`constraints.sector_caps.${sec}`]}>
                    {({ id, describedBy, invalid }) => (
                      <NumberInput
                        id={id}
                        allowEmpty
                        placeholder="Default"
                        value={weight ? pct(sc.per_sector[sec]) : (sc.per_sector[sec] ?? null)}
                        suffix={weight ? '%' : undefined}
                        onCommit={(v) =>
                          update((d) => {
                            const ps = d.constraints.sector_caps!.per_sector
                            if (v === null) delete ps[sec]
                            else ps[sec] = weight ? v / 100 : v
                          })
                        }
                        aria-describedby={describedBy}
                        aria-invalid={invalid}
                      />
                    )}
                  </Field>
                ))}
              </div>
            </fieldset>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

export function TargetControls({ config, update, errors }: { config: RunConfig; update: Update; errors: FieldErrors }) {
  const t = config.constraints.target_return
  return (
    <Card>
      <CardHeader level={2} title="Target return" description="Require at least this annualised expected return, estimated on the training window only." />
      <Toggle checked={t !== null} onChange={(on) => update((d) => void (d.constraints.target_return = on ? 0.12 : null))} label="Set a return floor" />
      {t !== null ? (
        <Field className="mt-4 max-w-xs" label="Minimum expected return (annual)" error={errors['constraints.target_return']}>
          {({ id, describedBy, invalid }) => (
            <NumberInput id={id} value={pct(t)} suffix="%" onCommit={(v) => update((d) => void (d.constraints.target_return = v === null ? 0 : v / 100))} aria-describedby={describedBy} aria-invalid={invalid} />
          )}
        </Field>
      ) : null}
    </Card>
  )
}

export function CostControls({ config, update, errors }: { config: RunConfig; update: Update; errors: FieldErrors }) {
  const tc = config.constraints.transaction_costs
  const sp = tc.starting_portfolio
  const total = Object.values(sp.weights).reduce((a, b) => a + b, 0)
  return (
    <Card>
      <CardHeader level={2} title="Transaction costs" description="Costs are charged on traded weight relative to the starting portfolio and reported gross and net." />
      <Toggle checked={tc.enabled} onChange={(v) => update((d) => void (d.constraints.transaction_costs.enabled = v))} label="Include transaction costs" />
      {tc.enabled ? (
        <div className="mt-4 grid grid-cols-1 gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Proportional cost" hint="Brokerage, STT and impact per unit traded" error={errors['constraints.transaction_costs.proportional_bps']}>
              {({ id, describedBy, invalid }) => (
                <NumberInput id={id} value={tc.proportional_bps} suffix="bps" onCommit={(v) => update((d) => void (d.constraints.transaction_costs.proportional_bps = v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
              )}
            </Field>
            <Field label="Fixed cost per trade" hint={tc.fixed_inr_per_trade ? formatInr(tc.fixed_inr_per_trade) : 'None'} error={errors['constraints.transaction_costs.fixed_inr_per_trade']}>
              {({ id, describedBy, invalid }) => (
                <NumberInput id={id} value={tc.fixed_inr_per_trade} suffix="₹" onCommit={(v) => update((d) => void (d.constraints.transaction_costs.fixed_inr_per_trade = v ?? 0))} aria-describedby={describedBy} aria-invalid={invalid} />
              )}
            </Field>
          </div>
          <Segmented
            name="start"
            legend="Starting portfolio"
            size="sm"
            value={sp.type}
            onChange={(t) =>
              update((d) => {
                d.constraints.transaction_costs.starting_portfolio = { type: t, weights: {} }
              })
            }
            options={[
              { value: 'cash', label: 'All cash (initial purchase)' },
              { value: 'holdings', label: 'Existing holdings' },
            ]}
          />
          {sp.type === 'holdings' ? (
            <fieldset>
              <legend className="text-sm font-medium text-ink-2">Current weights (the rest is cash)</legend>
              {config.symbols.length ? (
                <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
                  {config.symbols.map((s) => (
                    <Field key={s} label={s}>
                      {({ id }) => (
                        <NumberInput
                          id={id}
                          allowEmpty
                          placeholder="0"
                          suffix="%"
                          value={pct(sp.weights[s])}
                          onCommit={(v) =>
                            update((d) => {
                              const w = d.constraints.transaction_costs.starting_portfolio.weights
                              if (v === null || v === 0) delete w[s]
                              else w[s] = v / 100
                            })
                          }
                        />
                      )}
                    </Field>
                  ))}
                </div>
              ) : (
                <p className="mt-1 text-sm text-muted">Select assets to enter their current weights.</p>
              )}
              <p className={`mt-2 text-sm tabular ${total > 1 + 1e-9 ? 'font-medium text-danger' : 'text-muted'}`} role={errors['constraints.transaction_costs.starting_portfolio'] ? 'alert' : undefined}>
                Invested {(total * 100).toFixed(1)}% · cash {Math.max(0, 100 - total * 100).toFixed(1)}%
                {errors['constraints.transaction_costs.starting_portfolio'] ? ` — ${errors['constraints.transaction_costs.starting_portfolio']}` : ''}
              </p>
            </fieldset>
          ) : null}
        </div>
      ) : null}
    </Card>
  )
}

const DATE_FIELDS: Array<{ seg: string; keys: Array<[keyof RunConfig['dates'], string]> }> = [
  { seg: 'Training', keys: [['train_start', 'Start'], ['train_end', 'End']] },
  { seg: 'Validation', keys: [['validation_start', 'Start'], ['validation_end', 'End']] },
  { seg: 'Held-out test', keys: [['test_start', 'Start'], ['test_end', 'End']] },
]

export function DateControls({ config, update, errors, range }: { config: RunConfig; update: Update; errors: FieldErrors; range?: { first: string; last: string } }) {
  return (
    <Card>
      <CardHeader
        level={2}
        title="Date split"
        description={`Estimate on training data, choose settings on validation, then evaluate once on the held-out test window.${range ? ` Data available ${range.first} to ${range.last}.` : ''}`}
      />
      {errors.dates ? (
        <p className="mb-3 text-sm font-medium text-danger" role="alert">
          {errors.dates}
        </p>
      ) : null}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {DATE_FIELDS.map((g) => (
          <fieldset key={g.seg} className="min-w-0 rounded-md border border-line p-3">
            <legend className="px-1 text-sm font-medium text-ink-2">{g.seg}</legend>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-1">
              {g.keys.map(([k, label]) => (
                <Field key={k} label={label} error={errors[`dates.${k}`]}>
                  {({ id, describedBy, invalid }) => (
                    <TextInput
                      id={id}
                      type="date"
                      value={config.dates[k]}
                      min={range?.first}
                      max={range?.last}
                      onChange={(e) => update((d) => void (d.dates[k] = e.target.value))}
                      aria-describedby={describedBy}
                      aria-invalid={invalid}
                    />
                  )}
                </Field>
              ))}
            </div>
          </fieldset>
        ))}
      </div>
    </Card>
  )
}
