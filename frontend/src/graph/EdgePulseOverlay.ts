import type { Core, EdgeSingular } from 'cytoscape'
import type { NetworkActivityItem } from '../types/system'

export type PulseKind = 'open' | 'close' | 'update'

interface Pulse {
  start: number
  dur: number
  kind: PulseKind
}

/** One traveling particle caused by ACTUAL observed bytes. */
interface DataParticle {
  edgeId: string
  /** +1 = source->target (forward), -1 = target->source (reverse) */
  dir: 1 | -1
  t: number // 0..1 progress along the edge
  dur: number // ms to travel the edge
  size: number
  born: number
}

interface EdgeActivity {
  fwdBps: number
  revBps: number
  lastActivity: number // performance.now() of the last observed activity
  level: number // 1 low, 2 medium, 3 high
  lastSpawn: number
  synthetic?: boolean // TEST-ONLY injected activity (benchmark mode)
}

const PULSE_COLORS: Record<PulseKind, string> = {
  open: '#35e0ff',
  close: '#ff5d5d',
  update: '#8affb0',
}

// directional data particles: forward = OUT (process -> remote), reverse = IN
const FWD_COLOR = '#35e0ff'
const REV_COLOR = '#ffb35c'

const MAX_PULSES = 30
const MAX_RECENT = 80
const MAX_PARTICLES = 140
const MAX_ACTIVITY_EDGES = 400

// per-edge particle ceiling scales with activity level: a low edge may keep
// 2 particles, a high edge up to 6 — the global MAX_PARTICLES budget is
// never exceeded, and quiet edges can never starve loud ones
const PER_EDGE_CAP: Record<number, number> = { 1: 2, 2: 4, 3: 6 }

// activity freshness windows (ms) — mirrors the backend decay model
export const ACTIVE_MS = 500
export const RECENT_MS = 5000

// spawn cadence per activity level (ms between particle launches per edge)
const SPAWN_INTERVAL: Record<number, number> = { 1: 1600, 2: 700, 3: 300 }

/**
 * Canvas overlay that draws traveling "signal" particles along Cytoscape
 * edges. There are two strictly separate signal classes:
 *
 *  1. LIFECYCLE pulses  — CONNECTION_OPENED / CLOSED / UPDATE events
 *                         (pulse(), kind open|close|update)
 *  2. DATA particles    — actual observed network bytes from the backend
 *                         telemetry aggregator (applyActivity()): direction
 *                         is real (fwd/rev), intensity scales with the
 *                         observed rate, and particles stop ~500 ms after
 *                         the last observed activity (ACTIVE -> RECENT ->
 *                         IDLE decay). A connection that merely stays open
 *                         NEVER animates.
 *
 * Rendering is rAF-driven and stops entirely when nothing is active, so the
 * cost is proportional to real activity, not graph size.
 */
export class EdgePulseOverlay {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private pulses = new Map<string, Pulse>()
  private recent = new Map<string, number>()
  private activity = new Map<string, EdgeActivity>()
  private particles: DataParticle[] = []
  private running = false
  private raf = 0
  private stops = 0
  private testMuted = false
  private ro: ResizeObserver

  constructor(
    private cy: Core,
    container: HTMLElement,
  ) {
    this.canvas = document.createElement('canvas')
    this.canvas.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:10;'
    container.appendChild(this.canvas)
    const ctx = this.canvas.getContext('2d')
    if (!ctx) throw new Error('canvas 2d unavailable')
    this.ctx = ctx
    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(container)
    this.resize()
  }

  private resize(): void {
    const parent = this.canvas.parentElement
    if (!parent) return
    const rect = parent.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    this.canvas.style.width = `${rect.width}px`
    this.canvas.style.height = `${rect.height}px`
    this.canvas.width = Math.max(1, Math.round(rect.width * dpr))
    this.canvas.height = Math.max(1, Math.round(rect.height * dpr))
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  // ------------------------------------------------------ lifecycle pulses

  pulse(edgeId: string, kind: PulseKind): void {
    if (this.testMuted) return
    if (this.pulses.has(edgeId)) this.pulses.delete(edgeId)
    this.pulses.set(edgeId, {
      start: performance.now(),
      dur: kind === 'close' ? 650 : 550,
      kind,
    })
    while (this.pulses.size > MAX_PULSES) {
      const first = this.pulses.keys().next().value as string | undefined
      if (first === undefined) break
      this.pulses.delete(first)
    }
    this.markRecent(edgeId, 3500)
    this.ensureRunning()
  }

  markRecent(edgeId: string, ms: number): void {
    if (this.testMuted) return
    this.recent.set(edgeId, performance.now() + ms)
    while (this.recent.size > MAX_RECENT) {
      const first = this.recent.keys().next().value as string | undefined
      if (first === undefined) break
      this.recent.delete(first)
    }
    this.ensureRunning()
  }

  // ---------------------------------------------------- data activity feed

  /** Feed one aggregated activity batch (from the backend, ~5 msg/s max).
   * ``synthetic`` marks TEST-ONLY injected activity (benchmark mode only)
   * so it can never be confused with real observed bytes. */
  applyActivity(items: NetworkActivityItem[], synthetic = false): void {
    const now = performance.now()
    const touched = new Set<string>()
    for (const it of items) {
      touched.add(it.edge_id)
      const prev = this.activity.get(it.edge_id)
      this.activity.set(it.edge_id, {
        fwdBps: it.fwd_bps,
        revBps: it.rev_bps,
        lastActivity: now,
        level: it.level,
        lastSpawn: prev?.lastSpawn ?? 0,
        synthetic: synthetic || prev?.synthetic,
      })
    }
    // prune edges that stopped reporting and are no longer active/recent
    for (const [eid, st] of this.activity) {
      if (touched.has(eid)) continue
      if (now - st.lastActivity > RECENT_MS) this.activity.delete(eid)
    }
    if (this.activity.size > MAX_ACTIVITY_EDGES) {
      // bounded memory: keep only the most recently active
      const sorted = [...this.activity.entries()].sort(
        (a, b) => b[1].lastActivity - a[1].lastActivity,
      )
      this.activity = new Map(sorted.slice(0, MAX_ACTIVITY_EDGES))
    }
    if (items.length > 0) this.ensureRunning()
  }

  private spawnParticle(edgeId: string, st: EdgeActivity): void {
    const edge = this.cy.getElementById(edgeId) as unknown as EdgeSingular
    if (edge.length === 0 || edge.removed()) return
    const own = this.particles.filter((p) => p.edgeId === edgeId)
    if (own.length >= (PER_EDGE_CAP[st.level] ?? 2)) return
    if (this.particles.length >= MAX_PARTICLES) return
    const total = st.fwdBps + st.revBps
    if (total <= 0) return
    // weighted direction: whichever direction actually carries bytes
    const dir: 1 | -1 = Math.random() < st.fwdBps / total ? 1 : -1
    const dur = 650 + Math.random() * 350
    this.particles.push({
      edgeId,
      dir,
      t: 0,
      dur,
      size: 1.6 + Math.min(1.4, Math.sqrt(st.level) * 0.5),
      born: performance.now(),
    })
  }

  private ensureRunning(): void {
    if (!this.running) {
      this.running = true
      this.loop()
    }
  }

  private toRender(p: { x: number; y: number }): { x: number; y: number } {
    return {
      x: p.x * this.cy.zoom() + this.cy.pan().x,
      y: p.y * this.cy.zoom() + this.cy.pan().y,
    }
  }

  /** Model-space point at t along the edge; null when the edge's endpoints
   * are not computable (e.g. a display:none node mid-filter). Callers skip
   * null so a transient hidden element can never kill the rAF loop. */
  private pointOn(edge: EdgeSingular, t: number): { x: number; y: number } | null {
    const s = edge.sourceEndpoint()
    const e = edge.targetEndpoint()
    if (!s || !e || typeof s.x !== 'number' || typeof e.x !== 'number') return null
    return { x: s.x + (e.x - s.x) * t, y: s.y + (e.y - s.y) * t }
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop)
    const now = performance.now()

    // --- time-based decay (v0.2.1 fix) -------------------------------------
    // The backend only sends network_activity while activity exists, so a
    // stale entry can never rely on the NEXT batch to be removed. The rAF
    // loop prunes by wall-clock age: once an edge has been silent for
    // RECENT_MS it leaves the map, `hasData` goes false, and the loop can
    // stop entirely. This is visual decay, NOT a fake zero-rate observation.
    for (const [edgeId, st] of this.activity) {
      if (now - st.lastActivity > RECENT_MS) this.activity.delete(edgeId)
    }

    // spawn data particles for ACTIVE edges (actual observed traffic).
    // Prioritized: strongest level + most recent activity spawn first, so
    // when the global particle budget is exhausted the loudest/recent edges
    // keep their particles and quiet ones wait their turn.
    const spawnOrder = [...this.activity.entries()].sort((a, b) => {
      if (b[1].level !== a[1].level) return b[1].level - a[1].level
      return b[1].lastActivity - a[1].lastActivity
    })
    for (const [edgeId, st] of spawnOrder) {
      const age = now - st.lastActivity
      if (age >= ACTIVE_MS) continue
      const interval = SPAWN_INTERVAL[st.level] ?? 1600
      if (now - st.lastSpawn >= interval) {
        st.lastSpawn = now
        this.spawnParticle(edgeId, st)
        if (this.particles.length >= MAX_PARTICLES) break
      }
    }

    // advance particles; drop finished ones
    const alive: DataParticle[] = []
    for (const p of this.particles) {
      const edge = this.cy.getElementById(p.edgeId) as unknown as EdgeSingular
      if (edge.length === 0 || edge.removed()) continue
      // a particle in flight may finish its travel even as decay sets in
      p.t = Math.min(1, (now - p.born) / p.dur)
      if (p.t < 1) alive.push(p)
    }
    this.particles = alive

    const hasData = this.activity.size > 0
    const hasRecent = this.recent.size > 0
    const hasPulses = this.pulses.size > 0
    if (!hasData && !hasRecent && !hasPulses && this.particles.length === 0) {
      cancelAnimationFrame(this.raf)
      this.running = false
      this.stops += 1
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
      return
    }

    const ctx = this.ctx
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
    const zoom = Math.max(0.4, this.cy.zoom())

    // --- faint traveling dots on recently-active edges (RECENT decay) ---
    // both lifecycle recents and data-activity recents (500ms..5s since bytes)
    const slowDots = new Set<string>()
    for (const [edgeId, until] of this.recent) {
      if (now > until) {
        this.recent.delete(edgeId)
        continue
      }
      slowDots.add(edgeId)
    }
    for (const [edgeId, st] of this.activity) {
      const age = now - st.lastActivity
      if (age >= ACTIVE_MS && age < RECENT_MS) slowDots.add(edgeId)
    }
    for (const edgeId of slowDots) {
      const edge = this.cy.getElementById(edgeId) as unknown as EdgeSingular
      if (edge.length === 0 || edge.removed()) continue
      const t = (now % 2600) / 2600
      const pt = this.pointOn(edge, t)
      if (!pt) continue
      const p = this.toRender(pt)
      ctx.beginPath()
      ctx.arc(p.x, p.y, 1.3 * zoom, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(63,180,216,0.14)'
      ctx.fill()
    }

    // --- directional data particles (ACTUAL bytes, real direction) -------
    for (const p of this.particles) {
      const edge = this.cy.getElementById(p.edgeId) as unknown as EdgeSingular
      if (edge.length === 0 || edge.removed()) continue
      const headT = p.dir === 1 ? p.t : 1 - p.t
      const pt = this.pointOn(edge, headT)
      if (!pt) continue
      const head = this.toRender(pt)
      const color = p.dir === 1 ? FWD_COLOR : REV_COLOR
      ctx.save()
      ctx.shadowColor = color
      ctx.shadowBlur = 8 * zoom
      ctx.beginPath()
      ctx.arc(head.x, head.y, p.size * zoom, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
      ctx.restore()
      for (let i = 1; i <= 4; i++) {
        const tt = p.t - i * 0.05
        if (tt < 0) break
        const pt2 = this.pointOn(edge, p.dir === 1 ? tt : 1 - tt)
        if (!pt2) break
        const p2 = this.toRender(pt2)
        const a = 0.35 * (1 - i / 5)
        ctx.beginPath()
        ctx.arc(p2.x, p2.y, p.size * 0.75 * zoom * (1 - i / 10), 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.globalAlpha = a
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }

    // --- lifecycle pulses (open/close/update events) ---------------------
    for (const [edgeId, pulse] of this.pulses) {
      const t = (now - pulse.start) / pulse.dur
      if (t >= 1) {
        this.pulses.delete(edgeId)
        continue
      }
      const edge = this.cy.getElementById(edgeId) as unknown as EdgeSingular
      if (edge.length === 0 || edge.removed()) {
        this.pulses.delete(edgeId)
        continue
      }
      const color = PULSE_COLORS[pulse.kind]
      const pt = this.pointOn(edge, t)
      if (!pt) continue
      const head = this.toRender(pt)
      ctx.save()
      ctx.shadowColor = color
      ctx.shadowBlur = 10 * zoom
      ctx.beginPath()
      ctx.arc(head.x, head.y, 2.2 * zoom, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
      ctx.restore()
      for (let i = 1; i <= 6; i++) {
        const tt = t - i * 0.035
        if (tt < 0) break
        const pt2 = this.pointOn(edge, tt)
        if (!pt2) break
        const p = this.toRender(pt2)
        const a = 0.4 * (1 - i / 7) * (pulse.kind === 'close' ? 0.8 : 1)
        ctx.beginPath()
        ctx.arc(p.x, p.y, 1.6 * zoom * (1 - i / 10), 0, Math.PI * 2)
        ctx.fillStyle = color
        ctx.globalAlpha = a
        ctx.fill()
      }
      ctx.globalAlpha = 1
    }
  }

  destroy(): void {
    cancelAnimationFrame(this.raf)
    this.ro.disconnect()
    this.canvas.remove()
  }

  /**
   * TEST ONLY (acceptance Test T3): force the terminal idle state — empty
   * activity/particles/pulses/recent — exactly as time-decay eventually
   * does, so the rAF stop branch can be verified deterministically even on
   * a machine whose real lifecycle events keep the loop alive. Clears ONLY
   * canvas/UI state; never touches telemetry or the DOM graph.
   */
  testForceIdle(): void {
    this.activity.clear()
    this.particles = []
    this.pulses.clear()
    this.recent.clear()
  }

  /**
   * TEST ONLY (acceptance Test T3): suspend the lifecycle pulse/recent
   * feed — the equivalent of a machine with no connection events — and
   * force the terminal idle state, so the rAF stop branch can be verified
   * deterministically even on a machine whose real events keep the loop
   * alive. Clears ONLY canvas/UI state; never touches telemetry or the
   * DOM graph.
   */
  testMute(muted: boolean): void {
    this.testMuted = muted
    if (!muted) return
    this.activity.clear()
    this.particles = []
    this.pulses.clear()
    this.recent.clear()
  }

  /**
   * Read-only diagnostics for acceptance tests / debugging: current
   * run-state of the overlay (never exposes packet data).
   */
  stats(): { running: boolean; activity: number; particles: number; pulses: number; recent: number; stops: number; synthetic: number; budget: { maxParticles: number; activityEdges: number; perEdgeCaps: Record<number, number> } } {
    let synthetic = 0
    for (const st of this.activity.values()) {
      if (st.synthetic) synthetic += 1
    }
    return {
      running: this.running,
      activity: this.activity.size,
      particles: this.particles.length,
      pulses: this.pulses.size,
      recent: this.recent.size,
      stops: this.stops,
      synthetic,
      budget: {
        maxParticles: MAX_PARTICLES,
        activityEdges: MAX_ACTIVITY_EDGES,
        perEdgeCaps: { ...PER_EDGE_CAP },
      },
    }
  }
}
