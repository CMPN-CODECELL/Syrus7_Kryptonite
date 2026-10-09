import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react'

export interface TabItem<T extends string> {
  id: T
  label: ReactNode
  badge?: ReactNode
}

/** WAI-ARIA tabs with roving focus (arrow keys, Home/End). Panels render via children(activeId). */
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  label,
  children,
  className = '',
}: {
  items: Array<TabItem<T>>
  value: T
  onChange: (id: T) => void
  label: string
  children: (active: T, panelProps: { id: string; role: 'tabpanel'; 'aria-labelledby': string; tabIndex: 0 }) => ReactNode
  className?: string
}) {
  const base = useId()
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const idx = Math.max(0, items.findIndex((t) => t.id === value))

  const onKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    let next = -1
    if (e.key === 'ArrowRight') next = (idx + 1) % items.length
    else if (e.key === 'ArrowLeft') next = (idx - 1 + items.length) % items.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    if (next >= 0) {
      e.preventDefault()
      onChange(items[next].id)
      refs.current[next]?.focus()
    }
  }

  return (
    <div className={className}>
      <div role="tablist" aria-label={label} className="-mx-1 flex gap-1 overflow-x-auto border-b border-line px-1 [scrollbar-width:thin]">
        {items.map((t, i) => {
          const selected = t.id === value
          return (
            <button
              key={t.id}
              ref={(el) => {
                refs.current[i] = el
              }}
              id={`${base}-tab-${t.id}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls={`${base}-panel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(t.id)}
              onKeyDown={onKey}
              className={`relative -mb-px flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-3 text-sm font-medium whitespace-nowrap transition-colors ${
                selected ? 'border-cobalt text-ink' : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              {t.label}
              {t.badge !== undefined && t.badge !== null ? <span className="rounded-full bg-mist-2 px-1.5 text-xs font-normal text-muted tabular"><span className="sr-only"> </span>{t.badge}</span> : null}
            </button>
          )
        })}
      </div>
      {children(value, { id: `${base}-panel-${value}`, role: 'tabpanel', 'aria-labelledby': `${base}-tab-${value}`, tabIndex: 0 })}
    </div>
  )
}
