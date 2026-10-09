import { memo, useDeferredValue, useMemo, useState } from 'react'
import type { AssetInfo } from '../../api/contract.gen'
import { Badge, Button, Checkbox, Field, IconAlert, IconCheck, IconSearch, Select, TextInput } from '../../components/ui'
import { formatInr, formatPct, sectorShort } from '../../lib/format'

const MAX = 50

export function AssetPicker({
  assets,
  selected,
  onToggle,
  onSet,
  onHover,
  error,
  presetMax,
}: {
  assets: AssetInfo[]
  selected: string[]
  onToggle: (s: string) => void
  onSet: (s: string[]) => void
  onHover: (s: string | null) => void
  error?: string | null
  presetMax?: number | null
}) {
  const [q, setQ] = useState('')
  const [sector, setSector] = useState('')
  const [onlySelected, setOnlySelected] = useState(false)
  const query = useDeferredValue(q.trim().toLowerCase())
  const sel = useMemo(() => new Set(selected), [selected])

  const sectors = useMemo(() => {
    const m = new Map<string, number>()
    for (const a of assets) m.set(a.sector, (m.get(a.sector) ?? 0) + 1)
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [assets])

  const shown = useMemo(
    () =>
      assets.filter(
        (a) =>
          (!sector || a.sector === sector) &&
          (!onlySelected || sel.has(a.symbol)) &&
          (!query || a.symbol.toLowerCase().includes(query) || a.name.toLowerCase().includes(query) || a.sector.toLowerCase().includes(query)),
      ),
    [assets, sector, onlySelected, sel, query],
  )
  const gaps = assets.filter((a) => !a.data_ok).length

  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,14rem)]">
        <Field label="Search assets">
          {({ id }) => (
            <div className="relative">
              <IconSearch className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-faint" />
              <TextInput id={id} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Symbol, company or sector" className="pl-9" />
            </div>
          )}
        </Field>
        <Field label="Sector">
          {({ id }) => (
            <Select id={id} value={sector} onChange={(e) => setSector(e.target.value)}>
              <option value="">All sectors ({assets.length})</option>
              {sectors.map(([s, n]) => (
                <option key={s} value={s}>
                  {s} ({n})
                </option>
              ))}
            </Select>
          )}
        </Field>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <Checkbox checked={onlySelected} onChange={setOnlySelected} label="Selected only" />
        <Button
          size="sm"
          variant="quiet"
          onClick={() => onSet([...selected, ...shown.map((a) => a.symbol).filter((s) => !sel.has(s))].slice(0, MAX))}
          disabled={shown.every((a) => sel.has(a.symbol))}
        >
          Select all shown
        </Button>
        <Button size="sm" variant="quiet" onClick={() => onSet([])} disabled={selected.length === 0}>
          Clear selection
        </Button>
        <p className="ml-auto text-sm text-ink-2 tabular" aria-live="polite" data-testid="selected-count">
          <span className="font-semibold text-ink">{selected.length}</span> of {assets.length} selected
          {presetMax ? <span className="text-muted"> · preset encodes up to {presetMax}</span> : null}
        </p>
      </div>
      {error ? (
        <p className="mt-1 flex items-center gap-1.5 text-sm font-medium text-danger" role="alert">
          <IconAlert size={15} /> {error}
        </p>
      ) : null}
      {gaps ? (
        <p className="mt-1 text-xs text-muted">
          {gaps} asset{gaps === 1 ? ' has' : 's have'} incomplete price history in the active dataset (marked “Data gap”); the preflight explains how they are handled.
        </p>
      ) : null}

      <ul className="mt-3 grid grid-cols-1 max-h-[30rem] gap-px overflow-y-auto rounded-lg border border-line bg-line md:grid-cols-2" aria-label="NIFTY 50 assets" data-testid="asset-list">
        {shown.length === 0 ? (
          <li className="col-span-full bg-canvas px-4 py-8 text-center text-sm text-muted">No assets match these filters.</li>
        ) : (
          shown.map((a) => <AssetRow key={a.symbol} a={a} checked={sel.has(a.symbol)} full={selected.length >= MAX} onToggle={onToggle} onHover={onHover} />)
        )}
      </ul>
    </div>
  )
}

const AssetRow = memo(function AssetRow({
  a,
  checked,
  full,
  onToggle,
  onHover,
}: {
  a: AssetInfo
  checked: boolean
  full: boolean
  onToggle: (s: string) => void
  onHover: (s: string | null) => void
}) {
  return (
    <li className={checked ? 'bg-teal-50/70' : 'bg-canvas'} onPointerEnter={() => onHover(a.symbol)} onPointerLeave={() => onHover(null)}>
      <label className="flex min-h-14 cursor-pointer items-center gap-3 px-3 py-2 has-disabled:cursor-not-allowed has-focus-visible:outline-2 has-focus-visible:-outline-offset-2 has-focus-visible:outline-cobalt">
        <input
          type="checkbox"
          className="h-[18px] w-[18px] shrink-0 accent-teal"
          checked={checked}
          disabled={!checked && full}
          onChange={() => onToggle(a.symbol)}
          onFocus={() => onHover(a.symbol)}
          onBlur={() => onHover(null)}
          aria-describedby={`asset-${a.symbol}-meta`}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="font-mono text-sm font-semibold text-ink">{a.symbol}</span>
            {checked ? <IconCheck size={14} className="text-teal" aria-hidden="true" /> : null}
            <span className="ml-auto text-xs text-muted tabular">{a.last_price_inr !== null ? formatInr(a.last_price_inr, { paise: true }) : ''}</span>
          </span>
          <span className="block truncate text-xs text-muted">{a.name}</span>
          <span id={`asset-${a.symbol}-meta`} className="mt-1 flex flex-wrap gap-1.5">
            <Badge tone="neutral" title={a.sector}>
              {sectorShort(a.sector)}
            </Badge>
            {a.data_ok ? (
              <Badge tone="neutral" title={`${a.n_obs} daily observations, ${a.first_date ?? '?'} to ${a.last_date ?? '?'}`}>
                {formatPct(a.coverage, 0)} coverage
              </Badge>
            ) : (
              <Badge tone="amber" icon={<IconAlert size={12} />} title={a.data_note ?? 'Incomplete price history'}>
                Data gap · {formatPct(a.coverage, 0)}
              </Badge>
            )}
          </span>
        </span>
      </label>
    </li>
  )
})
