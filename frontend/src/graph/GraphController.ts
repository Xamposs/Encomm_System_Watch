import cytoscape, {
  type Core,
  type EdgeSingular,
  type ElementDefinition,
  type StylesheetStyle,
  type ZoomOptions,
} from 'cytoscape'
import fcose from 'cytoscape-fcose'
import type { Filter, SystemEvent, TopoEdge, TopoNode } from '../types/system'
import { EdgePulseOverlay } from './EdgePulseOverlay'

cytoscape.use(fcose)

export const STYLESHEET: StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      'background-color': '#0c1520',
      'border-color': '#22374a',
      'border-width': 1,
      color: '#9fb4c8',
      'font-family': 'Consolas, "Cascadia Mono", monospace',
      'font-size': 9,
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      'text-max-width': '175px',
      'text-overflow-wrap': 'anywhere',
      'overlay-color': '#22d3ee',
      'overlay-opacity': 0,
      'overlay-padding': 6,
      'transition-property': 'opacity, border-color, background-color, line-color, width, overlay-opacity, border-width',
      'transition-duration': '250ms' as unknown as number,
    },
  },
  {
    selector: 'node[kind = "PROCESS"]',
    style: { shape: 'round-rectangle', width: 165, height: 36, 'border-color': '#2b4257' },
  },
  {
    selector: 'node[kind = "SYSTEM"]',
    style: {
      shape: 'round-rectangle', width: 200, height: 56, 'border-color': '#3d5a78',
      'border-width': 2, 'background-color': '#0a141f', 'font-size': 10,
    },
  },
  {
    selector: 'node[kind = "EXTERNAL_ENDPOINT"]',
    style: {
      shape: 'ellipse', width: 100, height: 26, 'background-color': '#0e141b',
      'border-color': '#31404e', color: '#7d93a8', 'font-size': 8.5,
    },
  },
  {
    selector: 'node[kind = "LISTENING_PORT"]',
    style: {
      shape: 'square' as never, width: 34, height: 22, 'background-color': '#0d1a16',
      'border-color': '#2f5d49', color: '#7fbfa4', 'font-size': 8.5,
    },
  },
  {
    selector: 'node[kind = "LOCAL_ENDPOINT"]',
    style: {
      shape: 'square' as never, width: 44, height: 22, 'background-color': '#12141a',
      'border-color': '#3a3f52', color: '#8b93a8', 'font-size': 8,
    },
  },
  { selector: 'node[?born]', style: { 'border-color': '#35e0ff', 'border-width': 2 } },
  { selector: 'node[?highCpu]', style: { 'border-color': '#f0a63c' } },
  { selector: 'node[?inspected]', style: { 'border-color': '#35e0ff', 'border-width': 2, 'background-color': '#0f1c2c' } },
  { selector: 'node[?searchMatch]', style: { 'overlay-opacity': 0.2 } },
  { selector: 'node[?dimmed]', style: { opacity: 0.18 } },
  { selector: 'node[?hidden]', style: { display: 'none' } },
  { selector: 'edge[?hidden]', style: { display: 'none' } },
  {
    selector: 'edge',
    style: {
      'line-color': '#1a2836',
      width: 1.1,
      'curve-style': 'bezier',
      opacity: 0.75,
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#1a2836',
      'arrow-scale': 0.55,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': '300ms' as unknown as number,
    },
  },
  { selector: 'edge[kind = "LOCALHOST"]', style: { 'line-color': '#2b5f70', 'target-arrow-shape': 'none' } },
  { selector: 'edge[kind = "LISTEN"]', style: { 'line-color': '#3d5a36', 'target-arrow-shape': 'none', 'line-style': 'dashed' } },
  { selector: 'edge[?active]', style: { 'line-color': '#2e5266', 'target-arrow-color': '#2e5266' } },
  { selector: 'edge[?recent]', style: { 'line-color': '#3d7a8f', 'target-arrow-color': '#3d7a8f' } },
  { selector: 'edge.pulse', style: { 'line-color': '#35e0ff', 'target-arrow-color': '#35e0ff', width: 2.2 } },
  { selector: 'edge.pulse-close', style: { 'line-color': '#ff5d5d', 'target-arrow-color': '#ff5d5d', width: 2.2 } },
  { selector: 'edge.fading', style: { opacity: 0 } },
  { selector: 'node.fading', style: { opacity: 0 } },
  { selector: 'node.no-labels', style: { label: '' } }, // hide labels at low zoom
  { selector: 'edge:selected', style: { 'line-color': '#35e0ff', width: 2 } },
]

function portLabel(ports: number[]): string {
  const uniq = [...new Set(ports)].sort((a, b) => a - b)
  const head = uniq.slice(0, 4).join(', ')
  return uniq.length > 4 ? `${head} +${uniq.length - 4}` : head
}

/**
 * Imperative controller over a single Cytoscape instance. All graph mutations
 * happen here (cy.batch), bypassing React — React only drives stats/events/
 * selection. Created once per mount; never recreated per snapshot.
 */
export class GraphController {
  private overlay: EdgePulseOverlay
  private pendingNodeRemoves = new Set<string>()
  private pendingEdgeRemoves = new Set<string>()
  private layoutTimer: number | undefined
  private pendingNewNodes = 0
  private filter: Filter = 'all'
  private search = ''
  private tooltip: HTMLDivElement | null = null

  constructor(
    private cy: Core,
    container: HTMLElement,
  ) {
    this.overlay = new EdgePulseOverlay(cy, container)
    cy.on('add', 'node', () => {
      this.pendingNewNodes += 1
      this.scheduleIncrementalLayout()
    })
    cy.on('mouseover', 'edge', (ev) => this.showTooltip(ev.target as EdgeSingular))
    cy.on('mouseout', 'edge', () => this.hideTooltip())
    cy.on('zoom', () => this.updateLabelVisibility())
  }

  /** Labels only above a readable zoom threshold — fit view stays a clean map. */
  private labelsVisible = true

  private updateLabelVisibility(): void {
    const show = this.cy.zoom() >= 0.45
    if (show === this.labelsVisible) return
    this.labelsVisible = show
    this.cy.batch(() => this.cy.nodes().toggleClass('no-labels', !show))
  }

  // ---------------------------------------------------------------- snapshot

  replaceAll(nodes: TopoNode[], edges: TopoEdge[]): void {
    this.pendingNodeRemoves.clear()
    this.pendingEdgeRemoves.clear()
    this.cy.batch(() => {
      this.cy.elements().remove()
      const defs: ElementDefinition[] = [
        ...nodes.map((n) => ({ group: 'nodes' as const, data: this.flattenNode(n) })),
        ...edges.map((e) => ({
          group: 'edges' as const,
          data: { ...e, portLabel: portLabel(e.ports) },
        })),
      ]
      this.cy.add(defs)
    })
    this.pendingNewNodes = 0
    this.runLayout('initial')
  }

  private flattenNode(n: TopoNode): Record<string, unknown> {
    const { data, ...rest } = n
    return { ...rest, ...data }
  }

  // ------------------------------------------------------------------ events

  applyEvent(ev: SystemEvent): void {
    switch (ev.event_type) {
      case 'PROCESS_STARTED': {
        const node = ev.metadata?.node as TopoNode | undefined
        if (node) this.upsertNode(node, true)
        break
      }
      case 'PROCESS_STOPPED':
        this.fadeRemoveNode(ev.source)
        break
      case 'CONNECTION_OPENED': {
        const m = ev.metadata
        if (m?.src_node) this.upsertNode(m.src_node as TopoNode, false)
        if (m?.tgt_node) this.upsertNode(m.tgt_node as TopoNode, false)
        this.upsertEdge({
          id: m.edge_id,
          source: m.src_node.id,
          target: m.tgt_node.id,
          kind: m.kind,
          proto: m.proto,
          ports: [m.edge_port].filter((p: number) => typeof p === 'number'),
          active: true,
          directed: m.kind !== 'LOCALHOST',
        })
        this.pulseEdge(m.edge_id, 'open')
        break
      }
      case 'CONNECTION_CLOSED': {
        const m = ev.metadata
        if ((m?.remaining ?? 0) > 0) {
          this.upsertEdgePorts(m.edge_id, m.ports)
          this.pulseEdge(m.edge_id, 'update')
        } else {
          this.pulseEdge(m.edge_id, 'close')
          this.fadeRemoveEdge(m.edge_id)
        }
        break
      }
      case 'PROCESS_METRICS_UPDATED': {
        const m = ev.metadata
        const el = this.cy.getElementById(ev.source)
        if (el.length) {
          this.cy.batch(() => {
            el.data('cpu_percent', m.cpu_percent)
            el.data('memory_mb', m.memory_mb)
            el.data('num_threads', m.num_threads)
            el.data('status', m.status)
            el.data('highCpu', (m.cpu_percent ?? 0) >= 25 ? true : undefined)
            el.data(
              'label',
              `${el.data('name')}\nPID ${el.data('pid')} · CPU ${Number(m.cpu_percent).toFixed(1)}% · ${Math.round(Number(m.memory_mb))}MB`,
            )
          })
        }
        break
      }
    }
  }

  private upsertNode(node: TopoNode, born: boolean): void {
    const existing = this.cy.getElementById(node.id)
    if (existing.length) {
      this.pendingNodeRemoves.delete(node.id)
      existing.removeClass('fading')
      this.cy.batch(() => {
        existing.data(this.flattenNode(node))
      })
      return
    }
    const data = this.flattenNode(node)
    if (born) data.born = true
    // place new nodes near the current view center (not model origin) so they
    // join the visible graph; the incremental fcose pass refines positions
    const ext = this.cy.extent()
    const position = {
      x: (ext.x1 + ext.x2) / 2 + (Math.random() - 0.5) * 160,
      y: (ext.y1 + ext.y2) / 2 + (Math.random() - 0.5) * 160,
    }
    this.cy.add({ group: 'nodes', data, position })
    if (born) {
      window.setTimeout(() => {
        const el = this.cy.getElementById(node.id)
        if (el.length) el.removeData('born')
      }, 800)
    }
  }

  private upsertEdge(edge: TopoEdge): void {
    const existing = this.cy.getElementById(edge.id)
    if (existing.length) {
      this.pendingEdgeRemoves.delete(edge.id)
      existing.removeClass('fading pulse pulse-close')
      this.cy.batch(() => {
        existing.data('ports', edge.ports)
        existing.data('portLabel', portLabel(edge.ports))
        existing.data('active', edge.active)
        existing.data('recent', true)
      })
    } else {
      this.cy.add({
        group: 'edges',
        data: { ...edge, portLabel: portLabel(edge.ports), recent: true },
      })
    }
    window.setTimeout(() => {
      const el = this.cy.getElementById(edge.id)
      if (el.length) el.removeData('recent')
    }, 3500)
  }

  private upsertEdgePorts(edgeId: string, ports: number[]): void {
    const el = this.cy.getElementById(edgeId)
    if (!el.length) return
    const list = Array.isArray(ports) ? ports : []
    this.cy.batch(() => {
      el.data('ports', list)
      el.data('portLabel', portLabel(list))
    })
  }

  private pulseEdge(edgeId: string, kind: 'open' | 'close' | 'update'): void {
    const el = this.cy.getElementById(edgeId)
    if (el.length) {
      el.addClass(kind === 'close' ? 'pulse-close' : 'pulse')
      window.setTimeout(() => {
        const cur = this.cy.getElementById(edgeId)
        if (cur.length) cur.removeClass('pulse pulse-close')
      }, 500)
    }
    this.overlay.pulse(edgeId, kind)
  }

  private fadeRemoveEdge(edgeId: string): void {
    const el = this.cy.getElementById(edgeId)
    if (!el.length) return
    this.pendingEdgeRemoves.add(edgeId)
    el.addClass('fading')
    window.setTimeout(() => {
      if (this.pendingEdgeRemoves.has(edgeId)) {
        this.pendingEdgeRemoves.delete(edgeId)
        const cur = this.cy.getElementById(edgeId)
        if (cur.length) cur.remove()
      }
    }, 600)
  }

  private fadeRemoveNode(nodeId: string): void {
    const el = this.cy.getElementById(nodeId)
    if (!el.length) return
    this.pendingNodeRemoves.add(nodeId)
    const connected = el.connectedEdges()
    el.addClass('fading')
    connected.addClass('fading')
    window.setTimeout(() => {
      if (this.pendingNodeRemoves.has(nodeId)) {
        this.pendingNodeRemoves.delete(nodeId)
        const cur = this.cy.getElementById(nodeId)
        if (cur.length) cur.remove()
      }
    }, 600)
  }

  // ------------------------------------------------------------ layout / view

  private runLayout(kind: 'initial' | 'incremental'): void {
    const options = {
      name: 'fcose',
      quality: 'default',
      randomize: kind === 'initial',
      animate: kind !== 'initial',
      animationDuration: 350,
      nodeRepulsion: kind === 'initial' ? 20000 : 6000,
      idealEdgeLength: kind === 'initial' ? 170 : 110,
      gravity: kind === 'initial' ? 0.05 : 0.2,
      numIter: kind === 'initial' ? 2000 : 300,
      // tiling arranges components into a rigid grid — off for an organic map
      tile: false,
      padding: 30,
      // incremental runs must not re-fit the view (keeps user's zoom/pan stable)
      fit: kind === 'initial',
    }
    try {
      this.cy.layout(options).run()
    } catch {
      this.cy.layout({ name: 'cose', animate: false } as never).run()
    }
  }

  private scheduleIncrementalLayout(): void {
    window.clearTimeout(this.layoutTimer)
    this.layoutTimer = window.setTimeout(() => {
      if (this.pendingNewNodes > 0) {
        this.pendingNewNodes = 0
        this.runLayout('incremental')
      }
    }, 2000)
  }

  fit(): void {
    this.cy.fit(undefined, 40)
  }

  zoomIn(): void {
    this.cy.zoom({ level: this.cy.zoom() * 1.3 } as unknown as ZoomOptions)
  }

  zoomOut(): void {
    this.cy.zoom({ level: this.cy.zoom() / 1.3 } as unknown as ZoomOptions)
  }

  // ------------------------------------------------------------ filter/search

  setFilter(filter: Filter): void {
    this.filter = filter
    this.applyFilter()
  }

  private applyFilter(): void {
    const f = this.filter
    const nodes = this.cy.nodes()
    const edges = this.cy.edges()
    this.cy.batch(() => {
      nodes.removeData('hidden')
      edges.removeData('hidden')
      if (f === 'all') return
      if (f === 'active') {
        const keep = new Set<string>()
        edges.filter((e) => e.data('active') === true).forEach((e) => {
          keep.add(e.source().id())
          keep.add(e.target().id())
        })
        edges.filter((e) => e.data('active') !== true).data('hidden', true)
        nodes.forEach((n) => {
          if (!keep.has(n.id())) n.data('hidden', true)
        })
      } else if (f === 'listening') {
        const keep = new Set<string>()
        edges.filter((e) => e.data('kind') === 'LISTEN').forEach((e) => {
          keep.add(e.source().id())
          keep.add(e.target().id())
        })
        edges.filter((e) => e.data('kind') !== 'LISTEN').data('hidden', true)
        nodes.forEach((n) => {
          if (!keep.has(n.id())) n.data('hidden', true)
        })
      } else if (f === 'highcpu') {
        const keep = new Set<string>()
        nodes.filter((n) => ((n.data('cpu_percent') as number | undefined) ?? 0) >= 20)
          .forEach((n) => {
            keep.add(n.id())
          })
        edges.forEach((e) => {
          if (!keep.has(e.source().id()) || !keep.has(e.target().id())) e.data('hidden', true)
        })
        nodes.forEach((n) => {
          if (!keep.has(n.id())) n.data('hidden', true)
        })
      }
    })
  }

  setSearch(query: string): void {
    this.search = query.trim().toLowerCase()
    this.applySearch()
  }

  private applySearch(): void {
    const q = this.search
    this.cy.batch(() => {
      this.cy.nodes().removeData('searchMatch').removeData('dimmed')
      if (!q) return
      this.cy.nodes().forEach((n) => {
        if (n.data('kind') !== 'PROCESS') {
          n.data('dimmed', true)
          return
        }
        const name = String(n.data('name') ?? '').toLowerCase()
        const exe = String(n.data('exe') ?? '').toLowerCase()
        const pid = String(n.data('pid') ?? '')
        const hit = name.includes(q) || exe.includes(q) || pid.includes(q)
        if (hit) n.data('searchMatch', true)
        else n.data('dimmed', true)
      })
    })
  }

  // ------------------------------------------------------------- inspector

  select(nodeId: string | null): void {
    this.cy.nodes('[?inspected]').removeData('inspected')
    if (nodeId) {
      const el = this.cy.getElementById(nodeId)
      if (el.length) el.data('inspected', true)
    }
  }

  getNodeData(nodeId: string): Record<string, unknown> | null {
    const el = this.cy.getElementById(nodeId)
    if (!el.length) return null
    return { ...el.data() }
  }

  // --------------------------------------------------------------- tooltip

  private showTooltip(edge: EdgeSingular): void {
    const d = edge.data()
    const ports = (d.ports as number[] | undefined) ?? []
    const uniq = [...new Set(ports)].sort((a, b) => a - b)
    const mid = edge.midpoint()
    const pos = {
      x: mid.x * this.cy.zoom() + this.cy.pan().x,
      y: mid.y * this.cy.zoom() + this.cy.pan().y,
    }
    const kindText = String(d.kind ?? '')
    const text =
      `${kindText} · ${String(d.proto ?? '')} · ${uniq.length} conn${uniq.length === 1 ? '' : 's'}` +
      (uniq.length ? ` · ports ${uniq.slice(0, 6).join(', ')}${uniq.length > 6 ? '…' : ''}` : '')
    if (!this.tooltip) {
      this.tooltip = document.createElement('div')
      this.tooltip.className = 'edge-tooltip'
      this.cy.container()?.parentElement?.appendChild(this.tooltip)
    }
    this.tooltip.textContent = text
    this.tooltip.style.left = `${pos.x + 12}px`
    this.tooltip.style.top = `${pos.y - 12}px`
    this.tooltip.style.display = 'block'
  }

  private hideTooltip(): void {
    if (this.tooltip) this.tooltip.style.display = 'none'
  }

  destroy(): void {
    window.clearTimeout(this.layoutTimer)
    this.overlay.destroy()
    this.tooltip?.remove()
  }
}
