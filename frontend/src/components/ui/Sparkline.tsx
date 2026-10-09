import { sparkPoints } from '../../lib/spark'

/** A small trend line. Decorative unless `label` is given; colour follows the first-to-last change. */
export function Sparkline({ values, width = 64, height = 20, label, className = '' }: { values: readonly number[]; width?: number; height?: number; label?: string; className?: string }) {
  const pts = sparkPoints(values, width, height)
  if (!pts) return <span className={`inline-block ${className}`} style={{ width, height }} aria-hidden="true" />
  const up = values[values.length - 1] >= values[0]
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={`shrink-0 overflow-visible ${className}`}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
    >
      <polyline points={pts} fill="none" stroke={up ? 'var(--color-teal-600)' : 'var(--color-danger)'} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
