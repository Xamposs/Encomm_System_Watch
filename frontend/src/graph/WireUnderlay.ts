import type { Core } from 'cytoscape'
import { perf } from './PerfMonitor'

/**
 * Reference-fidelity wiring palette. Edge kind remains the source of truth;
 * stable per-edge variants create the multicolour cable field while tooltips
 * and the inspector retain the exact relationship semantics.
 */
export const EDGE_KIND_COLORS: Record<string, string> = {
  LOCALHOST: '#4fd2f7',
  LISTEN: '#5ee89a',
  EXTERNAL: '#3f9fe8',
  USES_GPU: '#2fe6a8',
  SERVES_MODEL: '#b06cff',
  LOCAL_API: '#5fc8ff',
  HOSTS: '#e8a04a',
  PROCESS_PARENT: '#6b7d95',
  SPAWNED: '#9a7cf0',
  MEMBER_OF: '#a78bfa',
  HOSTED_BY: '#e8a04a',
  EXPOSES: '#2dd4bf',
  CONNECTED_TO: '#7f93ac',
  BACKED_BY: '#e879f9',
  AI_CALL: '#a855f7',
}
export const DEFAULT_EDGE_COLOR = '#3f5a75'

const EDGE_VARIANTS: Record<string, string[]> = {
  LOCALHOST: ['#22d3ee', '#2dd4bf', '#38bdf8', '#ec4899', '#f97316', '#8b5cf6'],
  LISTEN: ['#34d399', '#2dd4bf', '#84cc16'],
  EXTERNAL: ['#38bdf8', '#06b6d4', '#f97316', '#ef4444', '#ec4899'],
  PROCESS_PARENT: ['#a855f7', '#ec4899', '#7c3aed', '#3b82f6'],
  SPAWNED: ['#c026d3', '#8b5cf6', '#ec4899'],
  MEMBER_OF: ['#8b5cf6', '#d946ef', '#6366f1'],
  HOSTS: ['#f97316', '#f59e0b', '#ef4444'],
  HOSTED_BY: ['#f59e0b', '#fb7185', '#f97316'],
  CONNECTED_TO: ['#64748b', '#38bdf8', '#14b8a6'],
  AI_CALL: ['#ec4899', '#d946ef', '#8b5cf6'],
}

function renderedEdgeColor(kind: string, id: string): string {
  const variants = EDGE_VARIANTS[kind]
  if (!variants?.length) return EDGE_KIND_COLORS[kind] ?? DEFAULT_EDGE_COLOR
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  return variants[Math.abs(h) % variants.length]
}

/** One semantic composition zone (model space), drawn by the underlay. */
export interface ZoneInfo {
  label: string
  role: string
  x0: number
  x1: number
  y0: number
}

/**
 * Background wire-underlay canvas (v1.0.2 final pass).
 *
 * Draws a soft colored glow along EVERY real rendered edge BEHIND the
 * cards (the reference "wiring field" depth) plus faint zone headers, so
 * the map reads as cards floating above a live machine map. No synthetic
 * edges: the glow follows the exact rendered curve of each real edge via
 * edge.controlPoints(). Redraws only when cytoscape re-renders
 * (pan/zoom/update), rAF-throttled, and is fully transparent when the
 * graph is empty.
 */
export class WireUnderlay {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private raf = 0
  private destroyed = false
  private ro: ResizeObserver
  private cssW = 1
  private cssH = 1
  private lastDraw = 0
  private drawTimer: number | undefined
  private interacting = false

  constructor(
    private cy: Core,
    private container: HTMLElement,
    private getZones: () => ZoneInfo[],
  ) {
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'graph-wire-underlay'
    this.canvas.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:0;'
    // first child => cytoscape's own canvases (created earlier) paint above
    container.insertBefore(this.canvas, container.firstChild)
    const ctx = this.canvas.getContext('2d')
    if (!ctx) throw new Error('canvas 2d unavailable')
    this.ctx = ctx
    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(container)
    container.addEventListener('esw:layout', this.requestDraw)
    container.addEventListener('esw:interaction', this.onInteraction)
    this.resize()
    cy.on('pan zoom resize', this.requestDraw)
    cy.on('position', 'node', this.requestDraw)
    cy.on('add remove', 'edge', this.requestDraw)
    cy.on('destroy', this.onDestroy)
    this.requestDraw()
  }

  private onDestroy = (): void => {
    this.destroyed = true
  }

  private onInteraction = (event: Event): void => {
    this.interacting = Boolean((event as CustomEvent<{ active?: boolean }>).detail?.active)
    this.requestDraw()
  }

  private resize(): void {
    const parent = this.canvas.parentElement
    if (!parent) return
    const rect = parent.getBoundingClientRect()
    const dpr = window.devicePixelRatio || 1
    this.cssW = Math.max(1, rect.width)
    this.cssH = Math.max(1, rect.height)
    this.canvas.style.width = `${rect.width}px`
    this.canvas.style.height = `${rect.height}px`
    this.canvas.width = Math.max(1, Math.round(rect.width * dpr))
    this.canvas.height = Math.max(1, Math.round(rect.height * dpr))
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    this.requestDraw()
  }

  private requestDraw = (): void => {
    if (this.destroyed || this.raf || this.drawTimer !== undefined) return
    // Decorative wire glow is intentionally capped at 20 fps. Geometry and
    // hit-testing remain native Cytoscape; this pass never needs 60 redraws/s.
    const frameInterval = this.interacting ? 90 : 50
    const wait = Math.max(0, frameInterval - (performance.now() - this.lastDraw))
    perf.setOverlayRaf('wires', true)
    const schedule = (): void => {
      this.drawTimer = undefined
      this.raf = requestAnimationFrame(() => {
        this.raf = 0
        this.lastDraw = performance.now()
        try { this.draw() } finally { perf.setOverlayRaf('wires', false) }
      })
    }
    if (wait > 1) this.drawTimer = window.setTimeout(schedule, wait)
    else schedule()
  }

  /** First control point of the rendered curve (quadratic bezier). */
  private controlPointOf(e: { controlPoints(): number[] | Array<{ x: number; y: number }> }): { x: number; y: number } | null {
    try {
      const cps = e.controlPoints()
      if (!cps || cps.length < 2) return null
      const a = cps[0]
      if (typeof a === 'number') return { x: a as number, y: cps[1] as number }
      if (typeof (a as { x?: number }).x === 'number') {
        return { x: (a as { x: number }).x, y: (a as { y: number }).y }
      }
      return null
    } catch {
      return null
    }
  }

  private draw(): void {
    const started = performance.now()
    const cy = this.cy
    const ctx = this.ctx
    ctx.clearRect(0, 0, this.cssW, this.cssH)
    if (cy.nodes().length === 0) {
      perf.recordOverlayDraw('wire', performance.now() - started)
      return
    }
    const zoom = cy.zoom()
    const quality = this.interacting ? 'interaction' : zoom < 0.16 ? 'far' : zoom < 0.58 ? 'mid' : 'near'
    this.canvas.dataset.quality = quality
    const pan = cy.pan()
    const toX = (mx: number): number => mx * zoom + pan.x
    const toY = (my: number): number => my * zoom + pan.y

    // --- edge glow underlay (real rendered curves, behind the cards) -------
    const edges = cy.edges(':visible')
    // defensive cap: huge fixture graphs skip the decorative pass
    let processed = 0
    if (edges.length <= 2600) {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      // At FIT ALL, painting every glow twice costs far more than the tiny
      // sub-pixel result. Keep a stable representative wire field instead.
      const target = this.interacting ? 220 : zoom < 0.16 ? 320 : zoom < 0.58 ? 700 : 1500
      const stride = Math.max(1, Math.ceil(edges.length / target))
      for (let i = 0; i < edges.length; i += stride) {
        const e = edges[i]
        const kind = String(e.data('kind') ?? '')
        const color = renderedEdgeColor(kind, e.id())
        let alpha = 0.28
        if (e.hasClass('ai-dim')) alpha *= 0.22
        if (e.hasClass('fading')) continue // closed-connection fade: no glow
        const s = e.sourceEndpoint()
        const t = e.targetEndpoint()
        if (!s || !t || typeof s.x !== 'number' || typeof t.x !== 'number') continue
        const sx = toX(s.x); const sy = toY(s.y); const tx = toX(t.x); const ty = toY(t.y)
        const margin = 80
        if ((sx < -margin && tx < -margin) || (sx > this.cssW + margin && tx > this.cssW + margin) ||
          (sy < -margin && ty < -margin) || (sy > this.cssH + margin && ty > this.cssH + margin)) continue
        processed += 1
        const cp = this.controlPointOf(e)
        const w = Math.max(2.4, 4.2 * zoom)
        ctx.strokeStyle = color
        ctx.globalAlpha = alpha
        ctx.lineWidth = w
        ctx.beginPath()
        ctx.moveTo(sx, sy)
        if (cp) ctx.quadraticCurveTo(toX(cp.x), toY(cp.y), tx, ty)
        else ctx.lineTo(tx, ty)
        ctx.stroke()
        // inner brighter pass -> soft neon falloff
        if (!this.interacting && zoom >= 0.58) {
          ctx.globalAlpha = Math.min(0.78, alpha * 2.1)
          ctx.lineWidth = Math.max(0.9, 1.45 * zoom)
          ctx.stroke()
        }
      }
      ctx.globalAlpha = 1
    }
    this.canvas.dataset.processedEdges = String(processed)
    this.canvas.dataset.visibleEdges = String(edges.length)

    // --- faint zone headers (semantic regions of the composed map) ---------
    for (const z of this.getZones()) {
      const x0 = toX(z.x0)
      const x1 = toX(z.x1)
      const y = toY(z.y0)
      const w = x1 - x0
      if (w < 70) continue // far-out: skip micro zones
      if (y < -34 || y > this.cssH + 34) continue
      // thin rule under the label
      ctx.strokeStyle = 'rgba(111,139,180,0.24)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x0, y + 12)
      ctx.lineTo(x1, y + 12)
      ctx.stroke()
      // label
      ctx.font = '600 13px Consolas, "Cascadia Mono", monospace'
      ctx.fillStyle = 'rgba(139,161,196,0.58)'
      try {
        ;(ctx as unknown as { letterSpacing: string }).letterSpacing = '3px'
      } catch { /* older engines */ }
      ctx.fillText(z.label, x0, y)
      try {
        ;(ctx as unknown as { letterSpacing: string }).letterSpacing = '0px'
      } catch { /* older engines */ }
    }
    const elapsed = performance.now() - started
    perf.recordOverlayDraw('wire', elapsed)
    if (this.interacting) perf.recordInteractionFrame(elapsed)
  }

  destroy(): void {
    this.destroyed = true
    if (this.raf) cancelAnimationFrame(this.raf)
    if (this.drawTimer !== undefined) window.clearTimeout(this.drawTimer)
    perf.setOverlayRaf('wires', false)
    this.ro.disconnect()
    this.cy.off('pan zoom resize', this.requestDraw)
    this.cy.off('position', 'node', this.requestDraw)
    this.cy.off('add remove', 'edge', this.requestDraw)
    this.canvas.parentElement?.removeEventListener('esw:layout', this.requestDraw)
    this.container.removeEventListener('esw:interaction', this.onInteraction)
    this.canvas.remove()
  }
}
