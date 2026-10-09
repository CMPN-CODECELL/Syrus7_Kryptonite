import { describe, expect, it } from 'vitest'
import { AXIS_END, blochDir, latitude, meridian, outlineRadius, project, turnY } from './bloch'

const near = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) < eps

describe('Bloch sphere geometry', () => {
  it('puts |0> at the top pole and |1> at the bottom', () => {
    const up = blochDir(0, 1.3)
    const down = blochDir(Math.PI, 1.3)
    expect(near(up[1], 1) && near(down[1], -1)).toBe(true)
    expect(Math.hypot(...blochDir(Math.PI / 2, 0.7))).toBeCloseTo(1, 12)
  })

  it('turns about the vertical axis without changing length or height', () => {
    const p = turnY([0.6, 0.48, 0.64], 1.1)
    expect(Math.hypot(...p)).toBeCloseTo(1, 12)
    expect(p[1]).toBeCloseTo(0.48, 12)
  })

  it('projects the centre to the middle of the view and keeps both poles inside it', () => {
    const c = project([0, 0, 0], 200)
    expect(c.x).toBeCloseTo(100, 9)
    expect(c.y).toBeCloseTo(100, 9)
    const top = project([0, AXIS_END, 0], 1)
    const bottom = project([0, -AXIS_END, 0], 1)
    expect(top.y).toBeGreaterThan(0.02)
    expect(top.y).toBeLessThan(c.y / 200)
    expect(bottom.y).toBeLessThan(0.98)
    expect(bottom.y).toBeGreaterThan(c.y / 200)
  })

  it('draws every grid line inside the outline of the sphere', () => {
    const r = outlineRadius() * 200
    expect(r).toBeGreaterThan(40)
    expect(r).toBeLessThan(100)
    for (const p of [...latitude(Math.PI / 3, 0.35), ...meridian(0.5, 0.35)]) {
      const { x, y } = project(p, 200)
      expect(Math.hypot(x - 100, y - 100)).toBeLessThanOrEqual(r + 1e-6)
    }
  })

  it('shows nearer points as nearer', () => {
    expect(project([0, 0, 1], 100).depth).toBeLessThan(project([0, 0, -1], 100).depth)
  })
})
