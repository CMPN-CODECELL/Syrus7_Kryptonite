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
      <div className="mt-2 grid grid-cols-1 gap-2.5 md:grid-cols-3 md:gap-3">
        {list.map((p) => {
          const on = value === p.name
          return (
            <label
              key={p.name}
              title={p.description}
              className={`@container relative flex cursor-pointer flex-col rounded-lg border p-3.5 transition-colors has-focus-visible:outline-2 has-focus-visible:outline-offset-2 has-focus-visible:outline-cobalt ${
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
              {/* Two setting pairs per row whenever the card is wide enough (always on phones, where the cards stack). */}
              <dl className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 text-xs whitespace-nowrap text-ink-2 tabular @min-[18rem]:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto] @min-[18rem]:gap-x-4">
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
              <span className="mt-2 border-t border-line pt-1.5 text-xs text-muted" title={p.measured_note ?? undefined}>
                {p.measured_median_s !== null ? (
                  <>
                    Typical run <span className="font-medium text-ink-2 tabular">{formatDuration(p.measured_median_s)}</span>
                  </>
                ) : (
                  'Not timed on this server yet'
                )}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}
