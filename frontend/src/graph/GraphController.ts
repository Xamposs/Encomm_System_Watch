import cytoscape, {
  type Core,
  type EdgeSingular,
  type ElementDefinition,
  type NodeSingular,
  type StylesheetStyle,
  type ZoomOptions,
} from 'cytoscape'
import fcose from 'cytoscape-fcose'
import type {
  AiTelemetryEvent,
  Filter,
  GpuInfo,
  NetworkActivityItem,
  NetworkActivityNode,
  SystemEvent,
  TelemetryInfo,
  TopoEdge,
  TopoNode,
  ViewMode,
} from '../types/system'
import { EdgePulseOverlay, RECENT_MS } from './EdgePulseOverlay'
import { perf } from './PerfMonitor'

cytoscape.use(fcose)

export function fmtBps(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)} MB/s`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)} KB/s`
  return `${Math.round(v)} B/s`
}

/**
 * Deterministic per-edge curve offset (v0.6.0 UI fidelity): every edge gets a
 * stable, mildly different bezier control point derived from its id and its
 * endpoints' distance — fine technical wiring instead of rigid straight
 * flowchart lines. Pure function of element state (no cy mutation), so it is
 * safe inside a cytoscape style mapper.
 */
function edgeCurveDist(e: EdgeSingular): number {
  const s = e.source().position()
  const t = e.target().position()
  const len = Math.hypot(t.x - s.x, t.y - s.y) || 1
  const id = e.id()
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  const sign = h % 2 === 0 ? 1 : -1
  const d = Math.min(120, Math.max(30, len * 0.18))
  return sign * d
}

export const STYLESHEET: StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      // cytoscape 3.34 does NOT apply the data(label) default mapping — an
      // explicit mapping is required or every card renders blank (v0.6.0)
      label: 'data(label)',
      'background-color': '#141d2c',
      'border-color': '#4a6a92',
      'border-width': 1,
      color: '#c4d8ec',
      'font-family': 'Consolas, "Cascadia Mono", monospace',
      'font-size': 12,
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      'text-max-width': '95px',
      'text-overflow-wrap': 'anywhere',
      // subtle backing behind label text keeps dense wiring from slicing
      // through card text (reference control-room readability)
      'text-background-color': '#0a0f16',
      'text-background-opacity': 0.55,
      'text-background-padding': '2px',
      'overlay-color': '#22d3ee',
      'overlay-opacity': 0,
      'overlay-padding': 6,
      'transition-property': 'opacity, border-color, background-color, line-color, width, overlay-opacity, border-width',
      'transition-duration': '250ms' as unknown as number,
    },
  },
  {
    selector: 'node[kind = "PROCESS"]',
    style: { shape: 'round-rectangle', width: 108, height: 38, 'border-color': '#5f83ad', 'border-width': 1 },
  },
  {
    selector: 'node[kind = "PROCESS"].zoom-close',
    style: { 'font-size': 11, height: 44 },
  },
  {
    selector: 'node[kind = "SYSTEM"]',
    style: {
      shape: 'round-rectangle', width: 150, height: 40, 'border-color': '#6f96c2',
      'border-width': 1.5, 'background-color': '#1b2f47', 'font-size': 13,
    },
  },
  {
    selector: 'node[kind = "EXTERNAL_ENDPOINT"]',
    style: {
      shape: 'ellipse', width: 66, height: 16, 'background-color': '#1a2533',
      'border-color': '#5c7590', color: '#9db6cd', 'font-size': 10,
      'text-valign': 'top', 'text-margin-y': 2,
    },
  },
  {
    selector: 'node[kind = "LISTENING_PORT"]',
    style: {
      shape: 'square' as never, width: 24, height: 12, 'background-color': '#143024',
      'border-color': '#5da383', color: '#9fe0c2', 'font-size': 9.5,
      'text-valign': 'top', 'text-margin-y': 2,
    },
  },
  {
    selector: 'node[kind = "LOCAL_ENDPOINT"]',
    style: {
      shape: 'square' as never, width: 32, height: 14, 'background-color': '#1b2130',
      'border-color': '#72809f', color: '#aab4cf', 'font-size': 9.5,
      'text-valign': 'top', 'text-margin-y': 2,
    },
  },
  // ---- semantic nodes (GPU + AI observability, v0.3.0) --------------------
  // strong styling only for HIGH/CONFIRMED; MEDIUM/LOW stay subdued
  {
    selector: 'node[kind = "SEMANTIC"]',
    style: {
      shape: 'round-rectangle', width: 106, height: 32, 'background-color': '#231a3d',
      'border-color': '#8b5cf6', 'border-width': 1.4, color: '#d8c9ff',
      'border-style': 'double', 'font-size': 11.5,
    },
  },
  {
    selector: 'node[kind = "SEMANTIC"][confidence = "MEDIUM"], node[kind = "SEMANTIC"][confidence = "LOW"]',
    style: {
      'border-width': 1, 'border-style': 'dashed', 'background-color': '#191a2e',
      'border-color': '#6d5c9e', color: '#a99ac9', opacity: 0.85,
    },
  },
  {
    selector: 'node[kind = "LOCAL_LLM"]',
    style: {
      shape: 'round-rectangle', width: 100, height: 30, 'background-color': '#10273a',
      'border-color': '#38bdf8', 'border-width': 1.2, color: '#a5e3ff', 'font-size': 11.5,
    },
  },
  {
    selector: 'node[kind = "GPU"]',
    style: {
      shape: 'round-rectangle', width: 118, height: 38, 'background-color': '#12251f',
      'border-color': '#34d399', 'border-width': 1.4, color: '#a7f3d0', 'font-size': 11.5,
    },
  },
  // ---- infrastructure nodes (v0.4.0) — compact control-room identities ----
  {
    selector: 'node[kind = "SERVICE"]',
    style: {
      shape: 'round-rectangle', width: 112, height: 36, 'background-color': '#2a2517',
      'border-color': '#d9a03c', 'border-width': 1.2, color: '#f0d9a8', 'font-size': 11.5,
    },
  },
  {
    selector: 'node[kind = "WSL"]',
    style: {
      shape: 'hexagon', width: 92, height: 26, 'background-color': '#0f2730',
      'border-color': '#22d3ee', 'border-width': 1.2, color: '#a5f3fc', 'font-size': 11,
    },
  },
  {
    selector: 'node[kind = "DOCKER_ENGINE"]',
    style: {
      shape: 'diamond', width: 108, height: 32, 'background-color': '#12203a',
      'border-color': '#3b82f6', 'border-width': 1.4, color: '#bfdbfe', 'font-size': 11.5,
    },
  },
  {
    selector: 'node[kind = "CONTAINER"]',
    style: {
      shape: 'diamond', width: 96, height: 28, 'background-color': '#0f2a23',
      'border-color': '#2dd4bf', 'border-width': 1.2, color: '#99f6e4', 'font-size': 11,
    },
  },
  {
    selector: 'node[kind = "DOCKER_NETWORK"]',
    style: {
      shape: 'ellipse', width: 72, height: 16, 'background-color': '#161b26',
      'border-color': '#64748b', color: '#94a3b8', 'font-size': 9.5,
      'text-valign': 'top', 'text-margin-y': 2,
    },
  },
  {
    selector: 'node[kind = "VM"]',
    style: {
      shape: 'round-rectangle', width: 106, height: 32, 'background-color': '#2a1220',
      'border-color': '#d946ef', 'border-width': 1.3, color: '#f5d0fe', 'font-size': 11.5,
    },
  },
  // family (process tree) nodes — same visual language, distinct border
  {
    selector: 'node[?family]',
    style: {
      shape: 'round-rectangle', width: 118, height: 30, 'border-color': '#6f96c2',
      'border-width': 1.2, 'background-color': '#19304a', color: '#c4daf0',
      'border-style': 'dashed',
    },
  },
  // far zoom: wireframe mode — translucent fills, bright borders carry the shape
  {
    selector: 'node.compact',
    style: {
      'background-opacity': 0.3,
      'border-width': 1.6,
      'border-color': '#7fa8d4',
    },
  },
  { selector: 'node[?born]', style: { 'border-color': '#35e0ff', 'border-width': 1.6 } },
  { selector: 'node[?highCpu]', style: { 'border-color': '#f0a63c' } },
  { selector: 'node[?inspected]', style: { 'border-color': '#35e0ff', 'border-width': 1.8, 'background-color': '#0f1c2c' } },
  { selector: 'node[?searchMatch]', style: { 'overlay-opacity': 0.2 } },
  { selector: 'node[?dimmed]', style: { opacity: 0.18 } },
  { selector: 'node[?hidden]', style: { display: 'none' } },
  { selector: 'edge[?hidden]', style: { display: 'none' } },
  { selector: 'node.fam-hidden, edge.fam-hidden', style: { display: 'none' } },
  // focus mode: everything outside the neighborhood fades away
  { selector: 'node.focus-dim, edge.focus-dim', style: { opacity: 0.08 } },
  // multi-select (inspection only)
  {
    selector: 'node:selected',
    style: { 'border-color': '#35e0ff', 'border-width': 2, 'overlay-opacity': 0.18 },
  },
  {
    selector: 'edge:selected',
    style: { 'line-color': '#35e0ff', width: 1.8 },
  },
  {
    selector: 'edge',
    style: {
      'line-color': '#3a5a78',
      width: 1,
      'curve-style': 'unbundled-bezier',
      'control-point-distances': (e) => edgeCurveDist(e as EdgeSingular),
      'control-point-weights': 0.5,
      opacity: 0.55,
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#3a5a78',
      'arrow-scale': 0.35,
      // invisible overlay widens the hover hit-area (tooltip friendliness)
      'overlay-color': '#35e0ff',
      'overlay-opacity': 0,
      'overlay-padding': 10,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': '300ms' as unknown as number,
    },
  },
  { selector: 'edge[kind = "LOCALHOST"]', style: { 'line-color': '#4e93ad', 'target-arrow-shape': 'none' } },
  { selector: 'edge[kind = "LISTEN"]', style: { 'line-color': '#5f9660', 'target-arrow-shape': 'none', 'line-style': 'dashed', width: 0.9 } },
  // ---- semantic edges (v0.3.0) -------------------------------------------
  { selector: 'edge[kind = "USES_GPU"]', style: { 'line-color': '#34d399', 'target-arrow-color': '#34d399', 'line-style': 'dashed', width: 1.3 } },
  { selector: 'edge[kind = "SERVES_MODEL"]', style: { 'line-color': '#8b5cf6', 'target-arrow-color': '#8b5cf6', width: 1.3 } },
  { selector: 'edge[kind = "LOCAL_API"]', style: { 'line-color': '#38bdf8', 'target-arrow-color': '#38bdf8', 'line-style': 'dashed' } },
  { selector: 'edge[kind = "HOSTS"]', style: { 'line-color': '#38bdf8', 'target-arrow-color': '#38bdf8', 'line-style': 'dashed', width: 0.8 } },
  { selector: 'edge[kind = "PROCESS_PARENT"]', style: { 'line-color': '#5a6b85', 'target-arrow-color': '#5a6b85', 'line-style': 'dotted' } },
  { selector: 'edge[kind = "SPAWNED"]', style: { 'line-color': '#8b7cf0', 'target-arrow-color': '#8b7cf0', 'line-style': 'dotted' } },
  { selector: 'edge[kind = "MEMBER_OF"]', style: { 'line-color': '#4a6b95', 'target-arrow-color': '#4a6b95', 'line-style': 'dotted', width: 0.7 } },
  // ---- infrastructure edges (v0.4.0) --------------------------------------
  { selector: 'edge[kind = "HOSTED_BY"]', style: { 'line-color': '#d9a03c', 'target-arrow-color': '#d9a03c', 'line-style': 'dashed', width: 1 } },
  { selector: 'edge[kind = "EXPOSES"]', style: { 'line-color': '#2dd4bf', 'target-arrow-color': '#2dd4bf', 'line-style': 'dashed', width: 1 } },
  { selector: 'edge[kind = "CONNECTED_TO"]', style: { 'line-color': '#55647a', 'target-arrow-color': '#55647a', 'line-style': 'dotted', width: 0.8 } },
  { selector: 'edge[kind = "BACKED_BY"]', style: { 'line-color': '#d946ef', 'target-arrow-color': '#d946ef', 'line-style': 'dashed', width: 1 } },
  { selector: 'edge[?active]', style: { 'line-color': '#4a7fa0', 'target-arrow-color': '#4a7fa0' } },
  { selector: 'edge[?recent]', style: { 'line-color': '#5599b4', 'target-arrow-color': '#5599b4' } },
  // real observed traffic subtly brightens + thickens the edge; decays back
  { selector: 'edge[?actLow]', style: { 'line-color': '#3d9cb8', 'target-arrow-color': '#3d9cb8', width: 1.3 } },
  { selector: 'edge[?actMed]', style: { 'line-color': '#4dbcd8', 'target-arrow-color': '#4dbcd8', width: 1.7 } },
  { selector: 'edge[?actHigh]', style: { 'line-color': '#35e0ff', 'target-arrow-color': '#35e0ff', width: 2.2 } },
  { selector: 'edge.pulse', style: { 'line-color': '#35e0ff', 'target-arrow-color': '#35e0ff', width: 1.8 } },
  { selector: 'edge.pulse-close', style: { 'line-color': '#ff5d5d', 'target-arrow-color': '#ff5d5d', width: 1.8 } },
  { selector: 'edge.fading', style: { opacity: 0 } },
  { selector: 'node.fading', style: { opacity: 0 } },
  { selector: 'node.no-labels', style: { label: '' } }, // hide labels at low zoom
  // ---- large-graph LOD + benchmark markers (v0.3.1) ----------------------
  // far zoom: arrowheads are invisible — drop the geometry so dense graphs
  // render cheaply; edges thin out and subdue
  {
    selector: 'edge.lod-far',
    style: { 'target-arrow-shape': 'none', 'arrow-scale': 0, opacity: 0.35, width: 0.8 },
  },
  // synthetic TEST/BENCHMARK fixture nodes: same visual language, dashed
  // border marks them as non-real (never present in normal mode)
  {
    selector: 'node[?benchmark]',
    style: { 'border-style': 'dashed', 'border-color': '#7d8fa3' },
  },
  // ---- transient AI runtime nodes (v0.5.0) -------------------------------
  // Application-level AI telemetry (agent runs / model requests / tool /
  // MCP calls) — visually distinct from process and semantic nodes, and
  // TEST/FIXTURE nodes get a dashed border + test color.
  {
    selector: 'node[kind = "AI_RUNTIME"]',
    style: {
      shape: 'hexagon', width: 92, height: 26, 'background-color': '#1c1430',
      'border-color': '#8b5cf6', 'border-width': 1.3, color: '#e9d5ff',
      'font-size': 10.5,
    },
  },
  {
    selector: 'node[kind = "AI_RUNTIME"][ai_role = "MODEL_REQUEST"]',
    style: { 'border-color': '#38bdf8', color: '#bae6fd' },
  },
  {
    selector: 'node[kind = "AI_RUNTIME"][ai_role = "TOOL_CALL"]',
    style: { 'border-color': '#fbbf24', color: '#fde68a' },
  },
  {
    selector: 'node[kind = "AI_RUNTIME"][ai_role = "MCP_CALL"]',
    style: { 'border-color': '#f472b6', color: '#fbcfe8' },
  },
  {
    selector: 'node[kind = "AI_RUNTIME"][ai_role = "AI_ERROR"]',
    style: { 'border-color': '#f87171', color: '#fecaca' },
  },
  {
    selector: 'node[kind = "AI_RUNTIME"][?ai_test_only]',
    style: { 'border-style': 'dashed', 'border-color': '#9f1239', color: '#fda4af' },
  },
  // application-level AI relationship (proven telemetry, not network bytes)
  {
    selector: 'edge[kind = "AI_CALL"]',
    style: {
      'line-color': '#a855f7', 'target-arrow-color': '#a855f7',
      'target-arrow-shape': 'triangle', 'arrow-scale': 0.8,
      'line-style': 'dashed', width: 1.4, opacity: 0.75,
    },
  },
]

function portLabel(ports: number[]): string {
  const uniq = [...new Set(ports)].sort((a, b) => a - b)
  const head = uniq.slice(0, 4).join(', ')
  return uniq.length > 4 ? `${head} +${uniq.length - 4}` : head
}

export function gpuLabel(g: GpuInfo): string {
  const parts = [`GPU ${g.index}`, String(g.name ?? 'GPU')]
  const util = g.utilization_percent
  const used = g.vram_used_mb
  const total = g.vram_total_mb
  if (typeof util === 'number') {
    if (typeof used === 'number' && typeof total === 'number') {
      parts.push(`${Math.round(util)}% · ${(used / 1024).toFixed(1)}/${(total / 1024).toFixed(1)} GB`)
    } else {
      parts.push(`${Math.round(util)}%`)
    }
  } else if (typeof used === 'number' && typeof total === 'number') {
    parts.push(`${(used / 1024).toFixed(1)}/${(total / 1024).toFixed(1)} GB`)
  }
  return parts.join('\n')
}

interface EdgeActivityState {
  fwdBps: number
  revBps: number
  lastActivity: number // performance.now()
  level: number
}

const ZOOM_FAR = 0.38
const ZOOM_CLOSE = 0.8
/** Desired initial-overview zoom for readable labels (v0.6.0 look). Applied
 * to the initial camera ONLY while it still keeps at least
 * OVERVIEW_MIN_VIEWPORT_FRACTION of the visible graph inside the viewport —
 * never a destructive hard floor that can empty the viewport on large graphs
 * (v1.0.1 hotfix). */
const OVERVIEW_DESIRED_ZOOM = 0.55
/** Minimum fraction of visible nodes that must remain inside the viewport
 * after an initial-overview zoom-in. When zooming to OVERVIEW_DESIRED_ZOOM
 * would drop below this, the camera stops at the largest safe zoom instead of
 * forcing the overview — so a large real graph is never lost (v1.0.1). */
const OVERVIEW_MIN_VIEWPORT_FRACTION = 0.5

// incremental-layout policy (v0.3.1): on large graphs, small node additions
// must NOT trigger an expensive fcose pass over the whole graph. Above
// LARGE_GRAPH_NODES, an incremental layout only runs when the pending batch
// reaches LARGE_GRAPH_MIN_BATCH (or 5% of the graph, whichever is larger).
const LARGE_GRAPH_NODES = 600
const LARGE_GRAPH_MIN_BATCH = 40

// ---- transient AI runtime node budgets (v0.5.0) ---------------------------
// High-frequency AI events must never permanently explode graph node count:
// hard node cap + per-role TTL, pruned by the shared 1 s decay timer.
const MAX_AI_RUNTIME_NODES = 24
const AI_RUN_TTL_MS = 90_000
const AI_RUN_FINISHED_TTL_MS = 12_000
const AI_SPAN_TTL_MS = 45_000

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
  private edgeActivity = new Map<string, EdgeActivityState>()
  private activityMuted = false
  private activityDecayTimer: number | undefined
  // ---- transient AI runtime nodes (v0.5.0) -----------------------------
  // Bounded, time-decayed application-level AI objects. node_id -> expiry
  // (performance.now() ms); pruned by the SAME shared 1 s timer as network
  // activity decay, so high-frequency AI events can never permanently grow
  // the graph (cap + TTL below).
  private aiRuntimeNodes = new Map<string, { expires: number; data: Record<string, unknown> }>()
  private aiRuntimeEdges = new Set<string>()
  private telemetrySource = 'SOCKET EVENTS'
  private familyView: ViewMode = 'nodes'
  private focusNode: string | null = null
  private focusHops = 1
  private zoomBucket: 'far' | 'mid' | 'close' = 'mid'
  private labelDirty = true
  private labelsVisible = true
  private compactMode = false
  private view: 'system' | 'ai' | 'infra' = 'system'
  private benchmarkMode = false
  private lodFar = false
  /** last label actually written per node (avoids redundant cytoscape
   * dirtying on every metrics tick — v0.3.1 large-graph optimization) */
  private labelCache = new Map<string, string>()
  // ---- v1.0.1 camera/viewport ------------------------------------------
  /** true once the blank-graph safety recovery has run (view-only, once —
   * must never fight the user's own pan/zoom after initial placement). */
  private safetyRecovered = false
  /** layout lifecycle state (surfaced by viewportHealth()) */
  private layoutState: 'idle' | 'active' = 'idle'
  /** keeps the cytoscape renderer glued to the shell's final dimensions */
  private resizeObs: ResizeObserver | undefined

  constructor(
    private cy: Core,
    container: HTMLElement,
  ) {
    this.overlay = new EdgePulseOverlay(cy, container)
    // v1.0.1: keep the renderer glued to the shell's final dimensions so a
    // layout is never computed against a 0 / stale container (container
    // size does NOT only change on window resize — drawer/panel toggles move
    // the shell around it). cytoscape's own autoResize is window-orientated;
    // a ResizeObserver catches every container geometry change.
    if (typeof ResizeObserver !== 'undefined') {
      this.resizeObs = new ResizeObserver(() => cy.resize())
      this.resizeObs.observe(container)
    }
    cy.on('add', 'node', () => {
      this.pendingNewNodes += 1
      this.scheduleIncrementalLayout()
    })
    cy.on('mouseover', 'edge', (ev) => this.showTooltip(ev.target as EdgeSingular))
    cy.on('mouseout', 'edge', () => this.hideTooltip())
    cy.on('zoom', () => {
      this.updateLabelVisibility()
      this.refreshLabels()
      this.updateCompactMode()
      this.updateEdgeLod()
    })
    perf.setGraphSource({
      nodes: () => this.cy.nodes().length,
      edges: () => this.cy.edges().length,
      visibleNodes: () => this.cy.nodes(':visible').length,
      visibleEdges: () => this.cy.edges(':visible').length,
      particles: () => this.overlay.stats().particles,
      overlayRunning: () => this.overlay.stats().running,
      overlayActivity: () => this.overlay.stats().activity,
    })
  }

  /** TEST-ONLY benchmark mode flag (set from the WS snapshot mode). */
  setBenchmarkMode(on: boolean): void {
    this.benchmarkMode = on
    perf.setBenchmarkMode(on)
  }

  // ------------------------------------------------------------ view modes

  setViewMode(mode: ViewMode): void {
    if (mode === this.familyView) return
    this.familyView = mode
    if (mode === 'families') this.syncFamilyView()
    else this.teardownFamilyView()
  }

  setTelemetry(t: TelemetryInfo | undefined): void {
    this.telemetrySource =
      t?.source && t.source !== 'NONE' ? t.source : 'SOCKET EVENTS (TIER 0)'
  }

  // -------------------------------------------------------- SYSTEM / AI / INFRA views

  /**
   * SYSTEM <-> AI <-> INFRA view toggle. Every view is driven by backend
   * CLASSIFICATION (node kind + `semantic`/`infra`/`gpu_attributed` data),
   * never by frontend string search. The toggle only adds/removes a class:
   * the Cytoscape instance, layout, positions, selection and focus are all
   * preserved — nothing is rebuilt, no layout is forced.
   */
  setView(view: 'system' | 'ai' | 'infra'): void {
    if (view === this.view) return
    this.view = view
    const t0 = performance.now()
    this.applyView()
    perf.recordAiToggle(performance.now() - t0)
  }

  private isSemanticNode(el: NodeSingular): boolean {
    const kind = el.data('kind')
    if (kind === 'SEMANTIC' || kind === 'LOCAL_LLM' || kind === 'GPU') return true
    // transient AI runtime nodes belong to the AI view (v0.5.0)
    if (kind === 'AI_RUNTIME') return true
    return !!(el.data('semantic') || el.data('gpu_attributed'))
  }

  private isInfraNode(el: NodeSingular): boolean {
    const kind = el.data('kind')
    return kind === 'SERVICE' || kind === 'WSL' || kind === 'DOCKER_ENGINE' ||
      kind === 'CONTAINER' || kind === 'DOCKER_NETWORK' || kind === 'VM'
  }

  private applyView(): void {
    this.cy.batch(() => {
      this.cy.elements().removeClass('ai-dim')
      if (this.view === 'system') return
      if (this.view === 'ai') {
        this.applyAiView()
        return
      }
      this.applyInfraView()
    })
  }

  /** AI view: semantic resources + classified processes stay; noise dims. */
  private applyAiView(): void {
    const keep = new Set<string>()
    this.cy.nodes().forEach((n) => {
      if (this.isSemanticNode(n)) keep.add(n.id())
    })
    // GPU-attributed processes (USES_GPU endpoints) also stay
    this.cy.edges('[kind = "USES_GPU"]').forEach((e) => {
      keep.add(e.source().id())
      keep.add(e.target().id())
    })
    this.cy.nodes().forEach((n) => {
      if (!keep.has(n.id())) n.addClass('ai-dim')
    })
    this.cy.edges().forEach((e) => {
      const s = e.source().id()
      const t = e.target().id()
      // keep edges inside the semantic subset (including raw socket edges)
      if (keep.has(s) && keep.has(t)) return
      if (this.isSemanticNode(e.source()) || this.isSemanticNode(e.target())) return
      e.addClass('ai-dim')
    })
  }

  /** INFRA view: services, WSL, Docker, VMs + their host processes stay;
   * unrelated Windows noise dims. Classification-driven (data.infra roles,
   * EXPOSES/USES_GPU edge evidence). */
  private applyInfraView(): void {
    const keep = new Set<string>()
    this.cy.nodes().forEach((n) => {
      if (this.isInfraNode(n)) keep.add(n.id())
    })
    // processes with a proven infra role (service hosts, VM backends)
    this.cy.nodes('[?infra]').forEach((n) => {
      keep.add(n.id())
    })
    // GPU nodes whose USES_GPU edge is proven by an infra host process
    this.cy.edges('[kind = "USES_GPU"]').forEach((e) => {
      const s = e.source()
      const t = e.target()
      if (this.isInfraNode(s) || this.isInfraNode(t)) {
        keep.add(s.id())
        keep.add(t.id())
      }
    })
    // listening ports exposed by containers (EXPOSES evidence)
    this.cy.edges('[kind = "EXPOSES"]').forEach((e) => {
      keep.add(e.source().id())
      keep.add(e.target().id())
    })
    // the Windows host node anchors the infrastructure
    keep.add('sys:windows')
    this.cy.nodes().forEach((n) => {
      if (!keep.has(n.id())) n.addClass('ai-dim')
    })
    this.cy.edges().forEach((e) => {
      const s = e.source().id()
      const t = e.target().id()
      if (keep.has(s) && keep.has(t)) return
      if (this.isInfraNode(e.source()) || this.isInfraNode(e.target())) return
      e.addClass('ai-dim')
    })
  }

  /** Live GPU metrics from the `gpu` WS message (node may not exist yet). */
  applyGpu(gpus: GpuInfo[]): void {
    this.cy.batch(() => {
      for (const g of gpus) {
        const id = `gpu:${g.index}`
        let el = this.cy.getElementById(id)
        if (!el.length) {
          this.cy.add({
            group: 'nodes',
            data: {
              id, kind: 'GPU', label: '', semantic_type: 'GPU',
              gpu_index: g.index,
            },
            position: { x: 0, y: 0 },
          })
          el = this.cy.getElementById(id)
          this.pendingNewNodes += 1
          this.scheduleIncrementalLayout()
        }
        const data = el.data()
        data.name = g.name ?? data.name
        data.utilization_percent = g.utilization_percent ?? data.utilization_percent
        data.vram_used_mb = g.vram_used_mb ?? data.vram_used_mb
        data.vram_total_mb = g.vram_total_mb ?? data.vram_total_mb
        data.temperature_c = g.temperature_c ?? data.temperature_c
        data.power_w = g.power_w ?? data.power_w
        data.driver = g.driver ?? data.driver
        data.fan_percent = g.fan_percent ?? data.fan_percent
        data.processes = g.processes ?? data.processes
        data.label = gpuLabel(g)
        el.data(data)
      }
    })
  }

  // ------------------------------------------------------------- activity

  applyActivity(items: NetworkActivityItem[], nodes: NetworkActivityNode[], synthetic = false): void {
    if (this.activityMuted) return
    const t0 = performance.now()
    const now = performance.now()
    const touched = new Set<string>()
    for (const it of items) {
      touched.add(it.edge_id)
      this.edgeActivity.set(it.edge_id, {
        fwdBps: it.fwd_bps,
        revBps: it.rev_bps,
        lastActivity: now,
        level: it.level,
      })
    }
    // prune edges that stopped reporting and are no longer recent — MUST
    // also strip the cytoscape act* styling, or stale edges stay lit
    // forever with no entry left to decay (v0.2.1 decay bug)
    const pruned: string[] = []
    for (const [eid, st] of this.edgeActivity) {
      if (!touched.has(eid) && now - st.lastActivity > 5500) {
        this.edgeActivity.delete(eid)
        pruned.push(eid)
      }
    }
    if (pruned.length > 0) {
      this.cy.batch(() => {
        for (const eid of pruned) {
          const el = this.cy.getElementById(eid)
          if (el.length) el.removeData('actLow actMed actHigh')
        }
      })
    }
    this.ensureActivityDecayTimer()
    this.cy.batch(() => {
      for (const it of items) {
        const el = this.cy.getElementById(it.edge_id)
        if (el.length === 0) continue
        el.removeData('actLow actMed actHigh')
        if (it.level === 1) el.data('actLow', true)
        else if (it.level === 2) el.data('actMed', true)
        else if (it.level >= 3) el.data('actHigh', true)
        // keep the process cards' NETWORK section fresh (throttled by batch rate)
        const src = el.source()
        const tgt = el.target()
        if (src.data('kind') === 'PROCESS') {
          src.data('net_out_bps', it.fwd_bps)
          src.data('net_in_bps', it.rev_bps)
          src.data('last_activity', it.last_activity)
        }
        if (tgt.data('kind') === 'PROCESS') {
          tgt.data('net_out_bps', it.rev_bps)
          tgt.data('net_in_bps', it.fwd_bps)
          tgt.data('last_activity', it.last_activity)
        }
      }
      for (const n of nodes) {
        const el = this.cy.getElementById(n.sid)
        if (el.length === 0) continue
        el.data('net_out_bps', n.up_bps)
        el.data('net_in_bps', n.down_bps)
        el.data('last_activity', n.last_activity)
      }
    })
    this.overlay.applyActivity(items, synthetic)
    perf.recordActivityBatch(performance.now() - t0)
  }

  // ------------------------------------------------- application AI telemetry

  /**
   * Feed one batch of normalized application-level AI events (the
   * ``ai_activity`` WS message). Creates bounded transient runtime nodes
   * (AGENT RUN / MODEL REQUEST / TOOL CALL / MCP CALL) linked ONLY to
   * proven parents — ``runtime.parent_node_id`` (backend trace evidence),
   * ``sem:hermes`` (agent identity evidence), or a LOCAL_LLM node with an
   * exact ``model_id`` match. TEST/FIXTURE events are marked
   * ``ai_test_only`` and drive the distinct AI signal lane (fuchsia
   * diamonds) — never the DATA particle lane.
   */
  applyAiActivity(events: AiTelemetryEvent[]): void {
    if (events.length === 0 || this.activityMuted) return
    const now = performance.now()
    const defs: ElementDefinition[] = []
    const signals: { edge_id: string; test_only?: boolean }[] = []

    // exact model_id -> LOCAL_LLM node lookup (few nodes; linear is fine)
    const llmNodes: { id: string; models: { id: string }[] }[] = []
    this.cy.nodes('[kind = "LOCAL_LLM"]').forEach((n) => {
      llmNodes.push({ id: n.id(), models: (n.data('models') as { id: string }[]) ?? [] })
    })

    this.cy.batch(() => {
      for (const ev of events) {
        const rt = ev.runtime
        if (!rt?.node_id) continue
        const testOnly = Boolean(rt.test_only || ev.test_only)
        const role = rt.kind === 'AI_ERROR' ? 'AI_ERROR' : rt.kind
        const isRun = rt.kind === 'AGENT_RUN'
        const ttl = isRun
          ? (rt.finished ? AI_RUN_FINISHED_TTL_MS : AI_RUN_TTL_MS)
          : AI_SPAN_TTL_MS
        const label = `${testOnly ? '[TEST] ' : ''}${rt.label ?? rt.kind.replace(/_/g, ' ')}`
        const data: Record<string, unknown> = {
          id: rt.node_id,
          kind: 'AI_RUNTIME',
          ai_role: role,
          label,
          ai_test_only: testOnly || undefined,
          ai_status: ev.status,
          ai_model: ev.model_id,
          ai_tool: ev.tool_name,
          ai_trace: ev.trace_id,
          ai_agent: ev.agent_name ?? ev.agent_id,
          ai_tokens: ev.total_tokens,
          ai_tps: ev.tps,
          ai_latency: ev.duration_ms,
        }
        this.aiRuntimeNodes.set(rt.node_id, { expires: now + ttl, data })
        while (this.aiRuntimeNodes.size > MAX_AI_RUNTIME_NODES) {
          const oldest = this.aiRuntimeNodes.keys().next().value
          if (oldest === undefined) break
          this.aiRuntimeNodes.delete(oldest)
        }
        const existing = this.cy.getElementById(rt.node_id)
        if (existing.length) {
          existing.data(data)
        } else {
          defs.push({ group: 'nodes', data, position: this.aiNodePosition(rt.parent_node_id) })
        }

        // ---- proven linkage (never invented parentage) ------------------
        const links: [string, string][] = []
        if (rt.parent_node_id && this.cy.getElementById(rt.parent_node_id).length) {
          links.push([rt.parent_node_id, rt.node_id])
        }
        if (isRun && !rt.finished) {
          const agent = `${ev.agent_name ?? ''} ${ev.agent_id ?? ''}`.toLowerCase()
          if (agent.includes('hermes') && this.cy.getElementById('sem:hermes').length) {
            links.push(['sem:hermes', rt.node_id])
          }
        }
        if (ev.model_id && !isRun) {
          const hit = llmNodes.find((n) => n.models.some((m) => m.id === ev.model_id))
          if (hit) links.push([rt.node_id, hit.id])
        }
        for (const [src, tgt] of links) {
          const eid = `ai:${src}->${tgt}`
          if (this.cy.getElementById(eid).length) {
            signals.push({ edge_id: eid, test_only: testOnly })
            continue
          }
          this.aiRuntimeEdges.add(eid)
          defs.push({
            group: 'edges',
            data: {
              id: eid, source: src, target: tgt, kind: 'AI_CALL', proto: 'ai',
              ports: [], active: true, directed: true, ai_test_only: testOnly || undefined,
            },
          })
          signals.push({ edge_id: eid, test_only: testOnly })
        }
      }
    })

    if (defs.length > 0) {
      this.cy.add(defs)
      this.pendingNewNodes += defs.filter((d) => d.group === 'nodes').length
      this.scheduleIncrementalLayout()
    }
    if (signals.length > 0) this.overlay.applyAiSignals(signals)
    if (this.aiRuntimeNodes.size > 0) this.ensureActivityDecayTimer()
    if (this.view !== 'system') this.applyView()
  }

  /** Place a new runtime node near its proven parent, else view center. */
  private aiNodePosition(parentId: string | null | undefined): { x: number; y: number } {
    if (parentId) {
      const p = this.cy.getElementById(parentId)
      if (p.length) {
        const pos = p.position()
        return { x: pos.x + (Math.random() - 0.5) * 140, y: pos.y + (Math.random() - 0.5) * 140 }
      }
    }
    const ext = this.cy.extent()
    return {
      x: (ext.x1 + ext.x2) / 2 + (Math.random() - 0.5) * 120,
      y: (ext.y1 + ext.y2) / 2 + (Math.random() - 0.5) * 120,
    }
  }

  /** Read-only AI telemetry diagnostics for acceptance / debugging. */
  aiStats(): {
    runtimeNodes: number
    runtimeEdges: number
    overlayAiSignals: number
    overlayAiParticles: number
    maxRuntimeNodes: number
  } {
    const s = this.overlay.stats()
    return {
      runtimeNodes: this.aiRuntimeNodes.size,
      runtimeEdges: this.aiRuntimeEdges.size,
      overlayAiSignals: s.aiSignals,
      overlayAiParticles: s.aiParticles,
      maxRuntimeNodes: MAX_AI_RUNTIME_NODES,
    }
  }

  /** TEST ONLY (acceptance AC): clear AI runtime state deterministically. */
  testClearAiRuntime(): void {
    this.cy.batch(() => {
      for (const nid of [...this.aiRuntimeNodes.keys()]) {
        const el = this.cy.getElementById(nid)
        if (el.length) el.remove()
      }
      for (const eid of [...this.aiRuntimeEdges]) {
        const el = this.cy.getElementById(eid)
        if (el.length) el.remove()
      }
    })
    this.aiRuntimeNodes.clear()
    this.aiRuntimeEdges.clear()
  }

  /**
   * TEST ONLY (benchmark mode): inject synthetic activity batches to
   * exercise the particle budget deterministically. Throws outside
   * benchmark mode — real telemetry is never fabricated. Injected
   * particles are flagged ``synthetic`` in the overlay stats.
   */
  testInjectActivity(items: NetworkActivityItem[]): void {
    if (!this.benchmarkMode) {
      throw new Error('testInjectActivity is TEST-ONLY and requires benchmark mode')
    }
    const existing = items.filter((it) => this.cy.getElementById(it.edge_id).length > 0)
    if (existing.length === 0) return
    this.applyActivity(existing, [], true)
  }

  // --------------------------------------------------- activity decay (v0.2.1)

  /**
   * One shared activity-decay scheduler (never a per-edge timer): a single
   * 1 s interval, started when the first activity batch arrives and stopped
   * as soon as the edge-activity map is empty. While it runs it clears
   * stale `actLow/actMed/actHigh` styling (and stale per-process net rates)
   * purely by wall-clock age — so visual state decays even when the backend
   * sends no further network_activity batch. This is UI decay, never a fake
   * zero-rate network observation.
   */
  private ensureActivityDecayTimer(): void {
    if (this.activityDecayTimer !== undefined) return
    this.activityDecayTimer = window.setInterval(() => this.decayEdgeActivity(), 1000)
  }

  private decayEdgeActivity(): void {
    const now = performance.now()
    const stale: string[] = []
    for (const [eid, st] of this.edgeActivity) {
      if (now - st.lastActivity > RECENT_MS) stale.push(eid)
    }
    if (stale.length > 0) {
      this.cy.batch(() => {
        for (const eid of stale) {
          this.edgeActivity.delete(eid)
          const el = this.cy.getElementById(eid)
          if (el.length) el.removeData('actLow actMed actHigh net_out_bps net_in_bps last_activity')
        }
      })
    }
    // node-halo rates decay independently of edge staleness
    this.clearStaleNodeRates()
    // transient AI runtime nodes decay by wall-clock age (bounded graph)
    this.decayAiRuntimeNodes()
    const nodesWithRates = this.cy.nodes('[?last_activity]').length
    if (this.edgeActivity.size === 0 && nodesWithRates === 0 &&
        this.aiRuntimeNodes.size === 0 && this.activityDecayTimer !== undefined) {
      clearInterval(this.activityDecayTimer)
      this.activityDecayTimer = undefined
    }
  }

  /** Time-decay transient AI runtime nodes (TTL per role, hard cap). */
  private decayAiRuntimeNodes(): void {
    const now = performance.now()
    const expired: string[] = []
    for (const [nid, st] of this.aiRuntimeNodes) {
      if (now > st.expires) expired.push(nid)
    }
    if (expired.length === 0) return
    this.cy.batch(() => {
      for (const nid of expired) {
        this.aiRuntimeNodes.delete(nid)
        const el = this.cy.getElementById(nid)
        if (el.length) el.remove()
        for (const eid of [...this.aiRuntimeEdges]) {
          const e = this.cy.getElementById(eid)
          if (e.length && (e.source().id() === nid || e.target().id() === nid)) {
            this.aiRuntimeEdges.delete(eid)
            e.remove()
          }
        }
      }
    })
  }

  /** Process nodes keep their ↓/↑ rates only while telemetry is fresh. */
  private clearStaleNodeRates(): void {
    const epochNow = Date.now() / 1000
    const els = this.cy.nodes('[?last_activity]')
    if (els.length === 0) return
    const stale: NodeSingular[] = []
    els.forEach((n) => {
      const la = n.data('last_activity') as number | undefined
      if (typeof la === 'number' && epochNow - la > RECENT_MS / 1000) stale.push(n as NodeSingular)
    })
    if (stale.length === 0) return
    this.cy.batch(() => {
      for (const n of stale) n.removeData('net_out_bps net_in_bps last_activity')
    })
  }

  /** Read-only diagnostics for acceptance tests / debugging. */
  overlayStats() {
    return this.overlay.stats()
  }

  /** TEST ONLY (acceptance Test T3): force the overlay's terminal idle
   * state to verify the rAF stop mechanism deterministically. */
  testForceIdle(): void {
    this.overlay.testForceIdle()
  }

  /** TEST ONLY (acceptance Test T): mute activity ingestion AND the
   * lifecycle pulse feed while verifying the full time-based decay path
   * deterministically (see EdgePulseOverlay.testMute). On a live machine
   * with real ETW, ambient loopback traffic legitimately keeps refreshing
   * the activity maps forever, so the equivalent of a machine with no
   * further network_activity batches is asserted instead. */
  testMute(muted: boolean): void {
    this.activityMuted = muted
    this.overlay.testMute(muted)
  }

  debugStats(): { edgeActivity: number; telemetrySource: string } {
    return { edgeActivity: this.edgeActivity.size, telemetrySource: this.telemetrySource }
  }

  // ---------------------------------------------------------------- snapshot

  replaceAll(nodes: TopoNode[], edges: TopoEdge[]): void {
    const t0 = performance.now()
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
      // transient AI runtime nodes are NOT part of the backend snapshot —
      // re-add the survivors so a snapshot refresh never wipes live AI state
      for (const [nid, st] of this.aiRuntimeNodes) {
        if (!this.cy.getElementById(nid).length) {
          this.cy.add({
            group: 'nodes', data: { ...st.data, id: nid },
            position: this.aiNodePosition(null),
          })
        }
      }
      for (const eid of this.aiRuntimeEdges) {
        if (this.cy.getElementById(eid).length) continue
        const [src, tgt] = eid.slice(3).split('->')
        if (!src || !tgt) continue
        if (this.cy.getElementById(src).length && this.cy.getElementById(tgt).length) {
          this.cy.add({
            group: 'edges',
            data: {
              id: eid, source: src, target: tgt, kind: 'AI_CALL', proto: 'ai',
              ports: [], active: true, directed: true,
            },
          })
        }
      }
    })
    this.pendingNewNodes = 0
    this.labelCache.clear()
    this.refreshLabels()
    this.updateEdgeLod()
    this.runLayout('initial')
    if (this.familyView === 'families') this.syncFamilyView()
    if (this.focusNode) this.applyFocus()
    if (this.view !== 'system') this.applyView()
    perf.recordUpdate(performance.now() - t0)
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
        if (node) this.upsertNode(node, true, undefined)
        break
      }
      case 'PROCESS_STOPPED':
        this.fadeRemoveNode(ev.source)
        break
      case 'CONNECTION_OPENED': {
        const m = ev.metadata
        const srcNode = m?.src_node as TopoNode | undefined
        const tgtNode = m?.tgt_node as TopoNode | undefined
        if (srcNode) this.upsertNode(srcNode, false, tgtNode?.id)
        if (tgtNode) this.upsertNode(tgtNode, false, srcNode?.id)
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
          })
          this.labelDirty = true
          this.refreshLabels()
        }
        break
      }
      // ---- semantic events (v0.3.0) ------------------------------------
      case 'HERMES_DETECTED':
      case 'LM_STUDIO_DETECTED':
      case 'MCP_SERVER_DETECTED':
      case 'SEMANTIC_DETECTED':
      case 'MODEL_LOADED':
      case 'MODEL_AVAILABLE': {
        const m = ev.metadata
        const node = m?.node as TopoNode | undefined
        if (node) this.upsertNode(node, true, undefined)
        const edges = m?.edges as TopoEdge[] | undefined
        if (Array.isArray(edges)) {
          for (const e of edges) {
            if (e?.id && e?.source && e?.target) this.upsertEdge(e)
          }
        }
        break
      }
      case 'SEMANTIC_LOST':
        this.fadeRemoveNode(String(ev.metadata?.node_id ?? ev.source))
        break
      case 'GPU_PROCESS_ATTACHED': {
        const m = ev.metadata
        if (m?.edge) this.upsertEdge(m.edge as TopoEdge)
        if (m?.sid) {
          const el = this.cy.getElementById(String(m.sid))
          if (el.length) el.data('gpu_attributed', true)
        }
        break
      }
      case 'GPU_PROCESS_DETACHED': {
        const m = ev.metadata
        if (m?.edge_id) this.fadeRemoveEdge(String(m.edge_id))
        break
      }
      // ---- infrastructure events (v0.4.0, change-only) ------------------
      // All carry metadata.node + metadata.edges (same shape as semantic
      // events). SERVICE_*/CONTAINER_*/WSL_*/VM_* update the node in place;
      // CONTAINER_REMOVED / VM_LOST remove it. A stopped service KEEPS its
      // node (the service still exists — its status is just STOPPED).
      case 'SERVICE_STARTED':
      case 'SERVICE_STOPPED':
      case 'SERVICE_STATUS_CHANGED':
      case 'CONTAINER_STARTED':
      case 'CONTAINER_STOPPED':
      case 'CONTAINER_CREATED':
      case 'WSL_STATE_CHANGED':
      case 'VM_DETECTED':
      case 'VM_STATE_CHANGED': {
        const m = ev.metadata
        const node = m?.node as TopoNode | undefined
        if (node) this.upsertNode(node, false, undefined)
        const edges = m?.edges as TopoEdge[] | undefined
        if (Array.isArray(edges)) {
          for (const e of edges) {
            if (e?.id && e?.source && e?.target) this.upsertEdge(e)
          }
        }
        break
      }
      case 'CONTAINER_REMOVED':
      case 'VM_LOST':
        this.fadeRemoveNode(String(ev.metadata?.node_id ?? ev.source))
        break
    }
    if (this.familyView === 'families') this.syncFamilyView()
    if (this.focusNode) this.applyFocus()
    if (this.view !== 'system') this.applyView()
  }

  private upsertNode(node: TopoNode, born: boolean, anchorId: string | undefined): void {
    const existing = this.cy.getElementById(node.id)
    if (existing.length) {
      this.pendingNodeRemoves.delete(node.id)
      existing.removeClass('fading')
      this.cy.batch(() => {
        existing.data(this.flattenNode(node))
      })
      this.labelDirty = true
      this.refreshLabels()
      return
    }
    const data = this.flattenNode(node)
    if (born) data.born = true
    // place new nodes near a real neighbor when known (topology coherence),
    // otherwise near the current view center so they join the visible graph
    let position: { x: number; y: number }
    const anchor = anchorId ? this.cy.getElementById(anchorId) : undefined
    if (anchor && anchor.length) {
      const ap = anchor.position()
      position = {
        x: ap.x + (Math.random() - 0.5) * 180,
        y: ap.y + (Math.random() - 0.5) * 180,
      }
    } else {
      const ext = this.cy.extent()
      position = {
        x: (ext.x1 + ext.x2) / 2 + (Math.random() - 0.5) * 160,
        y: (ext.y1 + ext.y2) / 2 + (Math.random() - 0.5) * 160,
      }
    }
    this.cy.add({ group: 'nodes', data, position })
    if (born) {
      window.setTimeout(() => {
        const el = this.cy.getElementById(node.id)
        if (el.length) el.removeData('born')
      }, 800)
    }
    if (this.familyView === 'families') {
      // a new member of an existing family must not render separately
      const members = this.familyMembersOf(node.id)
      if (members) {
        const el = this.cy.getElementById(node.id)
        if (el.length) el.addClass('fam-hidden')
        this.syncFamilyView()
      }
    }
  }

  private upsertEdge(edge: TopoEdge): void {
    const existing = this.cy.getElementById(edge.id)
    if (existing.length) {
      this.pendingEdgeRemoves.delete(edge.id)
      existing.removeClass('fading pulse pulse-close')
      this.cy.batch(() => {
        // MERGE ports: CONNECTION_OPENED events carry one conn's port each,
        // and several conns share one edge — replacing would randomly drop
        // ports depending on event arrival order
        const merged = [...new Set([
          ...((existing.data('ports') as number[] | undefined) ?? []),
          ...edge.ports,
        ])]
        existing.data('ports', merged)
        existing.data('portLabel', portLabel(merged))
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

  // ------------------------------------------------------- semantic zoom

  /**
   * Writes node labels per zoom bucket, but ONLY when the label actually
   * changed (labelCache): on a large graph, metric ticks must not re-dirty
   * every node's label every second — that forces a full style pass.
   */
  private refreshLabels(): void {
    const zoom = this.cy.zoom()
    const bucket: 'far' | 'mid' | 'close' =
      zoom < ZOOM_FAR ? 'far' : zoom < ZOOM_CLOSE ? 'mid' : 'close'
    if (bucket === this.zoomBucket && !this.labelDirty) return
    this.zoomBucket = bucket
    this.labelDirty = false
    // prune cache entries for nodes that no longer exist
    if (this.labelCache.size > this.cy.nodes().length * 2) {
      const live = new Set(this.cy.nodes().map((n) => n.id()))
      for (const id of this.labelCache.keys()) {
        if (!live.has(id)) this.labelCache.delete(id)
      }
    }
    this.cy.batch(() => {
      this.cy.nodes('[kind = "PROCESS"]').forEach((n) => {
        if (n.data('family')) return
        const name = String(n.data('name') ?? n.data('label') ?? '')
        const pid = String(n.data('pid') ?? '')
        let label: string
        if (bucket === 'far') label = ''
        else if (bucket === 'mid') label = `${name}\nPID ${pid}`
        else {
          const cpu = Number(n.data('cpu_percent') ?? 0)
          const mem = Math.round(Number(n.data('memory_mb') ?? 0))
          label = `${name}\nPID ${pid} · ${cpu.toFixed(0)}%${mem > 0 ? ` · ${mem}MB` : ''}`
        }
        const key = `${bucket}:${label}`
        if (this.labelCache.get(n.id()) === key) return
        this.labelCache.set(n.id(), key)
        n.data('label', label)
      })
      // near zoom: slightly larger detail cards (visual only — positions
      // stay untouched by a style-only height change)
      this.cy.nodes('[kind = "PROCESS"]').toggleClass('zoom-close', bucket === 'close')
    })
  }

  /**
   * Edge LOD (v0.3.1): at far zoom, arrowheads and glows are invisible —
   * drop arrow geometry and thin edges so dense graphs render cheaply.
   */
  private updateEdgeLod(): void {
    const far = this.cy.zoom() < ZOOM_FAR
    if (far === this.lodFar) return
    this.lodFar = far
    this.cy.batch(() => {
      this.cy.edges().toggleClass('lod-far', far)
    })
  }

  private updateLabelVisibility(): void {
    const show = this.cy.zoom() >= ZOOM_FAR
    if (show === this.labelsVisible) return
    this.labelsVisible = show
    this.cy.batch(() => {
      this.cy.nodes('[kind != "PROCESS"]').toggleClass('no-labels', !show)
    })
  }

  /** Far zoom = wireframe: translucent fills so dense clusters stay legible. */
  private updateCompactMode(): void {
    const compact = this.cy.zoom() < 0.3
    if (compact === this.compactMode) return
    this.compactMode = compact
    this.cy.batch(() => {
      this.cy.nodes().toggleClass('compact', compact)
    })
  }

  // ---------------------------------------------------- family (group) view

  private familyOf(): Map<string, string> {
    // member sid -> family root sid, based on REAL parent relationships:
    // a process P with >= 2 children C where C.name == P.name forms a family
    // containing P and those children. Evidence: parent_sid from the backend.
    const nodes = this.cy.nodes('[kind = "PROCESS"]')
    const byId = new Map<string, NodeSingular>()
    nodes.forEach((n) => {
      byId.set(n.id(), n)
    })
    const children = new Map<string, string[]>() // parent sid -> child sids
    for (const [sid, n] of byId) {
      const parent = n.data('parent_sid')
      if (typeof parent === 'string' && byId.has(parent)) {
        const name = String(n.data('name') ?? '')
        const pname = String(byId.get(parent)!.data('name') ?? '')
        if (name && name === pname) {
          const list = children.get(parent) ?? []
          list.push(sid)
          children.set(parent, list)
        }
      }
    }
    const memberToRoot = new Map<string, string>()
    for (const [root, kids] of children) {
      if (kids.length >= 2) {
        memberToRoot.set(root, root)
        for (const k of kids) memberToRoot.set(k, root)
      }
    }
    return memberToRoot
  }

  private familyMembersOf(sid: string): string | undefined {
    // root of the family containing sid (direct children only, cheap check)
    const n = this.cy.getElementById(sid)
    if (!n.length) return undefined
    const parent = n.data('parent_sid')
    if (typeof parent !== 'string') return undefined
    const p = this.cy.getElementById(parent)
    if (!p.length) return undefined
    if (String(p.data('name') ?? '') !== String(n.data('name') ?? '')) return undefined
    const kids = this.cy
      .nodes('[kind = "PROCESS"]')
      .filter((c) => c.data('parent_sid') === parent && c.id() !== parent)
    return kids.length >= 2 ? parent : undefined
  }

  private syncFamilyView(): void {
    const memberToRoot = this.familyOf()
    const roots = new Set(memberToRoot.values())
    // one-pass root -> members grouping (avoids O(F*M) re-filtering)
    const membersByRoot = new Map<string, string[]>()
    for (const [sid, root] of memberToRoot) {
      const list = membersByRoot.get(root) ?? []
      list.push(sid)
      membersByRoot.set(root, list)
    }
    this.cy.batch(() => {
      // hide members, show (or create) family nodes
      for (const sid of memberToRoot.keys()) {
        const el = this.cy.getElementById(sid)
        if (el.length) el.addClass('fam-hidden')
      }
      for (const root of roots) {
        const members = membersByRoot.get(root) ?? []
        const famId = `fam:${root}`
        const rootEl = this.cy.getElementById(root)
        const name = String(rootEl.data('name') ?? '')
        const cpu = members.reduce(
          (s, m) => s + Number(this.cy.getElementById(m).data('cpu_percent') ?? 0), 0,
        )
        const mem = members.reduce(
          (s, m) => s + Number(this.cy.getElementById(m).data('memory_mb') ?? 0), 0,
        )
        let fam = this.cy.getElementById(famId)
        if (!fam.length) {
          fam = this.cy.add({
            group: 'nodes',
            data: {
              id: famId,
              kind: 'PROCESS',
              label: `${name} ×${members.length}`,
              family: true,
              member_ids: members,
              name,
              pid: rootEl.data('pid'),
              cpu_percent: cpu,
              memory_mb: mem,
              conn_count: members.reduce(
                (s, m) => s + Number(this.cy.getElementById(m).data('conn_count') ?? 0), 0,
              ),
            },
            position: rootEl.position(),
          })
        } else {
          fam.data('member_ids', members)
          fam.data('cpu_percent', cpu)
          fam.data('memory_mb', mem)
          fam.data('conn_count', members.reduce(
            (s, m) => s + Number(this.cy.getElementById(m).data('conn_count') ?? 0), 0,
          ))
        }
        this.refreshLabelFor(famId)
      }
      // rewire: every edge touching a member routes through the family node
      const memberSet = new Set(memberToRoot.keys())
      this.cy.edges().forEach((e) => {
        const s = e.source().id()
        const t = e.target().id()
        const sIn = memberSet.has(s)
        const tIn = memberSet.has(t)
        if (!sIn && !tIn) return
        const ns = sIn ? `fam:${memberToRoot.get(s)}` : s
        const nt = tIn ? `fam:${memberToRoot.get(t)}` : t
        let a = ns
        let b = nt
        if (e.data('kind') === 'LOCALHOST' && a > b) [a, b] = [b, a]
        const fid = `fam-e:${a}->${b}:${String(e.data('kind') ?? '')}`
        const existing = this.cy.getElementById(fid)
        if (existing.length) {
          const ports = new Set([...(existing.data('ports') as number[] ?? []), ...(e.data('ports') as number[] ?? [])])
          existing.data('ports', [...ports])
          existing.data('portLabel', portLabel([...ports]))
        } else {
          this.cy.add({
            group: 'edges',
            data: {
              id: fid, source: a, target: b, kind: e.data('kind'),
              proto: e.data('proto'), ports: e.data('ports') ?? [],
              active: e.data('active') === true, directed: e.data('directed') === true,
              famEdge: true,
            },
          })
        }
        e.addClass('fam-hidden')
      })
      // clean up stale family nodes/edges whose family disappeared
      this.cy.nodes('[?family]').forEach((n) => {
        if (!roots.has(String(n.id()).replace(/^fam:/, ''))) n.remove()
      })
      this.cy.edges('[?famEdge]').forEach((e) => {
        const src = e.source().id()
        const tgt = e.target().id()
        if (!src.startsWith('fam:') && !tgt.startsWith('fam:')) e.remove()
      })
      // unhide non-member nodes
      this.cy.nodes().forEach((n) => {
        if (n.data('family')) return
        if (!memberSet.has(n.id())) n.removeClass('fam-hidden')
      })
      this.cy.edges().forEach((e) => {
        if (e.data('famEdge')) return
        const s = e.source().id()
        const t = e.target().id()
        if (!memberSet.has(s) && !memberSet.has(t)) e.removeClass('fam-hidden')
      })
    })
    if (this.pendingNewNodes > 0) {
      this.pendingNewNodes = 0
      this.runLayout('incremental')
    }
  }

  private teardownFamilyView(): void {
    this.cy.batch(() => {
      this.cy.nodes('[?family]').remove()
      this.cy.edges('[?famEdge]').remove()
      this.cy.nodes().removeClass('fam-hidden')
      this.cy.edges().removeClass('fam-hidden')
    })
  }

  // -------------------------------------------------------------- focus

  setFocus(nodeId: string | null, hops = 1): void {
    this.focusNode = nodeId
    this.focusHops = hops
    this.applyFocus()
  }

  private applyFocus(): void {
    this.cy.batch(() => {
      this.cy.elements().removeClass('focus-dim')
      if (!this.focusNode) return
      const start = this.cy.getElementById(this.focusNode)
      if (!start.length) return
      const reachable = new Set<string>([this.focusNode])
      let frontier = [this.focusNode]
      for (let h = 0; h < this.focusHops; h++) {
        const next: string[] = []
        for (const id of frontier) {
          const el = this.cy.getElementById(id)
          if (!el.length) continue
          el.connectedEdges().forEach((e) => {
            for (const end of [e.source().id(), e.target().id()]) {
              if (!reachable.has(end)) {
                reachable.add(end)
                next.push(end)
              }
            }
          })
        }
        frontier = next
      }
      this.cy.nodes().forEach((n) => {
        if (!reachable.has(n.id())) n.addClass('focus-dim')
      })
      this.cy.edges().forEach((e) => {
        if (!reachable.has(e.source().id()) || !reachable.has(e.target().id())) {
          e.addClass('focus-dim')
        }
      })
    })
  }

  // ------------------------------------------------------------ layout / view

  private refreshLabelFor(id: string): void {
    const el = this.cy.getElementById(id)
    if (!el.length || !el.data('family')) return
    const name = String(el.data('name') ?? '')
    const count = (el.data('member_ids') as string[] | undefined)?.length ?? 0
    el.data('label', `${name} ×${count}`)
  }

  /**
   * Layout policy (v0.3.1): initial layouts get a size-scaled iteration
   * budget and no animation on very large graphs (animating 1500 nodes is
   * janky for zero information). Incremental runs are debounced and gated
   * by batch size (see scheduleIncrementalLayout) so the graph does not
   * re-layout continuously as processes churn.
   */
  private runLayout(kind: 'initial' | 'incremental'): void {
    const n = this.cy.nodes().length
    const numIter = n > 1500 ? 900 : n > 800 ? 1300 : 2000
    const animate = kind !== 'initial' && n <= 800
    const options = {
      name: 'fcose',
      quality: 'default',
      randomize: kind === 'initial',
      animate,
      animationDuration: 350,
      // v0.6.0 density tuning: tighter springs + stronger gravity pack the
      // real machine map into a dense "nervous system" instead of a sparse
      // canvas with huge empty gaps (organized chaos, not rigid tiling —
      // repulsion/edge length tuned so clusters stay organic and edges
      // remain visible between cards)
      nodeRepulsion: kind === 'initial' ? 14000 : 5000,
      idealEdgeLength: kind === 'initial' ? 125 : 85,
      gravity: kind === 'initial' ? 0.1 : 0.25,
      numIter,
      // tiling arranges components into a rigid grid — off for an organic map
      tile: false,
      padding: 20,
      // incremental runs must not re-fit the view (keeps user's zoom/pan stable)
      fit: kind === 'initial',
    }
    const t0 = performance.now()
    this.layoutState = 'active'
    try {
      const layout = this.cy.layout(options)
      let stopped = false
      const onStop = (): void => {
        if (stopped) return
        stopped = true
        this.layoutState = 'idle'
        perf.recordLayout(performance.now() - t0)
        // v1.0.1: the layoutstop EVENT is the single source of layout
        // completion. The camera is fitted only here, on fcose's FINAL node
        // positions. (The old `if (!animate) onStop()` manual call ran the
        // fit before fcose reached usable positions on some paths and had to
        // be bailed out by fcose's own internal fit — a race that produced
        // the blank-graph camera on the user's machine.)
        if (kind === 'initial') {
          this.fitOverview()
          this.safetyRecover()
        }
      }
      // register BEFORE run() so a synchronous layoutstop still lands here
      layout.one('layoutstop', onStop)
      layout.run()
    } catch {
      this.layoutState = 'idle'
      this.cy.layout({ name: 'cose', animate: false } as never).run()
      perf.recordLayout(performance.now() - t0)
      if (kind === 'initial') {
        this.fitOverview()
        this.safetyRecover()
      }
    }
  }

  private scheduleIncrementalLayout(): void {
    window.clearTimeout(this.layoutTimer)
    this.layoutTimer = window.setTimeout(() => {
      if (this.pendingNewNodes > 0) {
        const total = this.cy.nodes().length
        const large = total > LARGE_GRAPH_NODES
        const batch = this.pendingNewNodes
        this.pendingNewNodes = 0
        if (large && batch < Math.max(LARGE_GRAPH_MIN_BATCH, Math.round(total * 0.05))) {
          // small additions on a large graph keep their anchor/center
          // positions — no full re-layout (preserves stability)
          return
        }
        this.runLayout('incremental')
      }
    }, 2000)
  }

  /** Manual RELAYOUT: re-run the initial layout (fit + randomize). */
  relayout(): void {
    this.runLayout('initial')
  }

  /**
   * Manual FIT ALL (v1.0.1): true fit of the CURRENTLY VISIBLE topology —
   * never a zoom floor on top, so "fit all" can never shove content
   * off-screen. This is what the toolbar FIT button must mean.
   */
  fit(): void {
    const eles = this.cy.elements(':visible')
    if (eles.length) this.cy.fit(eles, 40)
  }

  /**
   * Initial overview (v1.0.1): fit all visible elements, then modestly zoom
   * toward OVERVIEW_DESIRED_ZOOM for readable labels — but ONLY while at
   * least OVERVIEW_MIN_VIEWPORT_FRACTION of the visible nodes stay inside the
   * viewport. There is NO destructive hard floor: on a large real graph the
   * camera keeps the true fit so the map is never lost.
   */
  fitOverview(): void {
    const eles = this.cy.elements(':visible')
    if (!eles.length) return
    this.cy.fit(eles, 40)
    const desired = OVERVIEW_DESIRED_ZOOM
    if (this.cy.zoom() >= desired) return
    const safe = this.safeOverviewZoom(desired)
    if (safe > this.cy.zoom()) {
      this.cy.zoom({
        level: safe,
        renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
      } as unknown as ZoomOptions)
    }
  }

  /** Largest zoom (<= desired) that still keeps >= OVERVIEW_MIN_VIEWPORT_FRACTION
   * of the visible NODES inside the viewport, for a camera centered on the
   * visible graph's bounding box (the same box cy.fit centers on). At the
   * current (true-fit) zoom the whole graph is in viewport, so a safe value
   * always exists in [zoom, desired]. */
  private safeOverviewZoom(desired: number): number {
    const cy = this.cy
    const eles = cy.elements(':visible')
    const nodes = eles.filter((el) => el.isNode())
    if (!nodes.length) return cy.zoom()
    const bb = eles.boundingBox()
    const cx = (bb.x1 + bb.x2) / 2
    const cyy = (bb.y1 + bb.y2) / 2
    const W = cy.width()
    const H = cy.height()
    const fracAt = (z: number): number => {
      const hw = W / 2 / z
      const hh = H / 2 / z
      let inside = 0
      nodes.forEach((n) => {
        const p = n.position()
        if (Math.abs(p.x - cx) <= hw && Math.abs(p.y - cyy) <= hh) inside++
      })
      return inside / nodes.length
    }
    const lo = cy.zoom()
    const hi = desired
    if (fracAt(hi) >= OVERVIEW_MIN_VIEWPORT_FRACTION) return hi
    let a = lo
    let b = hi
    for (let i = 0; i < 24; i++) {
      const m = (a + b) / 2
      if (fracAt(m) >= OVERVIEW_MIN_VIEWPORT_FRACTION) a = m
      else b = m
    }
    return (a + b) / 2
  }

  /**
   * Blank-graph safety recovery (v1.0.1): after the initial layout + fit, if
   * the graph has visible nodes but NONE intersect the viewport, perform ONE
   * view-only correction (resize, then re-fit the visible elements). Runs a
   * single time per controller and never re-arms — it must not fight the
   * user's own pan/zoom after initial placement.
   */
  private safetyRecover(): void {
    if (this.safetyRecovered) return
    this.safetyRecovered = true
    const cy = this.cy
    const vis = cy.nodes(':visible')
    if (!vis.length) return
    const W = cy.width()
    const H = cy.height()
    let inView = 0
    vis.forEach((nn) => {
      const p = nn.renderedPosition()
      if (p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H) inView++
    })
    if (inView > 0) return
    cy.resize()
    const eles = cy.elements(':visible')
    if (eles.length) cy.fit(eles, 40)
  }

  /** Read-only viewport/graph health diagnostic (acceptance + debugging):
   * "nodes exist" is not the same as "nodes are visible to the user". */
  viewportHealth(): Record<string, unknown> {
    const cy = this.cy
    const vis = cy.nodes(':visible')
    const W = cy.width()
    const H = cy.height()
    let viewportNodes = 0
    vis.forEach((nn) => {
      const p = nn.renderedPosition()
      if (p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H) viewportNodes++
    })
    const visBB = vis.length ? vis.boundingBox() : null
    return {
      totalNodes: cy.nodes().length,
      visibleNodes: vis.length,
      viewportNodes,
      totalEdges: cy.edges().length,
      visibleEdges: cy.edges(':visible').length,
      zoom: cy.zoom(),
      pan: { x: cy.pan().x, y: cy.pan().y },
      graphBoundingBox: visBB
        ? { x1: visBB.x1, y1: visBB.y1, x2: visBB.x2, y2: visBB.y2 }
        : null,
      containerWidth: W,
      containerHeight: H,
      layoutState: this.layoutState,
    }
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
    const t0 = performance.now()
    this.applyFilter()
    perf.recordFilter(performance.now() - t0)
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
    // cytoscape quirk (v0.3.1): removing the data field behind a
    // `display:none` selector ([?hidden]) does NOT re-run the style pass —
    // elements stay visually hidden. `cy.style().update()` forces the pass
    // (must run OUTSIDE the batch; a batched pass skips hidden elements).
    this.cy.style().update()
  }

  setSearch(query: string): void {
    this.search = query.trim().toLowerCase()
    const t0 = performance.now()
    this.applySearch()
    perf.recordSearch(performance.now() - t0)
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

  selectedCount(): number {
    return this.cy.elements(':selected').length
  }

  clearSelection(): void {
    this.cy.elements(':selected').unselect()
  }

  // ------------------------------------------------------- shift+box select

  private selBox: HTMLDivElement | null = null
  private selStart: { x: number; y: number } | null = null
  private selPending = false

  /**
   * Shift + drag on the background draws a rubber-band selection box.
   * Implemented with capture-phase listeners that stop propagation once a
   * real drag is detected, so normal left-drag panning is never hijacked.
   */
  initShiftBoxSelection(): void {
    const container = this.cy.container()
    if (!container) return
    const rect = (): DOMRect => container.getBoundingClientRect()
    const onDown = (e: MouseEvent): void => {
      if (e.button !== 0 || !e.shiftKey) return
      this.selPending = true
      const r = rect()
      this.selStart = { x: e.clientX - r.left, y: e.clientY - r.top }
    }
    const onMove = (e: MouseEvent): void => {
      if (!this.selPending || !this.selStart) return
      const r = rect()
      const x = e.clientX - r.left
      const y = e.clientY - r.top
      if (!this.selBox && Math.hypot(x - this.selStart.x, y - this.selStart.y) > 6) {
        e.stopPropagation() // cytoscape's pan must not start
        this.selBox = document.createElement('div')
        this.selBox.className = 'sel-box'
        container.appendChild(this.selBox)
      }
      if (this.selBox) {
        e.stopPropagation()
        const x1 = Math.min(this.selStart.x, x)
        const y1 = Math.min(this.selStart.y, y)
        this.selBox.style.left = `${x1}px`
        this.selBox.style.top = `${y1}px`
        this.selBox.style.width = `${Math.abs(x - this.selStart.x)}px`
        this.selBox.style.height = `${Math.abs(y - this.selStart.y)}px`
      }
    }
    const onUp = (e: MouseEvent): void => {
      this.selPending = false
      if (!this.selBox || !this.selStart) return
      e.stopPropagation()
      const r = rect()
      const x = e.clientX - r.left
      const y = e.clientY - r.top
      const x1 = Math.min(this.selStart.x, x)
      const y1 = Math.min(this.selStart.y, y)
      const x2 = Math.max(this.selStart.x, x)
      const y2 = Math.max(this.selStart.y, y)
      const inBox = (px: number, py: number): boolean =>
        px >= x1 && px <= x2 && py >= y1 && py <= y2
      this.cy.batch(() => {
        this.cy.nodes().forEach((n) => {
          const p = n.renderedPosition()
          if (inBox(p.x, p.y)) n.select()
        })
      })
      this.selBox.remove()
      this.selBox = null
      this.selStart = null
    }
    // capture phase: runs before cytoscape's bubble-phase handlers
    container.addEventListener('mousedown', onDown, true)
    container.addEventListener('mousemove', onMove, true)
    container.addEventListener('mouseup', onUp, true)
    this.selBoxCleanup = () => {
      container.removeEventListener('mousedown', onDown, true)
      container.removeEventListener('mousemove', onMove, true)
      container.removeEventListener('mouseup', onUp, true)
      this.selBox?.remove()
    }
  }

  private selBoxCleanup: (() => void) | null = null

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
    const srcLabel = String(edge.source().data('label') || edge.source().data('name') || edge.source().id()).split('\n')[0]
    const tgtLabel = String(edge.target().data('label') || edge.target().data('name') || edge.target().id()).split('\n')[0]
    const act = this.edgeActivity.get(edge.id())
    const rows: string[] = []
    rows.push(`${kindText} · ${String(d.proto ?? '')} · ${uniq.length} conn${uniq.length === 1 ? '' : 's'}`)
    rows.push(`${srcLabel} → ${tgtLabel}`)
    rows.push(`ports ${uniq.slice(0, 6).join(', ')}${uniq.length > 6 ? '…' : ''}`)
    // traffic fields ONLY when actual telemetry exists for this edge
    if (act && (act.fwdBps > 0 || act.revBps > 0)) {
      rows.push(`Traffic: ↓ ${fmtBps(act.revBps)} · ↑ ${fmtBps(act.fwdBps)}`)
      const ageMs = Math.round(performance.now() - act.lastActivity)
      rows.push(`Last activity: ${ageMs} ms ago`)
    } else {
      rows.push('Traffic: — (no observed activity)')
    }
    rows.push(`Telemetry: ${this.telemetrySource}`)
    if (!this.tooltip) {
      this.tooltip = document.createElement('div')
      this.tooltip.className = 'edge-tooltip'
      this.cy.container()?.parentElement?.appendChild(this.tooltip)
    }
    this.tooltip.innerHTML = rows.map((r) => `<div>${r}</div>`).join('')
    this.tooltip.style.left = `${pos.x + 12}px`
    this.tooltip.style.top = `${pos.y - 12}px`
    this.tooltip.style.display = 'block'
  }

  private hideTooltip(): void {
    if (this.tooltip) this.tooltip.style.display = 'none'
  }

  destroy(): void {
    if (this.activityDecayTimer !== undefined) {
      clearInterval(this.activityDecayTimer)
      this.activityDecayTimer = undefined
    }
    this.resizeObs?.disconnect()
    this.resizeObs = undefined
    this.selBoxCleanup?.()
    this.overlay.destroy()
  }
}
