// Static SVG version of the hero, used without WebGL, with reduced motion, on slow devices,
// and as the first paint while the 3D chunk loads. Tiles sit in fixed, evenly spaced slots on two
// ellipses (see flatSlot), so they never overlap however many assets are selected.

import { memo, useMemo } from 'react'
import { FLAT, flatSlot, pickHeroAssets, type HeroAsset } from './support'

type V3 = [number, number, number]

const VIEW_TILT = 0.24
const W = 800
const H = 560
const S = 92
const CX = FLAT.cx
const CY = FLAT.cy

function project([x, y, z]: V3): { sx: number; sy: number; depth: number } {
  const c = Math.cos(VIEW_TILT)
  const s = Math.sin(VIEW_TILT)
  const yv = y * c - z * s
  const zv = y * s + z * c
  return { sx: CX + x * S, sy: CY - yv * S, depth: zv }
}

export interface HeroFallbackProps {
  assets: HeroAsset[]
  selected: string[]
  weights?: Record<string, number> | null
  hover?: string | null
  compact?: boolean
}

interface Tile {
  key: string
  label: string
  x: number
  y: number
  kind: 'docked' | 'orbit' | 'more'
}

export const HeroFallback = memo(function HeroFallback({ assets, selected, weights, hover, compact }: HeroFallbackProps) {
  const pick = useMemo(() => pickHeroAssets(assets, selected, hover), [assets, selected, hover])

  const tiles = useMemo(() => {
    const out: Tile[] = []
    const nDock = pick.docked.length + (pick.more ? 1 : 0)
    pick.docked.forEach((s, k) => out.push({ key: s, label: s, ...flatSlot('dock', k, nDock), kind: 'docked' }))
    if (pick.more) out.push({ key: '+more', label: `+${pick.more}`, ...flatSlot('dock', nDock - 1, nDock), kind: 'more' })
    if (!compact) pick.orbit.forEach((s, k) => out.push({ key: s, label: s, ...flatSlot('orbit', k, pick.orbit.length), kind: 'orbit' }))
    return out
  }, [pick, compact])

  const slabs = useMemo(
    () =>
      [-0.48, -0.16, 0.16, 0.48].map((y, i) => {
        const rot = 0.55 + i * 0.12
        const corners: V3[] = [
          [-0.75, y + 0.075, -0.75],
          [0.75, y + 0.075, -0.75],
          [0.75, y + 0.075, 0.75],
          [-0.75, y + 0.075, 0.75],
        ].map(([x, yy, z]) => [x * Math.cos(rot) - z * Math.sin(rot), yy, x * Math.sin(rot) + z * Math.cos(rot)] as V3)
        const top = corners.map(project)
        const bottom = corners.map(([x, yy, z]) => project([x, yy - 0.15, z]))
        // front faces: edges whose midpoint is nearest the viewer
        const faces = [0, 1, 2, 3]
          .map((k) => {
            const n = (k + 1) % 4
            return { k, n, depth: (top[k].depth + top[n].depth) / 2 }
          })
          .sort((a, b) => b.depth - a.depth)
          .slice(0, 2)
          .map(({ k, n }) => `M${top[k].sx},${top[k].sy}L${top[n].sx},${top[n].sy}L${bottom[n].sx},${bottom[n].sy}L${bottom[k].sx},${bottom[k].sy}Z`)
        return { top: top.map((p) => `${p.sx.toFixed(1)},${p.sy.toFixed(1)}`).join(' '), faces }
      }),
    [],
  )

  const bars = useMemo(() => {
    const w = Object.entries(weights ?? {})
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 12)
    return w.map(([s, v], i) => {
      const a = Math.PI * (0.18 + (0.64 * i) / Math.max(1, w.length - 1 || 1))
      const base = project([Math.cos(a) * 1.25, -1.15, Math.sin(a) * 1.25])
      return { s, x: base.sx, y: base.sy, h: Math.max(0.05, v * 2.4) * S * Math.cos(VIEW_TILT) }
    })
  }, [weights])

  const curve = useMemo(() => {
    const pts: string[] = []
    for (let i = 0; i <= 48; i++) {
      const t = i / 48
      const p = project([-2.8 + t * 5.6, -1.1 + Math.sqrt(t) * 2.3, -2.6])
      pts.push(`${p.sx.toFixed(1)},${p.sy.toFixed(1)}`)
    }
    return `M${pts.join('L')}`
  }, [])

  // Orbit tiles on the upper half pass behind the core; everything else is drawn in front of it.
  const back = tiles.filter((t) => t.kind === 'orbit' && t.y < CY)
  const front = tiles.filter((t) => !(t.kind === 'orbit' && t.y < CY))

  const tile = (t: Tile) => {
    const isHover = hover === t.key
    const docked = t.kind === 'docked'
    const more = t.kind === 'more'
    const w = FLAT.tileW
    const h = FLAT.tileH
    return (
      <g key={t.key} transform={`translate(${t.x.toFixed(1)} ${t.y.toFixed(1)})`} opacity={docked || more || isHover ? 1 : t.y < CY ? 0.72 : 0.9}>
        <rect
          x={-w / 2}
          y={-h / 2}
          width={w}
          height={h}
          rx={7}
          fill={isHover ? '#eff3fe' : docked ? '#e8f6f5' : '#ffffff'}
          stroke={isHover ? '#1f48d6' : docked || more ? '#0e8f8b' : '#cbd3df'}
          strokeWidth={docked || isHover ? 1.6 : 1.1}
          strokeDasharray={more ? '4 3' : undefined}
        />
        <text
          y={0.5}
          textAnchor="middle"
          dominantBaseline="middle"
          fontSize={Math.min(12.5, 68 / (Math.max(4, t.label.length) * 0.6))}
          fontWeight={600}
          fill={isHover ? '#1f48d6' : docked || more ? '#0b5f5c' : '#525d6e'}
        >
          {t.label}
        </text>
      </g>
    )
  }

  const coreSize = 34 + Math.min(selected.length, 20) * 1.2

  return (
    <svg viewBox={compact ? '130 90 540 380' : `0 0 ${W} ${H}`} className="h-full w-full" aria-hidden="true" focusable="false" style={{ fontFamily: 'var(--font-sans)' }}>
      <defs>
        <linearGradient id="qf-core" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#f4f7ff" stopOpacity="0.9" />
          <stop offset="1" stopColor="#dfe7ff" stopOpacity="0.55" />
        </linearGradient>
      </defs>
      {!compact ? <path d={curve} fill="none" stroke="#7c8db8" strokeOpacity={0.35} strokeWidth={1.4} /> : null}
      {!compact ? <ellipse cx={CX} cy={CY} rx={FLAT.orbit.rx} ry={FLAT.orbit.ry} fill="none" stroke="#1f48d6" strokeOpacity={0.18} strokeWidth={1.1} /> : null}
      <ellipse cx={CX} cy={CY} rx={255} ry={92} fill="none" stroke="#1f48d6" strokeOpacity={0.14} strokeWidth={1} transform={`rotate(-12 ${CX} ${CY})`} />
      <ellipse cx={CX} cy={CY} rx={FLAT.dock.rx} ry={FLAT.dock.ry} fill="none" stroke="#0e8f8b" strokeOpacity={0.3} strokeWidth={1.1} strokeDasharray="3 5" />
      {back.map(tile)}
      <g>
        {bars.length ? (
          <ellipse cx={CX} cy={project([0, -1.16, 0]).sy} rx={1.6 * S} ry={1.6 * S * Math.sin(VIEW_TILT)} fill="#dfe6f3" fillOpacity={0.35} />
        ) : null}
        {slabs.map((s, i) => (
          <g key={i}>
            {s.faces.map((f, j) => (
              <path key={j} d={f} fill="#dde5fd" fillOpacity={0.4} stroke="#1f48d6" strokeOpacity={0.45} strokeWidth={1} />
            ))}
            <polygon points={s.top} fill="url(#qf-core)" stroke="#1f48d6" strokeOpacity={0.55} strokeWidth={1} />
          </g>
        ))}
        <rect x={CX - coreSize / 2} y={CY - coreSize / 2} width={coreSize} height={coreSize} rx={5} fill="#1f48d6" opacity={0.9} transform={`rotate(45 ${CX} ${CY})`} />
        {bars.map((b) => (
          <rect key={b.s} x={b.x - 4} y={b.y - b.h} width={8} height={b.h} rx={1.5} fill="#0e8f8b" />
        ))}
      </g>
      {front.map(tile)}
    </svg>
  )
})
