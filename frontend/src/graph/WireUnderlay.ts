import type { Core } from 'cytoscape'

/**
 * v1.0.2 final pass — reference-fidelity wiring palette.
 * Technical meaning (never decoration): blue/cyan = normal system/process/
 * network, teal/green = active/live/healthy/resource, amber/orange =
 * services/infra/warnings, purple/magenta = AI/Hermes/provider-related.
 * Red appears ONLY on genuine abnormal/error/close events (stylesheet).
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

  constructor(
    private cy: Core,
    container: HTMLElement,
    private getZones: () => ZoneInfo[],
  ) {
    this.canvas = document.createElement('canvas')
    this.canvas.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:0;'
    // first child => cytoscape's own canvases (created earlier) paint above
    container.insertBefore(this.canvas, container.firstChild)
    const ctx = this.canvas.getContext('2d')
    if (!ctx) throw new Error('canvas 2d unavailable')
    this.ctx = ctx
    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(container)
    this.resize()
    cy.on('render', this.requestDraw)
    cy.on('destroy', this.onDestroy)
    this.requestDraw()
  }

  private onDestroy = (): void => {
    this.destroyed = true
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
    if (this.destroyed) return
    if (this.raf) return
    this.raf = requestAnimationFrame(() => {
      this.raf = 0
      this.draw()
    })
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
    const cy = this.cy
    const ctx = this.ctx
    ctx.clearRect(0, 0, this.cssW, this.cssH)
    if (cy.nodes().length === 0) return
    const zoom = cy.zoom()
    const pan = cy.pan()
    const toX = (mx: number): number => mx * zoom + pan.x
    const toY = (my: number): number => my * zoom + pan.y

    // --- edge glow underlay (real rendered curves, behind the cards) -------
    const edges = cy.edges(':visible')
    // defensive cap: huge fixture graphs skip the decorative pass
    if (edges.length <= 2600) {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      for (const e of edges) {
        const kind = String(e.data('kind') ?? '')
        const color = EDGE_KIND_COLORS[kind] ?? DEFAULT_EDGE_COLOR
        let alpha = 0.17
        if (e.hasClass('ai-dim')) alpha *= 0.22
        if (e.hasClass('fading')) continue // closed-connection fade: no glow
        const s = e.sourceEndpoint()
        const t = e.targetEndpoint()
        if (!s || !t || typeof s.x !== 'number' || typeof t.x !== 'number') continue
        const cp = this.controlPointOf(e)
        const w = Math.max(3.0, 4.6 * zoom)
        ctx.strokeStyle = color
        ctx.globalAlpha = alpha
        ctx.lineWidth = w
        ctx.beginPath()
        ctx.moveTo(toX(s.x), toY(s.y))
        if (cp) ctx.quadraticCurveTo(toX(cp.x), toY(cp.y), toX(t.x), toY(t.y))
        else ctx.lineTo(toX(t.x), toY(t.y))
        ctx.stroke()
        // inner brighter pass -> soft neon falloff
        ctx.globalAlpha = alpha * 0.95
        ctx.lineWidth = Math.max(1.5, 2.2 * zoom)
        ctx.stroke()
      }
      ctx.globalAlpha = 1
    }

    // --- faint zone headers (semantic regions of the composed map) ---------
    for (const z of this.getZones()) {
      const x0 = toX(z.x0)
      const x1 = toX(z.x1)
      const y = toY(z.y0)
      const w = x1 - x0
      if (w < 70) continue // far-out: skip micro zones
      if (y < -34 || y > this.cssH + 34) continue
      // thin rule under the label
      ctx.strokeStyle = 'rgba(126,154,186,0.18)'
      ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x0, y + 12)
      ctx.lineTo(x1, y + 12)
      ctx.stroke()
      // label
      ctx.font = '12px Consolas, "Cascadia Mono", monospace'
      ctx.fillStyle = 'rgba(150,174,202,0.46)'
      try {
        ;(ctx as unknown as { letterSpacing: string }).letterSpacing = '3px'
      } catch { /* older engines */ }
      ctx.fillText(z.label, x0, y)
      try {
        ;(ctx as unknown as { letterSpacing: string }).letterSpacing = '0px'
      } catch { /* older engines */ }
    }
  }

  destroy(): void {
    this.destroyed = true
    this.ro.disconnect()
    this.canvas.remove()
  }
}
