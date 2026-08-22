import type { Core, NodeSingular } from 'cytoscape'
import { DEFAULT_EDGE_COLOR, EDGE_KIND_COLORS } from './WireUnderlay'
import { perf } from './PerfMonitor'

/** Tie-break order for a node's dominant edge kind (socket color). */
const SOCKET_PRIORITY = [
  'EXTERNAL',
  'LOCALHOST',
  'HOSTS',
  'HOSTED_BY',
  'LISTEN',
  'USES_GPU',
  'SERVES_MODEL',
  'AI_CALL',
  'LOCAL_API',
  'SPAWNED',
  'MEMBER_OF',
  'BACKED_BY',
  'EXPOSES',
  'CONNECTED_TO',
  'PROCESS_PARENT',
]

const NODE_KIND_COLORS: Record<string, string> = {
  SERVICE: '#d7aa57', GPU: '#71d8b1', LISTENING_PORT: '#71d8b1',
  SEMANTIC: '#e477b9', AI_RUNTIME: '#e477b9', EXTERNAL_ENDPOINT: '#70c8eb',
  PROCESS: '#52d9ed', SYSTEM: '#d7aa57', CONTAINER: '#71d8b1', VM: '#b99af2',
}

/**
 * Top connection-socket canvas (v1.0.2 final pass).
 *
 * Draws small circular "ports" on the left/right border midpoint of every
 * node with incident edges — the reference-style connection points that
 * make cards read as connected modules. Socket color = dominant incident
 * edge kind (real relationships only). Redraws on cytoscape render events,
 * rAF-throttled; skips nodes with no edges, hidden/dimmed elements, and
 * very large graphs (defensive cap).
 */
export class SocketOverlay {
  private canvas: HTMLCanvasElement
  private ctx: CanvasRenderingContext2D
  private raf = 0
  private destroyed = false
  private ro: ResizeObserver
  private cssW = 1
  private cssH = 1
  private colorCache = new Map<string, string>()
  private titleCache = new Map<string, string>()
  private cacheDirty = true
  private interacting = false

  constructor(
    private cy: Core,
    private container: HTMLElement,
  ) {
    this.canvas = document.createElement('canvas')
    this.canvas.className = 'graph-socket-overlay'
    this.canvas.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:14;'
    container.appendChild(this.canvas)
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
    // topology changes invalidate the per-node dominant-kind cache
    cy.on('add remove', 'edge', this.onTopologyChange)
    cy.on('destroy', this.onDestroy)
    this.requestDraw()
  }

  private onDestroy = (): void => {
    this.destroyed = true
  }

  private onTopologyChange = (): void => {
    this.cacheDirty = true
    this.titleCache.clear()
    this.requestDraw()
  }

  /**
   * Keep compact-LOD labels cheap: normalize/truncate once per node and size
   * tier instead of measuring text on every pan/zoom frame.
   */
  private compactTitle(node: NodeSingular, maxChars: number): string {
    const raw = String(
      node.data('cardTitle') ??
      node.data('display_name') ??
      node.data('name') ??
      node.data('label') ??
      node.id(),
    ).replace(/\s+/g, ' ').trim()
    const key = `${node.id()}\u0000${maxChars}\u0000${raw}`
    const cached = this.titleCache.get(key)
    if (cached !== undefined) return cached
    const title = raw.length <= maxChars
      ? raw
      : `${raw.slice(0, Math.max(1, maxChars - 1))}…`
    if (this.titleCache.size > 6000) this.titleCache.clear()
    this.titleCache.set(key, title)
    return title
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
    if (this.destroyed) return
    if (this.raf) return
    perf.setOverlayRaf('sockets', true)
    this.raf = requestAnimationFrame(() => {
      this.raf = 0
      try { this.draw() } finally { perf.setOverlayRaf('sockets', false) }
    })
  }

  /** Rebuild nodeId -> dominant incident edge kind color. */
  private rebuildCache(): void {
    this.colorCache.clear()
    const cy = this.cy
    const counts = new Map<string, Map<string, number>>()
    for (const e of cy.edges(':visible')) {
      let kind = String(e.data('kind') ?? '')
      if (!EDGE_KIND_COLORS[kind]) kind = ''
      try {
        const s = e.source().id()
        const t = e.target().id()
        if (!s || !t) continue
        let m = counts.get(s)
        if (!m) counts.set(s, (m = new Map()))
        m.set(kind, (m.get(kind) ?? 0) + 1)
        let m2 = counts.get(t)
        if (!m2) counts.set(t, (m2 = new Map()))
        m2.set(kind, (m2.get(kind) ?? 0) + 1)
      } catch {
        /* dangling edge — ignore */
      }
    }
    for (const nd of cy.nodes(':visible')) {
      const m = counts.get(nd.id())
      if (!m || m.size === 0) continue
      let bestKind = ''
      let best = 0
      for (const k of SOCKET_PRIORITY) {
        const c = m.get(k) ?? 0
        if (c > best) {
          best = c
          bestKind = k
        }
      }
      this.colorCache.set(nd.id(), EDGE_KIND_COLORS[bestKind] ?? DEFAULT_EDGE_COLOR)
    }
    this.cacheDirty = false
  }

  private draw(): void {
    const started = performance.now()
    const cy = this.cy
    const ctx = this.ctx
    ctx.clearRect(0, 0, this.cssW, this.cssH)
    const zoom = cy.zoom()
    if (zoom < 0.5) {
      // MID/FAR LOD: fixed-screen canvas mini-cards. Cytoscape model units
      // shrink with zoom, so relying on native borders alone turns nodes into
      // hairlines. This pass preserves a clear rectangular representation
      // without mounting any HTML DOM cards.
      const far = zoom < 0.09
      this.canvas.dataset.mode = far ? 'far-mini' : 'mid-mini'
      // Fixed-screen tiers follow the available screen-space pitch. At the
      // usual FIT ALL zoom (~0.2), 48x17 cards leave a small gap while still
      // carrying a readable process/service name.
      const width = far ? 18 : zoom < 0.18 ? 42 : zoom < 0.28 ? 48 : zoom < 0.39 ? 62 : 78
      const height = far ? 9 : zoom < 0.18 ? 15 : zoom < 0.28 ? 17 : zoom < 0.39 ? 20 : 23
      const fontSize = far ? 5.5 : zoom < 0.18 ? 6 : zoom < 0.28 ? 7 : zoom < 0.39 ? 8 : 9
      const maxChars = far ? 2 : zoom < 0.18 ? 7 : zoom < 0.28 ? 9 : zoom < 0.39 ? 11 : 14
      const nodes = cy.nodes(':visible')
      const stride = Math.max(1, Math.ceil(nodes.length / 1400))
      ctx.lineWidth = far ? 0.8 : 1
      ctx.font = `600 ${fontSize}px Consolas, "Cascadia Mono", monospace`
      ctx.textBaseline = 'middle'
      let labeledNodes = 0
      for (let i = 0; i < nodes.length; i += stride) {
        const node = nodes[i]
        const p = node.renderedPosition()
        if (p.x < -width || p.x > this.cssW + width || p.y < -height || p.y > this.cssH + height) continue
        const color = NODE_KIND_COLORS[String(node.data('kind') ?? '')] ?? '#6da3bd'
        ctx.fillStyle = far ? 'rgba(16,31,48,0.92)' : 'rgba(15,27,44,0.94)'
        ctx.strokeStyle = color
        ctx.beginPath()
        ctx.roundRect(p.x - width / 2, p.y - height / 2, width, height, far ? 2 : 3)
        ctx.fill()
        ctx.stroke()
        const title = this.compactTitle(node, maxChars)
        if (!title) continue
        ctx.fillStyle = color
        if (far) {
          ctx.textAlign = 'center'
          ctx.fillText(title, p.x, p.y + 0.25)
        } else {
          ctx.fillRect(p.x - width / 2 + 3, p.y - height / 2 + 3, 1.5, height - 6)
          ctx.textAlign = 'left'
          ctx.fillText(title, p.x - width / 2 + 7, p.y + 0.25)
        }
        labeledNodes += 1
      }
      this.canvas.dataset.labeledNodes = String(labeledNodes)
      this.canvas.dataset.cardWidth = String(width)
      this.canvas.dataset.cardHeight = String(height)
      perf.recordOverlayDraw('socket', performance.now() - started)
      return
    }
    this.canvas.dataset.mode = this.interacting ? 'interaction-paused' : 'near-sockets'
    this.canvas.dataset.labeledNodes = '0'
    delete this.canvas.dataset.cardWidth
    delete this.canvas.dataset.cardHeight
    if (this.interacting) {
      perf.recordOverlayDraw('socket', performance.now() - started)
      return
    }
    const nodes = cy.nodes(':visible')
    if (nodes.length === 0 || nodes.length > 3000) {
      perf.recordOverlayDraw('socket', performance.now() - started)
      return
    }
    if (this.cacheDirty) this.rebuildCache()
    const pan = cy.pan()
    const r = Math.max(2.2, 4.4 * zoom)
    ctx.lineWidth = Math.max(1.1, 1.4 * Math.min(1, zoom))
    for (const nd of nodes) {
      const info = this.colorCache.get(nd.id())
      if (!info) continue
      if (nd.hasClass('ai-dim') || nd.hasClass('focus-dim')) continue
      if (nd.hasClass('fading')) continue
      const p = nd.position()
      const hw = (nd.width() as number) / 2
      const xl = p.x - hw + 1
      const xr = p.x + hw - 1
      const y = p.y
      const sx = xl * zoom + pan.x
      const sy = y * zoom + pan.y
      const ex = xr * zoom + pan.x
      if (sy < -12 || sy > this.cssH + 12) continue
      if ((sx < -12 && ex < -12) || (sx > this.cssW + 12 && ex > this.cssW + 12)) continue
      // dark inset + colored ring (connection port)
      ctx.fillStyle = 'rgba(8,12,18,0.92)'
      ctx.strokeStyle = info
      ctx.beginPath()
      ctx.arc(sx, sy, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
      ctx.beginPath()
      ctx.arc(ex, sy, r, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
    perf.recordOverlayDraw('socket', performance.now() - started)
  }

  destroy(): void {
    this.destroyed = true
    if (this.raf) cancelAnimationFrame(this.raf)
    perf.setOverlayRaf('sockets', false)
    this.ro.disconnect()
    this.titleCache.clear()
    this.cy.off('pan zoom resize', this.requestDraw)
    this.cy.off('position', 'node', this.requestDraw)
    this.cy.off('add remove', 'edge', this.onTopologyChange)
    this.canvas.parentElement?.removeEventListener('esw:layout', this.requestDraw)
    this.container.removeEventListener('esw:interaction', this.onInteraction)
    this.canvas.remove()
  }
}
