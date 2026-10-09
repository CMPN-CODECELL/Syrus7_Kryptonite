import type { ReactNode } from 'react'
import { STATUS_LABEL } from '../../lib/format'
import { IconAlert, IconCheck, IconClock, IconInfo, IconMinus, IconSpinner, IconStop, IconX } from './icons'

export type Tone = 'neutral' | 'cobalt' | 'teal' | 'amber' | 'danger'

const tones: Record<Tone, string> = {
  neutral: 'bg-mist-2 text-ink-2 ring-line',
  cobalt: 'bg-cobalt-50 text-cobalt-800 ring-cobalt-200',
  teal: 'bg-teal-50 text-teal ring-teal-100',
  amber: 'bg-amber-50 text-amber ring-amber-200',
  danger: 'bg-danger-50 text-danger ring-danger-200',
}

export function Badge({ tone = 'neutral', icon, children, className = '', title }: { tone?: Tone; icon?: ReactNode; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={`inline-flex max-w-full items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset ${tones[tone]} ${className}`}>
      {icon}
      <span className="truncate">{children}</span>
    </span>
  )
}

type StatusSpec = { tone: Tone; icon: ReactNode }

const STATUS: Record<string, StatusSpec> = {
  queued: { tone: 'neutral', icon: <IconClock size={13} /> },
  running: { tone: 'cobalt', icon: <IconSpinner size={13} /> },
  succeeded: { tone: 'teal', icon: <IconCheck size={13} /> },
  failed: { tone: 'danger', icon: <IconX size={13} /> },
  cancelled: { tone: 'neutral', icon: <IconStop size={13} /> },
  timed_out: { tone: 'amber', icon: <IconClock size={13} /> },
  ok: { tone: 'teal', icon: <IconCheck size={13} /> },
  infeasible: { tone: 'danger', icon: <IconX size={13} /> },
  no_feasible_sample: { tone: 'amber', icon: <IconAlert size={13} /> },
  no_feasible_found: { tone: 'amber', icon: <IconAlert size={13} /> },
  unsupported: { tone: 'neutral', icon: <IconMinus size={13} /> },
  time_limited: { tone: 'amber', icon: <IconClock size={13} /> },
  skipped: { tone: 'neutral', icon: <IconMinus size={13} /> },
  error: { tone: 'danger', icon: <IconX size={13} /> },
  warning: { tone: 'amber', icon: <IconAlert size={13} /> },
  info: { tone: 'cobalt', icon: <IconInfo size={13} /> },
}

/** Status is always icon + text, never colour alone. */
export function StatusChip({ status, label, className = '' }: { status: string; label?: string; className?: string }) {
  const spec = STATUS[status] ?? { tone: 'neutral' as Tone, icon: <IconInfo size={13} /> }
  return (
    <Badge tone={spec.tone} icon={spec.icon} className={className}>
      {label ?? STATUS_LABEL[status] ?? status.replace(/_/g, ' ')}
    </Badge>
  )
}
