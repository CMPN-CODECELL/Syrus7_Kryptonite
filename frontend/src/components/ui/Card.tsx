import type { ReactNode } from 'react'

export function Card({
  children,
  className = '',
  as: Tag = 'section',
  padded = true,
  ...rest
}: { children: ReactNode; className?: string; as?: 'section' | 'div' | 'article' | 'aside'; padded?: boolean } & React.HTMLAttributes<HTMLElement>) {
  return (
    <Tag className={`min-w-0 rounded-lg border border-line bg-canvas shadow-card ${padded ? 'p-4 sm:p-5' : ''} ${className}`} {...rest}>
      {children}
    </Tag>
  )
}

export function CardHeader({ title, eyebrow, aside, description, id, level = 2 }: { title: ReactNode; eyebrow?: ReactNode; aside?: ReactNode; description?: ReactNode; id?: string; level?: 2 | 3 }) {
  const H = level === 2 ? 'h2' : 'h3'
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
        <H id={id} className={`${level === 2 ? 'text-lg' : 'text-base'} font-semibold tracking-tight text-ink`}>
          {title}
        </H>
        {description ? <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted">{description}</p> : null}
      </div>
      {aside ? <div className="flex shrink-0 flex-wrap items-center gap-2">{aside}</div> : null}
    </div>
  )
}

/** A labelled metric with explicit units. */
export function Metric({ label, value, unit, hint, tone, className = '' }: { label: ReactNode; value: ReactNode; unit?: ReactNode; hint?: ReactNode; tone?: 'loss' | 'gain'; className?: string }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className={`mt-1 text-xl font-semibold tracking-tight tabular ${tone === 'loss' ? 'text-danger' : 'text-ink'}`}>
        {value}
        {unit ? <span className="ml-1 text-xs font-medium text-muted">{unit}</span> : null}
      </dd>
      {hint ? <dd className="mt-0.5 text-xs leading-snug text-faint">{hint}</dd> : null}
    </div>
  )
}
