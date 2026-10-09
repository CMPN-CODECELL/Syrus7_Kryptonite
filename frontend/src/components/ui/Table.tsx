import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'

export interface Column<R> {
  key: string
  header: ReactNode
  cell: (row: R, index: number) => ReactNode
  align?: 'left' | 'right' | 'center'
  /** Render as row header (<th scope="row">). */
  rowHeader?: boolean
  className?: string
}

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect

/** Whether the scroll container has hidden columns to its left or right. */
function useScrollEdges() {
  const ref = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ left: false, right: false })
  const update = useCallback(() => {
    const el = ref.current
    if (!el) return
    const left = el.scrollLeft > 1
    const right = el.scrollWidth - el.clientWidth - el.scrollLeft > 1
    setEdges((p) => (p.left === left && p.right === right ? p : { left, right }))
  }, [])
  useIsoLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    update()
    el.addEventListener('scroll', update, { passive: true })
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update)
    ro?.observe(el)
    if (el.firstElementChild) ro?.observe(el.firstElementChild)
    return () => {
      el.removeEventListener('scroll', update)
      ro?.disconnect()
    }
  }, [update])
  return { ref, edges }
}

/**
 * Semantic data table inside a horizontally scrollable, focusable region so wide tables never cause
 * page overflow on small screens. When columns are hidden the region says so: an edge fade, a visible
 * "scroll for more columns" hint, and an accessible name that mentions the horizontal scroll. A leading
 * row-header column stays pinned while the rest scrolls.
 */
export function Table<R>({
  columns,
  rows,
  caption,
  captionHidden = false,
  rowKey,
  empty = 'No rows.',
  dense = false,
  rowClassName,
  footer,
  stickyFirst,
}: {
  columns: Array<Column<R>>
  rows: R[]
  caption: ReactNode
  captionHidden?: boolean
  rowKey: (row: R, index: number) => string
  empty?: ReactNode
  dense?: boolean
  rowClassName?: (row: R) => string
  footer?: ReactNode
  /** Pin the first column while scrolling; defaults to true when it is a row header. */
  stickyFirst?: boolean
}) {
  const { ref, edges } = useScrollEdges()
  const overflow = edges.left || edges.right
  const pin = (stickyFirst ?? columns[0]?.rowHeader === true) && overflow
  const pad = dense ? 'px-2.5 py-1.5' : 'px-3 py-2'
  const align = (a?: string) => (a === 'right' ? 'text-right' : a === 'center' ? 'text-center' : 'text-left')
  // The pinned cell paints an opaque background (with a tinted row's colour laid over it)
  // and a hairline shadow once columns have scrolled beneath it.
  const pinned = (i: number, bg: string) =>
    pin && i === 0 ? `sticky left-0 z-[1] ${bg} ${edges.left ? 'shadow-[1px_0_0_var(--color-line),6px_0_8px_-6px_rgb(15_23_42/0.18)]' : 'shadow-[1px_0_0_var(--color-line)]'}` : ''
  const label = typeof caption === 'string' ? caption : 'Data table'
  return (
    <div className="max-w-full">
      <div className="relative">
        <div
          ref={ref}
          className="max-w-full overflow-x-auto rounded-md border border-line"
          tabIndex={0}
          role="region"
          aria-label={overflow ? `${label}, scrolls horizontally` : label}
        >
          <table className="w-full border-separate border-spacing-0 text-sm tabular">
            <caption className={captionHidden ? 'sr-only' : 'border-b border-line bg-mist px-3 py-2 text-left text-xs font-medium text-muted'}>{caption}</caption>
            <thead className="bg-mist">
              <tr>
                {columns.map((c, i) => (
                  <th
                    key={c.key}
                    scope="col"
                    className={`${pad} border-b border-line text-xs font-semibold whitespace-nowrap text-muted ${align(c.align)} ${pinned(i, 'bg-mist')} ${c.className ?? ''}`}
                  >
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="[&>tr:not(:last-child)>*]:border-b [&>tr>*]:border-line">
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={columns.length} className="px-3 py-6 text-center text-sm text-muted">
                    {empty}
                  </td>
                </tr>
              ) : (
                rows.map((r, i) => {
                  const tint = rowClassName?.(r) ?? ''
                  return (
                    <tr key={rowKey(r, i)} className={tint}>
                      {columns.map((c, ci) =>
                        c.rowHeader ? (
                          <th key={c.key} scope="row" className={`${pad} font-medium whitespace-nowrap text-ink ${align(c.align)} ${pinned(ci, 'bg-canvas')} ${c.className ?? ''}`}>
                            {pin && ci === 0 && tint ? <span className={`pointer-events-none absolute inset-0 ${tint}`} aria-hidden="true" /> : null}
                            <span className="relative">{c.cell(r, i)}</span>
                          </th>
                        ) : (
                          <td key={c.key} className={`${pad} text-ink-2 ${align(c.align)} ${pinned(ci, 'bg-canvas')} ${c.className ?? ''}`}>
                            {c.cell(r, i)}
                          </td>
                        ),
                      )}
                    </tr>
                  )
                })
              )}
            </tbody>
            {footer ? <tfoot className="bg-mist [&>tr:first-child>*]:border-t [&>tr>*]:border-line">{footer}</tfoot> : null}
          </table>
        </div>
        {edges.right ? <div className="pointer-events-none absolute inset-y-px right-px w-10 rounded-r-md bg-gradient-to-l from-canvas to-transparent" aria-hidden="true" /> : null}
      </div>
      {overflow ? (
        <p className="mt-1.5 text-right text-xs text-muted" aria-hidden="true">
          {edges.right ? 'Scroll for more columns →' : '← Scroll back for the first columns'}
        </p>
      ) : null}
    </div>
  )
}
