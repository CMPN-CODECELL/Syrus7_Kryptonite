import { useId, useState, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react'
import { IconAlert, IconChevronDown } from './icons'

/** Label + control + hint + error, with ids wired for aria-describedby. */
export function Field({
  label,
  hint,
  error,
  children,
  className = '',
  labelAside,
  id: idProp,
}: {
  label: ReactNode
  hint?: ReactNode
  error?: string | null
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => ReactNode
  className?: string
  labelAside?: ReactNode
  id?: string
}) {
  const auto = useId()
  const id = idProp ?? `f${auto}`
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className}`}>
      <div className="flex items-baseline justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-ink-2">
          {label}
        </label>
        {labelAside}
      </div>
      {children({ id, describedBy, invalid: !!error })}
      {hint ? (
        <p id={hintId} className="text-xs leading-relaxed text-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errId} className="flex items-start gap-1.5 text-xs font-medium text-danger" role="alert">
          <IconAlert size={14} className="mt-px shrink-0" />
          <span>{error}</span>
        </p>
      ) : null}
    </div>
  )
}

export const inputClass =
  'min-h-11 w-full min-w-0 rounded-md border border-line-strong bg-canvas px-3 text-[0.95rem] text-ink tabular placeholder:text-faint transition-colors hover:border-ink-2/40 focus-visible:border-cobalt focus-visible:outline-2 focus-visible:outline-offset-0 aria-[invalid=true]:border-danger disabled:bg-mist disabled:text-faint'

export function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  const { className = '', ...rest } = props
  return <input type="text" className={`${inputClass} ${className}`} {...rest} />
}

/**
 * Number input that lets the user type freely (including empty / partial values) and commits
 * a parsed number. `null` is passed when the field is cleared and `allowEmpty` is set.
 */
export function NumberInput({
  value,
  onCommit,
  allowEmpty = false,
  suffix,
  className = '',
  ...rest
}: Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & {
  value: number | null | undefined
  onCommit: (v: number | null) => void
  allowEmpty?: boolean
  suffix?: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const shown = draft ?? (value === null || value === undefined ? '' : String(value))
  const input = (
    <input
      type="text"
      inputMode="decimal"
      autoComplete="off"
      className={`${inputClass} ${suffix ? 'pr-12' : ''} ${className}`}
      value={shown}
      onChange={(e) => {
        const raw = e.target.value
        setDraft(raw)
        const t = raw.trim()
        if (t === '') {
          if (allowEmpty) onCommit(null)
          return
        }
        const n = Number(t.replace(/,/g, ''))
        if (Number.isFinite(n)) onCommit(n)
      }}
      onBlur={() => setDraft(null)}
      {...rest}
    />
  )
  if (!suffix) return input
  return (
    <div className="relative">
      {input}
      <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-sm text-muted" aria-hidden="true">
        {suffix}
      </span>
    </div>
  )
}

export function Select({ className = '', children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className={`relative ${className}`}>
      <select className={`${inputClass} appearance-none pr-9`} {...rest}>
        {children}
      </select>
      <IconChevronDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted" />
    </div>
  )
}

export function Slider({
  id,
  value,
  min,
  max,
  step = 1,
  onChange,
  describedBy,
  valueText,
  disabled,
  ariaLabel,
}: {
  id?: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  describedBy?: string
  valueText?: string
  disabled?: boolean
  ariaLabel?: string
}) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0
  return (
    <input
      id={id}
      type="range"
      className="qf-range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-describedby={describedBy}
      aria-valuetext={valueText}
      aria-label={ariaLabel}
      style={{ ['--qf-fill' as string]: `${pct}%` }}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  )
}

/** Accessible switch (role="switch"); the visible label is part of the control so the whole row is clickable. */
export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  id,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
  id?: string
}) {
  const auto = useId()
  const descId = description ? `${id ?? auto}-d` : undefined
  return (
    // The switch keeps a 44px hit area but only takes the track's height in the layout, so it lines up with its label.
    <div className={`flex justify-between gap-4 ${description ? 'items-start' : 'items-center'}`}>
      <div className="min-w-0">
        <span id={`${id ?? auto}-l`} className="text-sm font-medium text-ink-2">
          {label}
        </span>
        {description ? (
          <p id={descId} className="mt-0.5 text-xs leading-relaxed text-muted">
            {description}
          </p>
        ) : null}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id ?? auto}-l`}
        aria-describedby={descId}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className="group relative -my-2.5 inline-flex min-h-11 min-w-11 shrink-0 items-center justify-center disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span className={`relative h-6 w-10 rounded-full transition-colors ${checked ? 'bg-cobalt' : 'bg-line-strong'}`}>
          <span
            className={`absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform duration-150 ${checked ? 'translate-x-4' : ''}`}
          />
        </span>
        <span className="sr-only">{checked ? 'On' : 'Off'}</span>
      </button>
    </div>
  )
}

export function Checkbox({
  checked,
  onChange,
  label,
  disabled,
  className = '',
  describedBy,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: ReactNode
  disabled?: boolean
  className?: string
  describedBy?: string
}) {
  return (
    <label className={`inline-flex min-h-11 cursor-pointer items-center gap-2.5 text-sm text-ink-2 pointer-fine:min-h-9 has-disabled:cursor-not-allowed has-disabled:opacity-60 ${className}`}>
      <input
        type="checkbox"
        className="h-[18px] w-[18px] shrink-0 accent-cobalt"
        checked={checked}
        disabled={disabled}
        aria-describedby={describedBy}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  )
}

/** Segmented single-choice control built on native radios (keyboard arrows work natively). */
export function Segmented<T extends string>({
  name,
  value,
  options,
  onChange,
  legend,
  hideLegend = false,
  size = 'md',
}: {
  name: string
  value: T
  options: Array<{ value: T; label: ReactNode; disabled?: boolean }>
  onChange: (v: T) => void
  legend: string
  hideLegend?: boolean
  size?: 'sm' | 'md'
}) {
  return (
    <fieldset className="min-w-0">
      <legend className={hideLegend ? 'sr-only' : 'mb-1.5 text-sm font-medium text-ink-2'}>{legend}</legend>
      <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-mist p-1">
        {options.map((o) => (
          <label
            key={o.value}
            className={`relative flex min-h-11 flex-1 cursor-pointer items-center justify-center rounded-md px-3 text-center text-sm font-medium transition-colors has-focus-visible:outline-2 has-focus-visible:outline-cobalt has-disabled:cursor-not-allowed has-disabled:opacity-50 ${
              size === 'sm' ? 'pointer-fine:min-h-9' : ''
            } ${value === o.value ? 'bg-canvas text-ink shadow-card ring-1 ring-line' : 'text-muted hover:text-ink'}`}
          >
            <input type="radio" className="sr-only" name={name} value={o.value} checked={value === o.value} disabled={o.disabled} onChange={() => onChange(o.value)} />
            {o.label}
          </label>
        ))}
      </div>
    </fieldset>
  )
}
