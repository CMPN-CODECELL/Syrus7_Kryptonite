import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { IconChevronDown, IconInfo, IconX } from './icons'

/**
 * Tooltip on a real button: opens on hover and focus, closes on Escape/blur.
 * The tip text is also exposed via aria-describedby so screen readers get it.
 * The bubble is centred on the button and nudged sideways to stay inside the viewport.
 */
export function Tooltip({ content, label = 'More information', children }: { content: ReactNode; label?: string; children?: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [shift, setShift] = useState(0)
  const id = useId()
  const bubble = useRef<HTMLSpanElement>(null)
  useLayoutEffect(() => {
    const el = bubble.current
    if (!open || !el) return
    const r = el.getBoundingClientRect()
    const left = r.left - shift
    const right = r.right - shift
    const vw = document.documentElement.clientWidth
    setShift(left < 8 ? 8 - left : right > vw - 8 ? vw - 8 - right : 0)
    // `shift` is read only to undo the previous nudge; measuring again when it changes would loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  return (
    <span className="relative inline-flex align-middle" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        aria-label={label}
        aria-describedby={id}
        aria-expanded={open}
        className="-m-3 inline-flex min-h-11 min-w-11 items-center justify-center rounded-full text-faint hover:text-cobalt focus-visible:text-cobalt"
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((o) => !o)}
      >
        {children ?? <IconInfo size={15} />}
      </button>
      <span
        ref={bubble}
        id={id}
        role="tooltip"
        style={shift ? { marginLeft: shift } : undefined}
        className={`absolute bottom-full left-1/2 z-30 mb-1 w-64 max-w-[78vw] -translate-x-1/2 rounded-md bg-ink px-3 py-2 text-xs leading-relaxed font-normal text-white shadow-float ${
          open ? 'block' : 'hidden'
        }`}
      >
        {content}
      </span>
    </span>
  )
}

/** Disclosure built on <details>, so it works without JS and is keyboard accessible. */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  className = '',
  aside,
}: {
  summary: ReactNode
  children: ReactNode
  defaultOpen?: boolean
  className?: string
  aside?: ReactNode
}) {
  return (
    <details className={`group rounded-md border border-line bg-canvas ${className}`} open={defaultOpen || undefined}>
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm font-medium text-ink-2 select-none hover:bg-mist [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <IconChevronDown className="shrink-0 -rotate-90 text-muted transition-transform group-open:rotate-0" />
          <span className="min-w-0">{summary}</span>
        </span>
        {aside}
      </summary>
      <div className="border-t border-line px-3 py-3">{children}</div>
    </details>
  )
}

/** Modal dialog on native <dialog> (focus trap + Escape handled by the browser). */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean
  onClose: () => void
  title: ReactNode
  children: ReactNode
  footer?: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) {
      if (typeof d.showModal === 'function') d.showModal()
      else d.setAttribute('open', '')
    } else if (!open && d.open) d.close()
  }, [open])
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      className="qf-dialog m-auto w-[min(32rem,calc(100vw-2rem))] rounded-lg border border-line bg-canvas p-0 text-ink shadow-float"
    >
      <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
        <h2 id={titleId} className="text-base font-semibold">
          {title}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close dialog" className="-m-2 inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-muted hover:bg-mist hover:text-ink">
          <IconX />
        </button>
      </div>
      <div className="px-5 py-4 text-sm leading-relaxed text-ink-2">{children}</div>
      {footer ? <div className="flex flex-wrap justify-end gap-2 border-t border-line bg-mist px-5 py-3">{footer}</div> : null}
    </dialog>
  )
}
