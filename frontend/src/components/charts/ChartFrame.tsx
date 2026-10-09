import { useId, type ReactNode } from 'react'
import { Disclosure } from '../ui'

/**
 * Every chart ships with a plain-language summary and its data as a table, so the figure is never the
 * only way to read a result (screen readers, keyboard users, copy/paste).
 */
export function ChartFrame({
  title,
  summary,
  table,
  children,
  height = 280,
  aside,
  legend,
  className = '',
  testId,
}: {
  title: ReactNode
  summary: ReactNode
  table: ReactNode
  children: ReactNode
  height?: number
  aside?: ReactNode
  legend?: ReactNode
  className?: string
  testId?: string
}) {
  const id = useId()
  return (
    <figure className={`m-0 min-w-0 ${className}`} aria-labelledby={`${id}-t`} data-testid={testId}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <h3 id={`${id}-t`} className="text-base font-semibold tracking-tight text-ink">
          {title}
        </h3>
        {aside ? <div className="flex items-center gap-2">{aside}</div> : null}
      </div>
      <p id={`${id}-s`} className="mt-1 max-w-prose text-sm leading-relaxed text-muted">
        {summary}
      </p>
      {legend ? <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs text-ink-2">{legend}</div> : null}
      <div role="img" aria-labelledby={`${id}-t`} aria-describedby={`${id}-s`} className="mt-3 w-full min-w-0" style={{ height }}>
        {children}
      </div>
      <Disclosure summary="Data table" className="mt-3">
        {table}
      </Disclosure>
    </figure>
  )
}

/** Legend entry that pairs colour with a shape/pattern and text (status is never colour alone). */
export function LegendItem({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width="18" height="12" viewBox="0 0 18 12" aria-hidden="true" focusable="false">
        {swatch}
      </svg>
      {children}
    </span>
  )
}
