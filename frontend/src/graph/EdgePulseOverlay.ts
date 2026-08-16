import type { Core, EdgeSingular } from 'cytoscape'

export type PulseKind = 'open' | 'close' | 'update'

interface Pulse {
  start: number
  dur: number
  kind: PulseKind
}

const COLORS: Record<PulseKind, string> = {
  open: '#35e0ff',
  close: '#ff5d5d',
  update: '#8affb0',
}

const MAX_PULSES = 30
const MAX_RECENT = 80

/**
 * Canvas overlay that draws traveling "signal" particles along Cytoscape
 * edges. Only edges that actually have activity are drawn, so the cost is
 * proportional to real system activity — not the graph size. Rendering is
 * rAF-driven and stops entirely when idle.
 */
export class EdgePulseOverlay {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private pulses = new Map<string, Pulse>()
  private recent = new Map<string, number>()
  private running = false
  private raf = 0
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

  pulse(edgeId: string, kind: PulseKind): void {
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
    this.recent.set(edgeId, performance.now() + ms)
    while (this.recent.size > MAX_RECENT) {
      const first = this.recent.keys().next().value as string | undefined
      if (first === undefined) break
      this.recent.delete(first)
    }
    this.ensureRunning()
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

  private pointOn(edge: EdgeSingular, t: number): { x: number; y: number } {
    const s = edge.sourceEndpoint()
    const e = edge.targetEndpoint()
    return { x: s.x + (e.x - s.x) * t, y: s.y + (e.y - s.y) * t }
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop)
    const now = performance.now()
    if (this.pulses.size === 0 && this.recent.size === 0) {
      cancelAnimationFrame(this.raf)
      this.running = false
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
      return
    }
    const ctx = this.ctx
    ctx.clearRect(0, 0, this.canvas.width, this.canvas.height)
    const zoom = Math.max(0.4, this.cy.zoom())

    // subtle continuous dots on recently-active edges
    for (const [edgeId, until] of this.recent) {
      if (now > until) {
        this.recent.delete(edgeId)
        continue
      }
      const edge = this.cy.getElementById(edgeId) as unknown as EdgeSingular
      if (edge.length === 0 || edge.removed()) {
        this.recent.delete(edgeId)
        continue
      }
      const t = (now % 2600) / 2600
      const p = this.toRender(this.pointOn(edge, t))
      ctx.beginPath()
      ctx.arc(p.x, p.y, 1.3 * zoom, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(63,180,216,0.14)'
      ctx.fill()
    }

    // strong traveling pulses for real events
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
      const color = COLORS[pulse.kind]
      const head = this.toRender(this.pointOn(edge, t))
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
        const p = this.toRender(this.pointOn(edge, tt))
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
}
