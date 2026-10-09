import { memo, useDeferredValue, useMemo, useState } from 'react'
import type { AssetInfo } from '../../api/contract.gen'
import { Badge, Button, Checkbox, Field, IconAlert, IconCheck, IconSearch, Select, Sparkline, TextInput, Tooltip } from '../../components/ui'
import { DASH, formatDate, formatInr, formatPct, sectorShort } from '../../lib/format'
import { GLOSSARY } from '../../lib/glossary'
import { useLivePrice, useLiveStatus } from '../../lib/live'

const MAX = 50

export function AssetPicker({
  assets,
  selected,
  onToggle,
  onSet,
  error,
  presetMax,
}: {
  assets: AssetInfo[]
  selected: string[]
  onToggle: (s: string) => void
  onSet: (s: string[]) => void
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
  const priceDate = assets.reduce<string | null>((d, a) => (a.last_date && (!d || a.last_date > d) ? a.last_date : d), null)
  const liveStatus = useLiveStatus()

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
      <p className="mt-2 flex flex-wrap items-center gap-x-1 text-xs text-muted" data-testid="price-note">
        <span>
          {liveStatus
            ? `Price: ${liveStatus === 'live' ? 'live' : 'latest'} NSE price, in ₹ per share.`
            : `Price: last NSE close${priceDate ? ` on ${formatDate(priceDate)}` : ''}, in ₹ per share.`}
        </span>
        <Tooltip content={GLOSSARY.price} label="What is the price?" />
        <span className="ml-3">Line and % show the last year.</span>
        <Tooltip content={GLOSSARY.change1y} label="What is the 1-year change?" />
      </p>
      {gaps ? (
        <p className="mt-1 text-xs text-muted">
          {gaps} stock{gaps === 1 ? ' has' : 's have'} a short price history (marked “Data gap”).
        </p>
      ) : null}

      <ul className="mt-3 grid grid-cols-1 max-h-[30rem] gap-px overflow-y-auto rounded-lg border border-line bg-line md:grid-cols-2" aria-label="NIFTY 50 assets" data-testid="asset-list">
        {shown.length === 0 ? (
          <li className="col-span-full bg-canvas px-4 py-8 text-center text-sm text-muted">No assets match these filters.</li>
        ) : (
          shown.map((a) => <AssetRow key={a.symbol} a={a} checked={sel.has(a.symbol)} full={selected.length >= MAX} onToggle={onToggle} />)
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
}: {
  a: AssetInfo
  checked: boolean
  full: boolean
  onToggle: (s: string) => void
}) {
  const live = useLivePrice(a.symbol)
  const flash = live && live.tick > 0 && live.move ? (live.move === 'up' ? 'qf-price-flash-up' : 'qf-price-flash-down') : ''
  return (
    <li className={checked ? 'bg-teal-50/70' : 'bg-canvas'} data-symbol={a.symbol}>
      <label className="flex min-h-14 cursor-pointer items-center gap-3 px-3 py-2 has-disabled:cursor-not-allowed has-focus-visible:outline-2 has-focus-visible:-outline-offset-2 has-focus-visible:outline-cobalt">
        <input
          type="checkbox"
          className="h-[18px] w-[18px] shrink-0 accent-teal"
          checked={checked}
          disabled={!checked && full}
          onChange={() => onToggle(a.symbol)}
          aria-describedby={`asset-${a.symbol}-meta`}
        />
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-ink">{a.symbol}</span>
            {checked ? <IconCheck size={14} className="text-teal" aria-hidden="true" /> : null}
            {live ? (
              <span key={live.tick} className={`-mr-1 ml-auto rounded px-1 text-sm font-medium text-ink-2 tabular ${flash}`} title="Live NSE price" data-testid="live-price">
                {formatInr(live.quote.price, { paise: true })}
              </span>
            ) : (
              <span className="ml-auto text-sm font-medium text-ink-2 tabular" title={a.last_date ? `NSE close on ${formatDate(a.last_date)}` : undefined}>
                {a.last_price_inr !== null ? formatInr(a.last_price_inr, { paise: true }) : DASH}
              </span>
            )}
          </span>
          <span className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{a.name}</span>
            {a.trend.length > 1 ? <Sparkline values={a.trend} width={44} height={14} /> : null}
            <span
              className={`w-14 shrink-0 text-right text-xs font-medium tabular ${a.change_1y === null ? 'text-faint' : a.change_1y < 0 ? 'text-danger' : 'text-teal'}`}
              title={a.change_1y === null ? (a.price_note ?? 'Not enough history for a 1-year change') : 'Change over the last year'}
            >
              {formatPct(a.change_1y, 1, { sign: true })}
            </span>
          </span>
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
            {a.price_note ? (
              <Badge tone="amber" icon={<IconAlert size={12} />} title={a.price_note}>
                {a.price_note.startsWith('corrected') ? 'Price corrected' : 'Price jump'}
              </Badge>
            ) : null}
          </span>
        </span>
      </label>
    </li>
  )
})
