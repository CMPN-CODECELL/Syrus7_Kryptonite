// The 3D hero (lazy chunk). Conceptual illustration only: a translucent "portfolio core",
// NIFTY 50 asset tiles on orbits, allocation bars and a return/risk curve. Selected assets
// (real Lab selection) leave their orbit and dock around the core. Nothing here depicts a
// circuit state or measured computation.

import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { memo, useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { ORBITS, ORBIT_MAX, dockSlot, pickHeroAssets, type HeroAsset } from './support'

const ORBIT_PER_RING = Math.ceil(ORBIT_MAX / ORBITS.length)

interface Shown {
  selected: string[] | null
  hover: string | null
  dock: Map<string, number>
  orbit: Map<string, number>
  nDock: number
}

export interface HeroSceneProps {
  assets: HeroAsset[]
  selected: string[]
  weights?: Record<string, number> | null
  hover?: string | null
  running: boolean
  compact?: boolean
  onLowPerf?: () => void
  onReady?: () => void
}

const COBALT = new THREE.Color('#1f48d6')
const TEAL = new THREE.Color('#0e8f8b')

// ---- Symbol atlas (one texture for all tiles) ---------------------------------------------
const ATLAS_COLS = 8
const CELL_W = 128
const CELL_H = 64

function buildAtlas(symbols: string[]): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = ATLAS_COLS * CELL_W
  c.height = ATLAS_COLS * CELL_H
  const g = c.getContext('2d')!
  g.clearRect(0, 0, c.width, c.height)
  g.fillStyle = '#ffffff'
  g.textAlign = 'center'
  g.textBaseline = 'middle'
  symbols.forEach((s, i) => {
    const col = i % ATLAS_COLS
    const row = Math.floor(i / ATLAS_COLS)
    let size = 30
    g.font = `600 ${size}px "Inter Variable", "Segoe UI", system-ui, sans-serif`
    while (g.measureText(s).width > CELL_W - 18 && size > 14) {
      size -= 2
      g.font = `600 ${size}px "Inter Variable", "Segoe UI", system-ui, sans-serif`
    }
    g.fillText(s, col * CELL_W + CELL_W / 2, row * CELL_H + CELL_H / 2 + 1)
  })
  const tex = new THREE.CanvasTexture(c)
  tex.anisotropy = 2
  tex.colorSpace = THREE.NoColorSpace
  tex.needsUpdate = true
  return tex
}

// Billboarded rounded tiles: face the camera so symbols stay legible while orbiting.
const tileVert = /* glsl */ `
  attribute float aIndex;
  attribute float aSel;
  attribute float aHover;
  uniform float uCols;
  varying vec2 vAtlas;
  varying vec2 vLocal;
  varying float vSel;
  varying float vHover;
  varying float vDepth;
  void main() {
    vec4 center = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
    float s = length(instanceMatrix[0].xyz);
    vec2 size = vec2(0.66, 0.31) * s;
    center.xy += position.xy * size;
    gl_Position = projectionMatrix * center;
    float col = mod(aIndex, uCols);
    float row = floor(aIndex / uCols);
    vAtlas = vec2((col + uv.x) / uCols, 1.0 - (row + 1.0 - uv.y) / uCols);
    vLocal = uv;
    vSel = aSel;
    vHover = aHover;
    vDepth = clamp((-center.z - 6.0) / 9.0, 0.0, 1.0);
  }
`

const tileFrag = /* glsl */ `
  uniform sampler2D uAtlas;
  uniform vec3 uTeal;
  uniform vec3 uCobalt;
  varying vec2 vAtlas;
  varying vec2 vLocal;
  varying float vSel;
  varying float vHover;
  varying float vDepth;
  float sdRound(vec2 p, vec2 b, float r) {
    vec2 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
  }
  void main() {
    vec2 p = (vLocal - 0.5) * vec2(2.13, 1.0);
    float d = sdRound(p, vec2(1.065, 0.5), 0.16);
    float aa = fwidth(d) * 1.2;
    float fill = 1.0 - smoothstep(-aa, aa, d);
    float border = smoothstep(-0.07 - aa, -0.07, d) * fill;
    vec3 base = mix(vec3(1.0), vec3(0.91, 0.965, 0.96), vSel);
    base = mix(base, vec3(0.937, 0.953, 0.996), vHover);
    vec3 edge = mix(vec3(0.80, 0.83, 0.88), uTeal, vSel);
    edge = mix(edge, uCobalt, vHover);
    float text = texture2D(uAtlas, vAtlas).a;
    vec3 ink = mix(vec3(0.33, 0.37, 0.44), mix(uTeal * 0.75, uCobalt, vHover), max(vSel, vHover));
    vec3 col = mix(base, edge, border);
    col = mix(col, ink, text * (1.0 - border));
    float alpha = fill * mix(0.92, 0.55, vDepth * (1.0 - vSel));
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(col, alpha);
  }
`

const Tiles = memo(function Tiles({ assets, selectedRef, hoverRef }: { assets: HeroAsset[]; selectedRef: React.RefObject<string[]>; hoverRef: React.RefObject<string | null> }) {
  const n = assets.length
  const mesh = useRef<THREE.InstancedMesh>(null)
  const state = useMemo(() => assets.map((a) => ({ symbol: a.symbol, pos: new THREE.Vector3(), sel: 0, hov: 0, vis: 0, init: false })), [assets])
  // The same subset as the static view: up to DOCK_MAX docked tiles and ORBIT_MAX on the orbits, so the
  // scene stays legible; recomputed only when the selection or hover changes.
  const shown = useRef<Shown>({ selected: null, hover: null, dock: new Map(), orbit: new Map(), nDock: 0 })
  const phase = useRef(ORBITS.map(() => 0))
  const { geometry, material } = useMemo(() => {
    const g = new THREE.PlaneGeometry(1, 1)
    g.setAttribute('aIndex', new THREE.InstancedBufferAttribute(new Float32Array(assets.map((_, i) => i)), 1))
    g.setAttribute('aSel', new THREE.InstancedBufferAttribute(new Float32Array(n), 1))
    g.setAttribute('aHover', new THREE.InstancedBufferAttribute(new Float32Array(n), 1))
    const m = new THREE.ShaderMaterial({
      vertexShader: tileVert,
      fragmentShader: tileFrag,
      uniforms: {
        uAtlas: { value: buildAtlas(assets.map((a) => a.symbol)) },
        uCols: { value: ATLAS_COLS },
        uTeal: { value: TEAL },
        uCobalt: { value: COBALT },
      },
      transparent: true,
      depthWrite: false,
    })
    return { geometry: g, material: m }
  }, [assets, n])

  useEffect(
    () => () => {
      geometry.dispose()
      ;(material.uniforms.uAtlas.value as THREE.Texture).dispose()
      material.dispose()
    },
    [geometry, material],
  )

  const tmp = useMemo(() => ({ m: new THREE.Matrix4(), q: new THREE.Quaternion(), s: new THREE.Vector3(), target: new THREE.Vector3(), e: new THREE.Euler() }), [])

  useFrame((_, rawDelta) => {
    const dt = Math.min(rawDelta, 0.05)
    const im = mesh.current
    if (!im) return
    const selected = selectedRef.current ?? []
    const hover = hoverRef.current
    if (shown.current.selected !== selected || shown.current.hover !== hover) {
      const pick = pickHeroAssets(assets, selected, hover)
      shown.current = { selected, hover, dock: new Map(pick.docked.map((s, i) => [s, i])), orbit: new Map(pick.orbit.map((s, i) => [s, i])), nDock: pick.docked.length }
    }
    const { dock, orbit, nDock } = shown.current
    ORBITS.forEach((o, r) => (phase.current[r] += o.speed * dt))
    const aSel = geometry.getAttribute('aSel') as THREE.InstancedBufferAttribute
    const aHover = geometry.getAttribute('aHover') as THREE.InstancedBufferAttribute
    const k = 1 - Math.exp(-dt * 3.2)
    state.forEach((t, i) => {
      const di = dock.get(t.symbol)
      const oi = orbit.get(t.symbol)
      if (di !== undefined) tmp.target.set(...dockSlot(di, nDock))
      else if (oi !== undefined) {
        // Orbit tiles are spread evenly by rank: ring oi % 3, slot oi / 3 on that ring.
        const ring = oi % ORBITS.length
        const o = ORBITS[ring]
        const a = (Math.floor(oi / ORBITS.length) / ORBIT_PER_RING) * Math.PI * 2 + ring * 0.7 + phase.current[ring]
        tmp.target.set(Math.cos(a) * o.radius, 0, Math.sin(a) * o.radius)
        tmp.e.set(o.tiltX, 0, o.tiltZ)
        tmp.target.applyEuler(tmp.e)
      } else tmp.target.copy(t.pos) // hidden tiles shrink away where they are
      const vis = di !== undefined || oi !== undefined ? 1 : 0
      if (!t.init) {
        t.pos.copy(tmp.target)
        t.vis = vis
        t.init = true
      } else t.pos.lerp(tmp.target, k)
      t.vis += (vis - t.vis) * k
      t.sel += ((di !== undefined ? 1 : 0) - t.sel) * k
      t.hov += ((hover === t.symbol ? 1 : 0) - t.hov) * Math.min(1, k * 2)
      tmp.s.setScalar((0.95 + t.hov * 0.08) * t.vis)
      tmp.m.compose(t.pos, tmp.q, tmp.s)
      im.setMatrixAt(i, tmp.m)
      aSel.setX(i, t.sel)
      aHover.setX(i, t.hov)
    })
    im.instanceMatrix.needsUpdate = true
    aSel.needsUpdate = true
    aHover.needsUpdate = true
  })

  if (n === 0) return null
  return <instancedMesh ref={mesh} args={[geometry, material, n]} frustumCulled={false} renderOrder={2} />
})

function Orbits() {
  const lines = useMemo(
    () =>
      ORBITS.map((o) => {
        const pts: THREE.Vector3[] = []
        const e = new THREE.Euler(o.tiltX, 0, o.tiltZ)
        for (let i = 0; i <= 160; i++) {
          const a = (i / 160) * Math.PI * 2
          pts.push(new THREE.Vector3(Math.cos(a) * o.radius, 0, Math.sin(a) * o.radius).applyEuler(e))
        }
        const g = new THREE.BufferGeometry().setFromPoints(pts)
        const m = new THREE.LineBasicMaterial({ color: COBALT, transparent: true, opacity: 0.22, depthWrite: false })
        return new THREE.Line(g, m)
      }),
    [],
  )
  useEffect(
    () => () =>
      lines.forEach((l) => {
        l.geometry.dispose()
        ;(l.material as THREE.Material).dispose()
      }),
    [lines],
  )
  // A small marker travels each orbit: a conceptual motif, not a qubit or measurement.
  const markers = useRef<Array<THREE.Mesh | null>>([])
  const phase = useRef([0, 2.1, 4.2])
  useFrame((_, d) => {
    const dt = Math.min(d, 0.05)
    ORBITS.forEach((o, i) => {
      phase.current[i] += dt * (0.22 + i * 0.05) * Math.sign(o.speed)
      const m = markers.current[i]
      if (!m) return
      m.position.set(Math.cos(phase.current[i]) * o.radius, 0, Math.sin(phase.current[i]) * o.radius).applyEuler(new THREE.Euler(o.tiltX, 0, o.tiltZ))
    })
  })
  return (
    <group>
      {lines.map((l, i) => (
        <primitive key={i} object={l} />
      ))}
      {ORBITS.map((_, i) => (
        <mesh
          key={i}
          ref={(el) => {
            markers.current[i] = el
          }}
        >
          <sphereGeometry args={[0.045, 12, 12]} />
          <meshBasicMaterial color={COBALT} transparent opacity={0.75} />
        </mesh>
      ))}
    </group>
  )
}

function Core({ countRef }: { countRef: React.RefObject<number> }) {
  const group = useRef<THREE.Group>(null)
  const inner = useRef<THREE.Mesh>(null)
  const pulse = useRef(0)
  const lastCount = useRef(0)
  const slabs = useMemo(() => [-0.48, -0.16, 0.16, 0.48], [])
  const slabGeo = useMemo(() => new THREE.BoxGeometry(1.5, 0.15, 1.5), [])
  const edgeGeo = useMemo(() => new THREE.EdgesGeometry(slabGeo), [slabGeo])
  useEffect(
    () => () => {
      slabGeo.dispose()
      edgeGeo.dispose()
    },
    [slabGeo, edgeGeo],
  )
  useFrame((_, d) => {
    const dt = Math.min(d, 0.05)
    const g = group.current
    if (g) g.rotation.y += dt * 0.12
    const c = countRef.current ?? 0
    if (c !== lastCount.current) {
      pulse.current = 1
      lastCount.current = c
    }
    pulse.current = Math.max(0, pulse.current - dt * 1.6)
    const m = inner.current
    if (m) {
      const s = 0.38 + Math.min(c, 20) * 0.012 + pulse.current * 0.08
      m.scale.setScalar(s)
      m.rotation.y -= dt * 0.3
      m.rotation.x = 0.6
    }
  })
  return (
    <group ref={group}>
      {slabs.map((y, i) => (
        <group key={y} position={[0, y, 0]} rotation={[0, i * 0.12, 0]}>
          <mesh geometry={slabGeo} renderOrder={1}>
            <meshPhysicalMaterial color="#e6ecff" transparent opacity={0.34} roughness={0.12} metalness={0.05} clearcoat={1} clearcoatRoughness={0.1} depthWrite={false} />
          </mesh>
          <lineSegments geometry={edgeGeo}>
            <lineBasicMaterial color={COBALT} transparent opacity={0.5} depthWrite={false} />
          </lineSegments>
        </group>
      ))}
      <mesh ref={inner}>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color={COBALT} emissive={COBALT} emissiveIntensity={0.25} roughness={0.35} metalness={0.1} />
      </mesh>
    </group>
  )
}

function AllocationBars({ weights }: { weights: Array<[string, number]> }) {
  const bars = useMemo(() => {
    const w = weights.filter(([, v]) => v > 0).slice(0, 12)
    return w.map(([s, v], i) => {
      const a = Math.PI * (0.18 + (0.64 * i) / Math.max(1, w.length - 1 || 1))
      return { s, h: Math.max(0.05, v * 2.4), x: Math.cos(a) * 1.25, z: Math.sin(a) * 1.25 }
    })
  }, [weights])
  if (!bars.length) return null
  return (
    <group position={[0, -1.15, 0]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, 0]}>
        <ringGeometry args={[0.95, 1.6, 48]} />
        <meshBasicMaterial color="#dfe6f3" transparent opacity={0.35} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {bars.map((b) => (
        <mesh key={b.s} position={[b.x, b.h / 2, b.z]}>
          <boxGeometry args={[0.1, b.h, 0.1]} />
          <meshStandardMaterial color={TEAL} roughness={0.5} />
        </mesh>
      ))}
    </group>
  )
}

function RiskReturnCurve() {
  const line = useMemo(() => {
    const pts: THREE.Vector3[] = []
    for (let i = 0; i <= 64; i++) {
      const t = i / 64
      const x = -2.8 + t * 5.6
      const y = -1.1 + Math.sqrt(t) * 2.3
      pts.push(new THREE.Vector3(x, y, -2.6))
    }
    const g = new THREE.BufferGeometry().setFromPoints(pts)
    return new THREE.Line(g, new THREE.LineBasicMaterial({ color: '#7c8db8', transparent: true, opacity: 0.35, depthWrite: false }))
  }, [])
  useEffect(
    () => () => {
      line.geometry.dispose()
      ;(line.material as THREE.Material).dispose()
    },
    [line],
  )
  return <primitive object={line} />
}

/** Fits the composition to the canvas aspect and applies hover tilt. */
function Rig({ compact, children }: { compact?: boolean; children: React.ReactNode }) {
  const group = useRef<THREE.Group>(null)
  const { camera, size } = useThree()
  useEffect(() => {
    const aspect = size.width / Math.max(1, size.height)
    const halfW = compact ? 3.0 : 3.9
    const halfH = compact ? 2.3 : 2.5
    const t = Math.tan(THREE.MathUtils.degToRad(36 / 2))
    const dist = Math.max(halfH, halfW / aspect) / t
    camera.position.set(0, dist * 0.2, dist)
    camera.lookAt(0, 0, 0)
  }, [camera, size, compact])
  useFrame((state, d) => {
    const g = group.current
    if (!g) return
    const k = 1 - Math.exp(-Math.min(d, 0.05) * 2.5)
    g.rotation.x += (state.pointer.y * 0.12 - g.rotation.x) * k
    g.rotation.z += (-state.pointer.x * 0.06 - g.rotation.z) * k
    g.rotation.y += (state.pointer.x * 0.18 - g.rotation.y) * k
  })
  return <group ref={group}>{children}</group>
}

/** Reports sustained slow frames so the stage can swap to the static fallback. */
function PerfProbe({ onLowPerf, onReady }: { onLowPerf?: () => void; onReady?: () => void }) {
  const samples = useRef<number[]>([])
  const frames = useRef(0)
  const done = useRef(false)
  useFrame((_, d) => {
    frames.current += 1
    if (frames.current === 2) onReady?.()
    if (done.current || frames.current < 30) return
    samples.current.push(d)
    if (samples.current.length >= 90) {
      done.current = true
      const sorted = [...samples.current].sort((a, b) => a - b)
      const median = sorted[Math.floor(sorted.length / 2)]
      if (median > 0.05) onLowPerf?.()
    }
  })
  return null
}

export default function HeroScene({ assets, selected, weights, hover, running, compact, onLowPerf, onReady }: HeroSceneProps) {
  // Props flow into refs so selection/hover changes never re-create the scene.
  const selectedRef = useRef<string[]>(selected)
  const hoverRef = useRef<string | null>(hover ?? null)
  const countRef = useRef(selected.length)
  useEffect(() => {
    selectedRef.current = selected
    hoverRef.current = hover ?? null
    countRef.current = selected.length
  }, [selected, hover])
  const weightsKey = weights ? JSON.stringify(Object.entries(weights).sort((a, b) => b[1] - a[1])) : ''
  // Parsed once per distinct weight set, so the bars only re-render when the allocation really changes.
  const weightList = useMemo(() => (weightsKey ? (JSON.parse(weightsKey) as Array<[string, number]>) : []), [weightsKey])
  // Tiles are keyed by the asset list identity (changes once when /api/assets loads).
  const assetsKey = assets.map((a) => a.symbol).join(',')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const stableAssets = useMemo(() => assets, [assetsKey])

  return (
    <Canvas
      dpr={[1, 1.5]}
      frameloop={running ? 'always' : 'never'}
      gl={{ antialias: true, alpha: true, powerPreference: 'low-power', preserveDrawingBuffer: false }}
      camera={{ fov: 36, near: 0.1, far: 60, position: [0, 2, 11] }}
      aria-hidden="true"
      style={{ touchAction: 'pan-y' }}
    >
      <ambientLight intensity={0.95} />
      <directionalLight position={[4, 6, 5]} intensity={1.5} />
      <hemisphereLight args={['#ffffff', '#dbe3f3', 0.5]} />
      <Rig compact={compact}>
        {!compact ? <RiskReturnCurve /> : null}
        <Orbits />
        <Core countRef={countRef} />
        <AllocationBars weights={weightList} />
        <Tiles assets={stableAssets} selectedRef={selectedRef} hoverRef={hoverRef} />
      </Rig>
      <PerfProbe onLowPerf={onLowPerf} onReady={onReady} />
    </Canvas>
  )
}
