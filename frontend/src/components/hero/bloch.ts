// Shared geometry for the Bloch-sphere hero, so the still SVG and the three.js scene draw the same picture from the
// same camera. A qubit's state is an arrow on the unit sphere: |0> (skip) points up, |1> (buy) points down, and a
// superposition lies between, for example on the equator.

/** Camera: vertical field of view (degrees), distance from the centre, and height above the equator (degrees). */
export const CAMERA = { fov: 26, distance: 5.8, elevation: 16 } as const

/** Where the drawing's vertical axis ends, as a fraction of the sphere's radius. */
export const AXIS_END = 1.22

/** Starting turn of the sphere about its vertical axis (radians), shared by the still and the first 3D frame. */
export const START_TURN = 0.35

export type Vec3 = readonly [number, number, number]

/** The arrow for polar angle theta (0 = |0>, pi = |1>) and azimuth phi, in a y-up frame. */
export function blochDir(theta: number, phi: number): Vec3 {
  return [Math.sin(theta) * Math.cos(phi), Math.cos(theta), Math.sin(theta) * Math.sin(phi)]
}

/** Turns a point about the vertical axis. */
export function turnY([x, y, z]: Vec3, a: number): Vec3 {
  const c = Math.cos(a)
  const s = Math.sin(a)
  return [x * c + z * s, y, -x * s + z * c]
}

/**
 * Projects a point to pixels in a square view of `size`, matching a three.js PerspectiveCamera with CAMERA's settings
 * looking at the origin. Returns depth too (larger is farther), for fading the back of the sphere.
 */
export function project(p: Vec3, size: number): { x: number; y: number; depth: number } {
  const e = (CAMERA.elevation * Math.PI) / 180
  const cy = CAMERA.distance * Math.sin(e)
  const cz = CAMERA.distance * Math.cos(e)
  // View basis: right = +x; up and forward tilted by the elevation.
  const vx = p[0]
  const vy = p[1] - cy
  const vz = p[2] - cz
  const upY = Math.cos(e)
  const upZ = -Math.sin(e)
  const fY = -Math.sin(e)
  const fZ = -Math.cos(e)
  const xc = vx
  const yc = vy * upY + vz * upZ
  const zc = vy * fY + vz * fZ
  const t = Math.tan((CAMERA.fov * Math.PI) / 360)
  return { x: ((xc / (zc * t) + 1) / 2) * size, y: ((1 - yc / (zc * t)) / 2) * size, depth: zc }
}

/** Points on a circle of latitude (polar angle theta), turned by `turn`. */
export function latitude(theta: number, turn = 0, steps = 96): Vec3[] {
  return Array.from({ length: steps + 1 }, (_, i) => turnY(blochDir(theta, (i / steps) * Math.PI * 2), turn))
}

/** Points on a meridian (a great circle through both poles) at azimuth phi, turned by `turn`. */
export function meridian(phi: number, turn = 0, steps = 96): Vec3[] {
  return Array.from({ length: steps + 1 }, (_, i) => turnY(blochDir((i / steps) * Math.PI * 2, phi), turn))
}

/** The resting state: an even superposition on the equator, pointing towards the viewer's right. */
export const IDLE = { theta: Math.PI / 2, phi: 0.95 } as const

/** Half the width of the sphere's outline as a fraction of the view, for the still drawing. */
export function outlineRadius(): number {
  const a = Math.asin(1 / CAMERA.distance)
  return Math.tan(a) / Math.tan((CAMERA.fov * Math.PI) / 360) / 2
}
