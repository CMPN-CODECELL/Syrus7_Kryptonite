// The 3D Bloch sphere for the landing hero, in plain three.js. Loaded on demand, so three.js never weighs on the
// first paint. One qubit: the arrow spins on the equator while it is "measured", then snaps to |0> (skip, up) or
// |1> (buy, down) with a ripple at that pole. Drag to turn the sphere. Rendering pauses whenever the sphere is off
// screen or the tab is hidden, and everything is released on dispose.

import {
  AmbientLight,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  CircleGeometry,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DirectionalLight,
  DoubleSide,
  Group,
  LineBasicMaterial,
  LineDashedMaterial,
  LineSegments,
  Material,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  NormalBlending,
  Object3D,
  PerspectiveCamera,
  Points,
  Quaternion,
  RingGeometry,
  Scene,
  ShaderMaterial,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  Texture,
  Vector3,
  WebGLRenderer,
} from 'three'
import { AXIS_END, CAMERA, IDLE, START_TURN, blochDir, latitude, meridian, type Vec3 } from './bloch'

export type QubitPose = 'idle' | 'measure' | '0' | '1'

export interface BlochScene {
  setPose(pose: QubitPose): void
  dispose(): void
}

export interface BlochSceneOptions {
  host: HTMLElement
  pose: QubitPose
  /** The first frame is on screen. */
  onReady: () => void
  /** Frames are consistently too slow on this device. */
  onSlow: () => void
  /** The WebGL context was lost. */
  onError: () => void
}

function token(el: HTMLElement, name: string, fallback: string): Color {
  const v = getComputedStyle(el).getPropertyValue(name).trim()
  try {
    return new Color(v || fallback)
  } catch {
    return new Color(fallback)
  }
}

/** Lines that fade where they pass behind the sphere, so the front reads clearly. */
function wireMaterial(color: Color): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { uColor: { value: color } },
    vertexShader: /* glsl */ `
      attribute float aAlpha;
      varying float vAlpha;
      varying float vFace;
      void main() {
        vec4 wp = modelMatrix * vec4(position, 1.0);
        vFace = dot(normalize(wp.xyz), normalize(cameraPosition));
        vAlpha = aAlpha;
        gl_Position = projectionMatrix * viewMatrix * wp;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying float vAlpha;
      varying float vFace;
      void main() {
        float k = mix(0.25, 1.0, smoothstep(-0.3, 0.3, vFace));
        gl_FragColor = vec4(uColor, vAlpha * k);
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
  })
}

/** Glass: nearly clear in the middle, tinted towards the rim, with one small highlight. */
function glassMaterial(tint: Color, rim: Color): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { uTint: { value: tint }, uRim: { value: rim } },
    vertexShader: /* glsl */ `
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        vN = normalize(normalMatrix * normal);
        vV = normalize(-mv.xyz);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uTint;
      uniform vec3 uRim;
      varying vec3 vN;
      varying vec3 vV;
      void main() {
        vec3 n = normalize(vN);
        float rim = pow(1.0 - clamp(dot(n, normalize(vV)), 0.0, 1.0), 2.3);
        float glint = pow(max(dot(n, normalize(vec3(-0.45, 0.62, 0.64))), 0.0), 48.0);
        vec3 col = mix(mix(uTint, uRim, rim), vec3(1.0), glint * 0.9);
        float a = max(0.06 + 0.44 * rim, glint * 0.65);
        gl_FragColor = vec4(col, a);
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
  })
}

/** Round, fading dots behind the arrow's tip. */
function trailMaterial(color: Color, dpr: number): ShaderMaterial {
  return new ShaderMaterial({
    uniforms: { uColor: { value: color }, uSize: { value: 7.0 }, uDpr: { value: dpr } },
    vertexShader: /* glsl */ `
      attribute float aFade;
      uniform float uSize;
      uniform float uDpr;
      varying float vFade;
      void main() {
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = max(1.0, uSize * uDpr * aFade);
        vFade = aFade;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor;
      varying float vFade;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        if (d > 0.5) discard;
        gl_FragColor = vec4(uColor, 0.5 * vFade * vFade * smoothstep(0.5, 0.25, d));
        #include <colorspace_fragment>
      }`,
    transparent: true,
    depthWrite: false,
  })
}

/** Polylines as one LineSegments geometry, each with its own opacity. */
function wireGeometry(lines: Array<{ points: Vec3[]; alpha: number }>): BufferGeometry {
  const pos: number[] = []
  const alpha: number[] = []
  for (const { points, alpha: a } of lines)
    for (let i = 0; i + 1 < points.length; i++) {
      pos.push(...points[i], ...points[i + 1])
      alpha.push(a, a)
    }
  const g = new BufferGeometry()
  g.setAttribute('position', new BufferAttribute(new Float32Array(pos), 3))
  g.setAttribute('aAlpha', new BufferAttribute(new Float32Array(alpha), 1))
  return g
}

function glowTexture(): Texture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const ctx = c.getContext('2d')
  if (ctx) {
    const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32)
    g.addColorStop(0, 'rgba(255,255,255,1)')
    g.addColorStop(0.35, 'rgba(255,255,255,0.45)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, 64, 64)
  }
  return new CanvasTexture(c)
}

const TRAIL = 42
const UP = new Vector3(0, 1, 0)

export function createBlochScene({ host, pose: initialPose, onReady, onSlow, onError }: BlochSceneOptions): BlochScene {
  const cobalt = token(host, '--color-cobalt', '#1f48d6')
  const teal = token(host, '--color-teal-600', '#0e8f8b')
  const slate = token(host, '--color-faint', '#6b7586')
  const ink = token(host, '--color-ink-2', '#1e293b')
  const muted = token(host, '--color-muted', '#525d6e')

  const renderer = new WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' })
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  renderer.setPixelRatio(dpr)
  renderer.setClearColor(0x000000, 0)
  const canvas = renderer.domElement
  canvas.style.display = 'block'
  canvas.style.width = '100%'
  canvas.style.height = '100%'
  canvas.style.touchAction = 'pan-y'
  canvas.style.cursor = 'grab'
  canvas.setAttribute('aria-hidden', 'true')
  host.appendChild(canvas)

  const scene = new Scene()
  const camera = new PerspectiveCamera(CAMERA.fov, 1, 0.1, 50)
  const e = (CAMERA.elevation * Math.PI) / 180
  camera.position.set(0, CAMERA.distance * Math.sin(e), CAMERA.distance * Math.cos(e))
  camera.lookAt(0, 0, 0)
  scene.add(new AmbientLight(0xffffff, 1.7))
  const sun = new DirectionalLight(0xffffff, 2.4)
  sun.position.set(2.5, 4, 5)
  scene.add(sun)

  const root = new Group()
  root.rotation.y = START_TURN
  scene.add(root)

  // The sphere, its equator plane, and the grid of latitudes and meridians.
  const glass = new Mesh(new SphereGeometry(1, 64, 48), glassMaterial(new Color(0xffffff).lerp(cobalt, 0.1), cobalt))
  glass.renderOrder = 1
  root.add(glass)
  const disc = new Mesh(new CircleGeometry(1, 96), new MeshBasicMaterial({ color: cobalt, transparent: true, opacity: 0.05, side: DoubleSide, depthWrite: false }))
  disc.rotation.x = -Math.PI / 2
  root.add(disc)
  const lats = [Math.PI / 6, Math.PI / 3, (2 * Math.PI) / 3, (5 * Math.PI) / 6].map((t) => ({ points: latitude(t), alpha: 0.2 }))
  const mers = [0, 1, 2, 3, 4, 5].map((k) => ({ points: meridian((k * Math.PI) / 6), alpha: 0.16 }))
  const wire = new LineSegments(wireGeometry([...lats, ...mers, { points: latitude(Math.PI / 2), alpha: 0.55 }]), wireMaterial(cobalt))
  wire.renderOrder = 2
  root.add(wire)

  // Axes: the measurement axis through both poles, and two faint dashed axes in the equator plane.
  const zAxis = new LineSegments(
    new BufferGeometry().setFromPoints([new Vector3(0, -AXIS_END, 0), new Vector3(0, AXIS_END, 0)]),
    new LineBasicMaterial({ color: muted, transparent: true, opacity: 0.55 }),
  )
  root.add(zAxis)
  const flat = new LineSegments(
    new BufferGeometry().setFromPoints([new Vector3(-1.12, 0, 0), new Vector3(1.12, 0, 0), new Vector3(0, 0, -1.12), new Vector3(0, 0, 1.12)]),
    new LineDashedMaterial({ color: muted, transparent: true, opacity: 0.3, dashSize: 0.05, gapSize: 0.04 }),
  )
  flat.computeLineDistances()
  root.add(flat)

  // Poles: a dot and a ripple ring each. |0> (skip) on top, |1> (buy) below.
  const dotGeo = new SphereGeometry(0.05, 20, 14)
  const poles = { '0': { y: 1, color: slate }, '1': { y: -1, color: teal } } as const
  const poleParts = (['0', '1'] as const).map((bit) => {
    const dot = new Mesh(dotGeo, new MeshBasicMaterial({ color: poles[bit].color }))
    dot.position.y = poles[bit].y
    const ring = new Mesh(new RingGeometry(0.86, 1, 64), new MeshBasicMaterial({ color: poles[bit].color, transparent: true, opacity: 0, side: DoubleSide, depthWrite: false }))
    ring.rotation.x = -Math.PI / 2
    ring.position.y = poles[bit].y
    root.add(dot, ring)
    return { bit, dot, ring, t: 1 }
  })

  // The state arrow: a shaft and a cone, a soft glow at the tip, and a short trail of where the tip has been.
  const arrowMat = new MeshStandardMaterial({ color: cobalt.clone(), roughness: 0.38, metalness: 0.12 })
  const arrow = new Group()
  const shaft = new Mesh(new CylinderGeometry(0.022, 0.022, 0.84, 16), arrowMat)
  shaft.position.y = 0.42
  const head = new Mesh(new ConeGeometry(0.075, 0.17, 28), arrowMat)
  head.position.y = 0.915
  const glowMap = glowTexture()
  const glow = new Sprite(new SpriteMaterial({ map: glowMap, color: cobalt.clone(), transparent: true, opacity: 0.55, depthWrite: false, blending: NormalBlending }))
  glow.scale.setScalar(0.34)
  glow.position.y = 1
  arrow.add(shaft, head, glow)
  root.add(arrow)
  const hub = new Mesh(new SphereGeometry(0.055, 20, 14), new MeshStandardMaterial({ color: ink, roughness: 0.5 }))
  root.add(hub)

  const trailPos = new Float32Array(TRAIL * 3)
  const trailFade = new Float32Array(TRAIL)
  for (let i = 0; i < TRAIL; i++) trailFade[i] = 1 - i / TRAIL
  const trailGeo = new BufferGeometry()
  trailGeo.setAttribute('position', new BufferAttribute(trailPos, 3))
  trailGeo.setAttribute('aFade', new BufferAttribute(trailFade, 1))
  const trailMat = trailMaterial(cobalt.clone(), dpr)
  const trail = new Points(trailGeo, trailMat)
  trail.frustumCulled = false
  root.add(trail)

  // Motion state. Theta follows a slightly springy target, so a measurement snaps to its pole with a small bounce.
  let pose: QubitPose = initialPose
  let theta: number = IDLE.theta
  let thetaVel = 0
  let phi: number = IDLE.phi
  let phiSpeed = 0.9
  let clock = 0
  let rippled = false
  let turn = 0
  let turnVel = 0
  let dragging = false
  let lastX = 0
  const tint = cobalt.clone()
  const dir = new Vector3()
  const q = new Quaternion()
  const placeTrail = (x: number, y: number, z: number) => {
    for (let i = 0; i < TRAIL; i++) trailPos.set([x, y, z], i * 3)
  }
  {
    const [x, y, z] = blochDir(theta, phi)
    placeTrail(x, y, z)
  }

  function step(dt: number) {
    clock += dt
    const target = pose === '0' ? 0 : pose === '1' ? Math.PI : pose === 'measure' ? Math.PI / 2 : IDLE.theta + 0.2 * Math.sin(clock * 1.1)
    const speed = pose === 'measure' ? 6.4 : pose === 'idle' ? 0.9 : 0
    thetaVel += ((target - theta) * 130 - thetaVel * 15) * dt
    theta += thetaVel * dt
    phiSpeed += (speed - phiSpeed) * (1 - Math.exp(-5 * dt))
    phi += phiSpeed * dt

    const want = pose === '1' ? teal : pose === '0' ? slate : cobalt
    tint.lerp(want, 1 - Math.exp(-9 * dt))
    arrowMat.color.copy(tint)
    glow.material.color.copy(tint)
    ;(trailMat.uniforms.uColor.value as Color).copy(tint)

    const [x, y, z] = blochDir(theta, phi)
    dir.set(x, y, z).normalize()
    arrow.quaternion.copy(q.setFromUnitVectors(UP, dir))
    trailPos.copyWithin(3, 0, (TRAIL - 1) * 3)
    trailPos.set([x, y, z], 0)
    trailGeo.attributes.position.needsUpdate = true

    // A ripple at the pole the qubit lands on.
    if ((pose === '0' || pose === '1') && !rippled && Math.abs(theta - target) < 0.14) {
      rippled = true
      const p = poleParts.find((r) => r.bit === pose)
      if (p) p.t = 0
    }
    for (const p of poleParts) {
      p.t = Math.min(1, p.t + dt / 0.75)
      const s = 0.12 + 0.62 * p.t
      p.ring.scale.set(s, s, s)
      ;(p.ring.material as MeshBasicMaterial).opacity = p.t >= 1 ? 0 : 0.55 * (1 - p.t)
      p.dot.scale.setScalar(1 + 0.9 * Math.sin(Math.PI * Math.min(1, p.t * 1.6)) * (p.t < 1 ? 1 : 0))
    }

    // A slow turn, plus whatever the reader added by dragging (which then eases off).
    if (!dragging) {
      turn += turnVel * dt
      turnVel *= Math.exp(-2.5 * dt)
    }
    root.rotation.y = START_TURN + clock * 0.14 + turn
  }

  // Drag to turn.
  const onDown = (ev: PointerEvent) => {
    dragging = true
    lastX = ev.clientX
    turnVel = 0
    canvas.style.cursor = 'grabbing'
    canvas.setPointerCapture(ev.pointerId)
  }
  const onMove = (ev: PointerEvent) => {
    if (!dragging) return
    const dx = ev.clientX - lastX
    lastX = ev.clientX
    turn += dx * 0.012
    turnVel = dx * 0.6
  }
  const onUp = (ev: PointerEvent) => {
    dragging = false
    canvas.style.cursor = 'grab'
    if (canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId)
  }
  canvas.addEventListener('pointerdown', onDown)
  canvas.addEventListener('pointermove', onMove)
  canvas.addEventListener('pointerup', onUp)
  canvas.addEventListener('pointercancel', onUp)

  const onLost = () => onError()
  canvas.addEventListener('webglcontextlost', onLost)

  // Size: the host is square; follow it exactly.
  const resize = () => {
    const w = Math.max(1, host.clientWidth)
    const h = Math.max(1, host.clientHeight)
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }
  resize()
  const ro = new ResizeObserver(resize)
  ro.observe(host)

  // The loop runs only while the sphere is on screen and the tab is visible.
  let raf = 0
  let last = 0
  let onScreen = true
  let ready = false
  const samples: number[] = []
  const frame = (now: number) => {
    raf = requestAnimationFrame(frame)
    const dt = last ? Math.min((now - last) / 1000, 1 / 20) : 1 / 60
    if (last && samples.length < 150) {
      samples.push((now - last) / 1000)
      if (samples.length === 150) {
        const sorted = samples.slice(30).sort((a, b) => a - b)
        if (sorted[Math.floor(sorted.length / 2)] > 1 / 22) onSlow()
      }
    }
    last = now
    step(dt)
    renderer.render(scene, camera)
    if (!ready) {
      ready = true
      onReady()
    }
  }
  const run = () => {
    if (raf || !onScreen || document.hidden) return
    last = 0
    raf = requestAnimationFrame(frame)
  }
  const stop = () => {
    cancelAnimationFrame(raf)
    raf = 0
  }
  const io = new IntersectionObserver((entries) => {
    onScreen = entries.some((en) => en.isIntersecting)
    if (onScreen) run()
    else stop()
  })
  io.observe(host)
  const onVisibility = () => (document.hidden ? stop() : run())
  document.addEventListener('visibilitychange', onVisibility)
  run()

  return {
    setPose(next) {
      if (next === pose) return
      pose = next
      rippled = false
    },
    dispose() {
      stop()
      io.disconnect()
      ro.disconnect()
      document.removeEventListener('visibilitychange', onVisibility)
      canvas.removeEventListener('pointerdown', onDown)
      canvas.removeEventListener('pointermove', onMove)
      canvas.removeEventListener('pointerup', onUp)
      canvas.removeEventListener('pointercancel', onUp)
      canvas.removeEventListener('webglcontextlost', onLost)
      const seen = new Set<object>()
      scene.traverse((o: Object3D) => {
        const m = o as Partial<Mesh>
        if (m.geometry && !seen.has(m.geometry)) {
          seen.add(m.geometry)
          m.geometry.dispose()
        }
        const mats = m.material ? (Array.isArray(m.material) ? m.material : [m.material]) : []
        for (const mat of mats as Material[])
          if (!seen.has(mat)) {
            seen.add(mat)
            mat.dispose()
          }
      })
      glowMap.dispose()
      renderer.dispose()
      // Free the GPU context now rather than whenever the browser gets round to it.
      renderer.forceContextLoss()
      canvas.remove()
    },
  }
}
