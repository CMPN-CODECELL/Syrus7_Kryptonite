import type { ReactNode } from 'react'
import { isApiError } from '../../api/client'
import { Button } from './Button'
import { IconAlert, IconRefresh, IconWifiOff } from './icons'

export function Skeleton({ className = '', label }: { className?: string; label?: string }) {
  return <div className={`qf-skeleton rounded-md ${className}`} role={label ? 'status' : undefined} aria-label={label} aria-hidden={label ? undefined : true} />
}

export function LoadingBlock({ label = 'Loading…', rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-3">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={`h-5 ${i % 3 === 2 ? 'w-2/3' : 'w-full'}`} />
      ))}
    </div>
  )
}

export function EmptyState({ title, children, action, icon, className = '' }: { title: ReactNode; children?: ReactNode; action?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={`flex flex-col items-center gap-3 rounded-lg border border-dashed border-line-strong bg-mist px-6 py-10 text-center ${className}`}>
      {icon ? <div className="text-faint">{icon}</div> : null}
      <h3 className="text-base font-semibold text-ink">{title}</h3>
      {children ? <div className="max-w-lg text-sm leading-relaxed text-muted">{children}</div> : null}
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  title,
  onRetry,
  children,
  className = '',
}: {
  error?: unknown
  title?: ReactNode
  onRetry?: () => void
  children?: ReactNode
  className?: string
}) {
  const offline = (isApiError(error) && error.isNetwork) || (typeof navigator !== 'undefined' && navigator.onLine === false)
  const message = isApiError(error) ? error.message : error instanceof Error ? error.message : null
  return (
    <div role="alert" className={`flex flex-col gap-3 rounded-lg border border-danger-200 bg-danger-50 p-4 sm:flex-row sm:items-start ${className}`}>
      <div className="mt-0.5 shrink-0 text-danger">{offline ? <IconWifiOff size={20} /> : <IconAlert size={20} />}</div>
      <div className="min-w-0 flex-1">
        <h3 className="text-sm font-semibold text-ink">{title ?? (offline ? 'Cannot reach the API' : 'Something went wrong')}</h3>
        {message ? <p className="mt-1 text-sm break-words text-ink-2">{message}</p> : null}
        {isApiError(error) && error.code && !error.isNetwork ? (
          <p className="mt-1 text-xs text-muted tabular">
            code {error.code}
            {error.requestId ? ` · request ${error.requestId}` : ''}
          </p>
        ) : null}
        {children ? <div className="mt-1 text-sm text-ink-2">{children}</div> : null}
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" icon={<IconRefresh />} onClick={onRetry} className="self-start">
          Try again
        </Button>
      ) : null}
    </div>
  )
}

export function Notice({ tone = 'info', title, children, icon, className = '' }: { tone?: 'info' | 'warning' | 'danger' | 'success'; title?: ReactNode; children?: ReactNode; icon?: ReactNode; className?: string }) {
  const cls = {
    info: 'border-cobalt-100 bg-cobalt-50 text-cobalt-800',
    warning: 'border-amber-200 bg-amber-50 text-amber',
    danger: 'border-danger-200 bg-danger-50 text-danger',
    success: 'border-teal-100 bg-teal-50 text-teal',
  }[tone]
  return (
    <div className={`flex gap-2.5 rounded-md border px-3 py-2.5 text-sm ${cls} ${className}`}>
      {icon ? <div className="mt-0.5 shrink-0">{icon}</div> : null}
      <div className="min-w-0 text-ink-2">
        {title ? <p className="font-semibold text-ink">{title}</p> : null}
        {children ? <div className={title ? 'mt-0.5' : ''}>{children}</div> : null}
      </div>
    </div>
  )
}
