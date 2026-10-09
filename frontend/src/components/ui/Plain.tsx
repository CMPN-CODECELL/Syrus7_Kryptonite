import type { ReactNode } from 'react'
import { IconHelp } from './icons'

/** A plain-language explanation placed above the detailed numbers, for first-time readers. */
export function InSimpleWords({ children, title = 'In simple words', className = '' }: { children: ReactNode; title?: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-cobalt-100 bg-cobalt-50/60 p-3.5 text-sm leading-relaxed text-ink-2 sm:p-4 ${className}`} data-testid="in-simple-words">
      <p className="flex items-center gap-1.5 text-xs font-semibold tracking-wide text-cobalt-800 uppercase">
        <IconHelp size={14} />
        {title}
      </p>
      <div className="mt-1.5">{children}</div>
    </div>
  )
}
