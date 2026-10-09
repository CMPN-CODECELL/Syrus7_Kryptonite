/** SVG polyline points for a sparkline of `values` in a w x h box (1 px inset so the stroke is not clipped). */
export function sparkPoints(values: readonly number[], w: number, h: number, x0 = 0, y0 = 0): string {
  const v = values.filter((n) => Number.isFinite(n))
  if (v.length < 2) return ''
  const lo = Math.min(...v)
  const hi = Math.max(...v)
  const span = hi - lo || 1
  const step = (w - 2) / (v.length - 1)
  return v.map((n, i) => `${(x0 + 1 + i * step).toFixed(1)},${(y0 + 1 + (h - 2) * (1 - (n - lo) / span)).toFixed(1)}`).join(' ')
}
