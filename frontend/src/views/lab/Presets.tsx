import type { Preset, PresetName } from '../../api/contract.gen'
import { Badge, IconCheck } from '../../components/ui'
import { formatDuration, formatInt } from '../../lib/format'

/** Quick / Balanced / Research as radio cards that state their actual settings, not adjectives. */
export function PresetPicker({ presets, value, onPick }: { presets: Preset[]; value: PresetName; onPick: (p: Preset) => void }) {
  const list = presets.filter((p) => p.name !== 'custom')
  return (
    <fieldset className="min-w-0">
      <legend className="flex w-full flex-wrap items-center gap-2 text-sm font-medium text-ink-2">
        Run preset
        {value === 'custom' ? (
          <Badge tone="amber" title="QAOA, baseline or timeout settings were edited by hand">
            Custom settings
          </Badge>
        ) : null}
      </legend>
      <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
        {list.map((p) => {
          const on = value === p.name
          return (
            <label
              key={p.name}
              className={`relative flex cursor-pointer flex-col rounded-lg border p-4 transition-colors has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-cobalt ${
                on ? 'border-cobalt bg-cobalt-50/60 ring-1 ring-cobalt' : 'border-line bg-canvas hover:border-line-strong'
              }`}
              data-testid={`preset-${p.name}`}
            >
              <input type="radio" name="preset" className="sr-only" value={p.name} checked={on} onChange={() => onPick(p)} />
              <span className="flex items-center gap-2">
                <span className="font-semibold text-ink">{p.title}</span>
                {on ? (
                  <span className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-cobalt-800">
                    <IconCheck size={14} /> Selected
                  </span>
                ) : null}
              </span>
              <span className="mt-1 text-xs leading-relaxed text-muted">{p.description}</span>
              <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-ink-2 tabular">
                <dt className="text-muted">Depth p</dt>
                <dd className="text-right">{p.qaoa.depth}</dd>
                <dt className="text-muted">Optimiser</dt>
                <dd className="text-right">{p.qaoa.optimizer}</dd>
                <dt className="text-muted">Evaluations</dt>
                <dd className="text-right">{formatInt(p.qaoa.max_evaluations)}</dd>
                <dt className="text-muted">Shots / eval</dt>
                <dd className="text-right">{formatInt(p.qaoa.shots)}</dd>
                <dt className="text-muted">Final shots</dt>
                <dd className="text-right">{formatInt(p.qaoa.final_shots)}</dd>
                <dt className="text-muted">Max assets</dt>
                <dd className="text-right">{p.max_assets}</dd>
                <dt className="text-muted">Timeout</dt>
                <dd className="text-right">{formatDuration(p.timeout_seconds)}</dd>
              </dl>
              <span className="mt-3 border-t border-line pt-2 text-xs text-muted">
                {p.measured_median_s !== null ? (
                  <>
                    Measured median <span className="font-medium text-ink-2 tabular">{formatDuration(p.measured_median_s)}</span>
                    {p.measured_note ? ` · ${p.measured_note}` : ''}
                  </>
                ) : (
                  'Runtime not yet measured on this server'
                )}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
