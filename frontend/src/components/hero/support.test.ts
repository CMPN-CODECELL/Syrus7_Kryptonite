import { describe, expect, it } from 'vitest'
import { DOCK_MAX, FLAT, ORBIT_MAX, flatSlot, pickHeroAssets, type HeroAsset } from './support'

const assets: HeroAsset[] = Array.from({ length: 50 }, (_, i) => ({ symbol: `SYM${String(i).padStart(2, '0')}`, sector: 'X' }))

interface Rect {
  x0: number
  y0: number
  x1: number
  y1: number
}

function rect({ x, y }: { x: number; y: number }): Rect {
  return { x0: x - FLAT.tileW / 2, y0: y - FLAT.tileH / 2, x1: x + FLAT.tileW / 2, y1: y + FLAT.tileH / 2 }
}

const overlaps = (a: Rect, b: Rect) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1

describe('static hero layout', () => {
  it('never overlaps two tiles, for any number of docked tiles', () => {
    const orbit = Array.from({ length: ORBIT_MAX }, (_, k) => rect(flatSlot('orbit', k, ORBIT_MAX)))
    for (let n = 0; n <= DOCK_MAX; n++) {
      const dock = Array.from({ length: n }, (_, k) => rect(flatSlot('dock', k, n)))
      const all = [...dock, ...orbit]
      for (let i = 0; i < all.length; i++) {
        const r = all[i]
        expect(r.x0 >= 0 && r.y0 >= 0 && r.x1 <= 800 && r.y1 <= 560, `tile ${i} of ${n} docked lies inside the full view`).toBe(true)
        for (let j = i + 1; j < all.length; j++) expect(overlaps(r, all[j]), `tiles ${i} and ${j} with ${n} docked`).toBe(false)
      }
      // The compact view (viewBox 130 90 540 380) shows the docked tiles only; they must fit it.
      for (const r of dock) expect(r.x0 >= 130 && r.y0 >= 90 && r.x1 <= 670 && r.y1 <= 470).toBe(true)
    }
  })
})

describe('pickHeroAssets', () => {
  it('docks the selection and fills the orbits with the rest', () => {
    const p = pickHeroAssets(assets, ['SYM03', 'SYM10'])
    expect(p.docked).toEqual(['SYM03', 'SYM10'])
    expect(p.more).toBe(0)
    expect(p.orbit).toHaveLength(ORBIT_MAX)
    expect(p.orbit).not.toContain('SYM03')
  })

  it('summarises a large selection as "+N"', () => {
    const sel = assets.slice(0, 14).map((a) => a.symbol)
    const p = pickHeroAssets(assets, sel)
    expect(p.docked).toHaveLength(DOCK_MAX - 1)
    expect(p.more).toBe(14 - (DOCK_MAX - 1))
  })

  it('always shows the hovered asset', () => {
    const sel = assets.slice(0, 14).map((a) => a.symbol)
    expect(pickHeroAssets(assets, sel, 'SYM13').docked).toContain('SYM13')
    expect(pickHeroAssets(assets, [], 'SYM40').orbit).toContain('SYM40')
  })

  it('ignores symbols outside the universe', () => {
    expect(pickHeroAssets(assets, ['NOPE', 'SYM01']).docked).toEqual(['SYM01'])
  })
})
