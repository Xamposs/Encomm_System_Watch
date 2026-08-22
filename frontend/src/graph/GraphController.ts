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
import type { ZoneInfo } from './WireUnderlay'
import { perf } from './PerfMonitor'

cytoscape.use(fcose)

export function fmtBps(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)} MB/s`
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)} KB/s`
  return `${Math.round(v)} B/s`
}

/**
 * Deterministic per-edge curve offset (v0.6.0 UI fidelity): every edge gets a
 * stable bezier control point. v1.0.2 topology composition: the curve is
 * lane-aware — edges that share the same source band and target band (the
 * x-columns of the composed rack map) bow in the SAME direction with a
 * coordinated magnitude, so groups of real edges visibly travel shared
 * corridors and fan out/in instead of one-random-curve-per-edge. A small
 * per-edge deterministic jitter keeps them distinct (never a single thick
 * fake line). Pure function of element state (no cy mutation), so it is
 * safe inside a cytoscape style mapper; falls back to the v0.6.0 hash when
 * composition data is absent (runtime-added edges).
 */
function edgeCurveDist(e: EdgeSingular): number {
  const s = e.source().position()
  const t = e.target().position()
  const len = Math.hypot(t.x - s.x, t.y - s.y) || 1
  const id = e.id()
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0
  const sb = Number(e.source().data('rackBand') ?? 0)
  const tb = Number(e.target().data('rackBand') ?? 0)
  // Reference-style wiring field: stable but deliberately varied bows. The
  // relationship stays exact; only the visual route is diversified so real
  // edges do not collapse into a few thick corridors.
  const sign = ((h >>> 2) & 1) === 0 ? 1 : -1
  const lane = ((Math.abs(sb - tb) + ((h >>> 5) & 15)) % 9) - 4
  const strength = 0.13 + ((h >>> 10) & 7) * 0.018
  const d = Math.min(230, Math.max(38, len * strength + lane * 8))
  return sign * d
}

export const STYLESHEET: StylesheetStyle[] = [
  {
    selector: 'node',
    style: {
      // cytoscape 3.34 does NOT apply the data(label) default mapping — an
      // explicit mapping is required or every card renders blank (v0.6.0)
      label: 'data(label)',
      'background-color': '#141f33',
      'border-color': '#4a6893',
      'border-width': 1.1,
      color: '#cfe0f5',
      'font-family': 'Consolas, "Cascadia Mono", monospace',
      'font-size': 11.5,
      'text-valign': 'center',
      'text-halign': 'center',
      'text-wrap': 'wrap',
      'text-max-width': '95px',
      'text-overflow-wrap': 'anywhere',
      // subtle backing behind label text keeps dense wiring from slicing
      // through card text (reference control-room readability)
      'text-background-color': '#0a0f16',
      'text-background-opacity': 0.5,
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
    style: { shape: 'round-rectangle', width: 112, height: 40, 'border-color': '#4d70a0', 'border-width': 1.1 },
  },
  {
    selector: 'node[kind = "PROCESS"].zoom-close',
    style: { 'font-size': 11, height: 42 },
  },
  {
    selector: 'node[kind = "SYSTEM"]',
    style: {
      shape: 'round-rectangle', width: 158, height: 42, 'border-color': '#7fa3d0',
      'border-width': 1.7, 'background-color': '#1e3450', 'font-size': 13.5,
    },
  },
  // v1.0.2: banked node populations (stopped services / orphan processes).
  // The 0-edge stopped-service population is composed into compact dim banks
  // instead of scattering across the map — same truthful nodes, same counts,
  // much lower visual weight (they remain clickable + fully inspectable).
  {
    selector: 'node.svc-bank',
    style: {
      width: 96, height: 26, 'font-size': 9.5,
      'background-color': '#1a1506', 'border-color': '#a58544', 'border-width': 1.0,
      color: '#d9b87f', 'border-style': 'dashed', opacity: 0.92,
      'text-background-opacity': 0.4,
    },
  },
  {
    selector: 'node.orphan-bank',
    style: {
      width: 92, height: 24, 'font-size': 9,
      'background-color': '#0d1118', 'border-color': '#4a6893', 'border-width': 0.9,
      color: '#9db3cd', opacity: 0.8, 'text-background-opacity': 0.35,
    },
  },
  // the connected core keeps slightly brighter edges to lead the eye
  {
    selector: 'node.core-node',
    style: { 'border-color': '#5789bd', 'border-width': 1.15, 'background-color': '#132336' },
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
  // AI/INFRA view dimming MUST live in the cytoscape stylesheet (canvas);
  // a DOM CSS rule can never affect canvas-rendered elements (v1.0.2 fix)
  { selector: 'node.ai-dim, edge.ai-dim', style: { opacity: 0.14 } },
  { selector: 'edge.ai-dim', style: { opacity: 0.1 } },
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
      'line-color': '#3f5a75',
      width: 1.05,
      'curve-style': 'unbundled-bezier',
      'control-point-distances': (e) => edgeCurveDist(e as EdgeSingular),
      'control-point-weights': 0.5,
      opacity: 0.26,
      'target-arrow-shape': 'triangle',
      'target-arrow-color': '#3f5a75',
      'arrow-scale': 0.5,
      // invisible overlay widens the hover hit-area (tooltip friendliness)
      'overlay-color': '#35e0ff',
      'overlay-opacity': 0,
      'overlay-padding': 10,
      'transition-property': 'opacity, line-color, width',
      'transition-duration': '300ms' as unknown as number,
    },
  },
  { selector: 'edge[kind = "LOCALHOST"]', style: { 'line-color': '#4fd2f7', 'target-arrow-shape': 'none', width: 1.2, opacity: 0.38 } },
  { selector: 'edge[kind = "LISTEN"]', style: { 'line-color': '#5ee89a', 'target-arrow-shape': 'none', 'line-style': 'dashed', width: 1.15, opacity: 0.42 } },
  { selector: 'edge[kind = "EXTERNAL"]', style: { 'line-color': '#3f9fe8', 'target-arrow-color': '#3f9fe8', width: 1.2, opacity: 0.4 } },
  // ---- semantic edges (v0.3.0) -------------------------------------------
  { selector: 'edge[kind = "USES_GPU"]', style: { 'line-color': '#2fe6a8', 'target-arrow-color': '#2fe6a8', 'line-style': 'dashed', width: 1.7 } },
  { selector: 'edge[kind = "SERVES_MODEL"]', style: { 'line-color': '#b06cff', 'target-arrow-color': '#b06cff', width: 1.8 } },
  { selector: 'edge[kind = "LOCAL_API"]', style: { 'line-color': '#5fc8ff', 'target-arrow-color': '#5fc8ff', 'line-style': 'dashed', width: 1.4 } },
  { selector: 'edge[kind = "HOSTS"]', style: { 'line-color': '#e8a04a', 'target-arrow-color': '#e8a04a', 'line-style': 'dashed', width: 1.2 } },
  { selector: 'edge[kind = "PROCESS_PARENT"]', style: { 'line-color': '#6b7d95', 'target-arrow-color': '#6b7d95', 'line-style': 'dotted', width: 1.1 } },
  { selector: 'edge[kind = "SPAWNED"]', style: { 'line-color': '#9a7cf0', 'target-arrow-color': '#9a7cf0', 'line-style': 'dotted', width: 1.2 } },
  { selector: 'edge[kind = "MEMBER_OF"]', style: { 'line-color': '#a78bfa', 'target-arrow-color': '#a78bfa', 'line-style': 'dotted', width: 1.0 } },
  // ---- infrastructure edges (v0.4.0) --------------------------------------
  { selector: 'edge[kind = "HOSTED_BY"]', style: { 'line-color': '#e8a04a', 'target-arrow-color': '#e8a04a', 'line-style': 'dashed', width: 1.3 } },
  { selector: 'edge[kind = "EXPOSES"]', style: { 'line-color': '#2dd4bf', 'target-arrow-color': '#2dd4bf', 'line-style': 'dashed', width: 1.2 } },
  { selector: 'edge[kind = "CONNECTED_TO"]', style: { 'line-color': '#7f93ac', 'target-arrow-color': '#7f93ac', 'line-style': 'dotted', width: 1.0 } },
  { selector: 'edge[kind = "BACKED_BY"]', style: { 'line-color': '#e879f9', 'target-arrow-color': '#e879f9', 'line-style': 'dashed', width: 1.2 } },
  { selector: 'edge[?active]', style: { 'line-color': '#5ec9e8', 'target-arrow-color': '#5ec9e8' } },
  { selector: 'edge[?recent]', style: { 'line-color': '#6fd4ee', 'target-arrow-color': '#6fd4ee' } },
  // real observed traffic subtly brightens + thickens the edge; decays back
  { selector: 'edge[?actLow]', style: { 'line-color': '#35b8d6', 'target-arrow-color': '#35b8d6', width: 1.5 } },
  { selector: 'edge[?actMed]', style: { 'line-color': '#22d8f5', 'target-arrow-color': '#22d8f5', width: 2 } },
  { selector: 'edge[?actHigh]', style: { 'line-color': '#35e0ff', 'target-arrow-color': '#35e0ff', width: 2.6 } },
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
  // The visible node surface is the rich HTML card layer. Cytoscape still
  // owns these transparent node rectangles for layout, edge endpoints,
  // hit-testing, selection and all graph interactions.
  {
    selector: 'node',
    style: {
      width: 184,
      height: 64,
      shape: 'round-rectangle',
      label: '',
      'background-opacity': 0,
      'border-opacity': 0,
      'text-opacity': 0,
      'overlay-opacity': 0,
    },
  },
  // Adaptive performance LOD: near uses virtualized HTML cards, mid uses
  // compact native cards, and far uses cheap but clearly visible mini-nodes.
  {
    selector: 'node.card-lod-mid',
    style: {
      label: 'data(cardTitle)',
      width: 210,
      height: 60,
      'background-opacity': 0.78,
      'background-color': '#111a29',
      'border-opacity': 0.9,
      'border-color': '#37667b',
      'border-width': 1,
      'text-opacity': 0.86,
      color: '#a9bdd0',
      'font-size': 9.5,
      'text-background-opacity': 0,
      'text-max-width': '188px',
    },
  },
  {
    selector: 'node.card-lod-far',
    style: {
      label: 'data(cardGlyph)',
      width: 110,
      height: 36,
      'background-opacity': 0.86,
      'background-color': '#101c2d',
      'border-opacity': 1,
      'border-color': '#35bdda',
      'border-width': 1.2,
      'text-opacity': 0.94,
      color: '#a8eff8',
      'font-size': 8,
      'font-weight': 700,
      'text-background-opacity': 0,
    },
  },
  {
    // The existence selector makes this more specific than every kind rule,
    // so the native hit-box cannot show through the premium HTML card.
    selector: 'node.card-lod-near[kind]',
    style: {
      width: 184, height: 64, label: '',
      'background-opacity': 0, 'border-opacity': 0, 'text-opacity': 0,
    },
  },
  { selector: 'node.card-lod-mid[kind = "SERVICE"], node.card-lod-far[kind = "SERVICE"]', style: { 'border-color': '#a87319', color: '#d7aa57' } },
  { selector: 'node.card-lod-mid[kind = "GPU"], node.card-lod-mid[kind = "LISTENING_PORT"], node.card-lod-far[kind = "GPU"], node.card-lod-far[kind = "LISTENING_PORT"]', style: { 'border-color': '#2a9f77', color: '#71d8b1' } },
  { selector: 'node.card-lod-mid[kind = "SEMANTIC"], node.card-lod-mid[kind = "AI_RUNTIME"], node.card-lod-far[kind = "SEMANTIC"], node.card-lod-far[kind = "AI_RUNTIME"]', style: { 'border-color': '#b83a83', color: '#e477b9' } },
  { selector: 'node.card-lod-mid[kind = "EXTERNAL_ENDPOINT"], node.card-lod-far[kind = "EXTERNAL_ENDPOINT"]', style: { 'border-color': '#2786ae', color: '#70c8eb' } },
  {
    selector: 'node.signal-source, node.signal-target',
    style: {
      'border-opacity': 1,
      'border-color': '#b8f7ff',
      'border-width': 2.4,
      'overlay-opacity': 0.25,
      'overlay-color': '#35e0ff',
    },
  },
  {
    selector: 'edge.signal-live',
    style: {
      'line-color': '#35e0ff',
      'target-arrow-color': '#35e0ff',
      opacity: 0.92,
      width: 2.4,
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

interface ViewLayoutCacheEntry {
  revision: number
  signature: string
  positions: Map<string, { x: number; y: number }>
  zones: ZoneInfo[]
  columns: number
  rows: number
}

type CameraTarget = 'overview' | 'current'

const ZOOM_FAR = 0.09
const ZOOM_CLOSE = 0.5

// incremental-layout policy (v0.3.1): on large graphs, small node additions
// must NOT trigger an expensive fcose pass over the whole graph. Above
// LARGE_GRAPH_NODES, steady additions accumulate until the maintenance gate
// in scheduleIncrementalLayout() is reached.
const LARGE_GRAPH_NODES = 600
const VIEW_GAP_X = 280
const VIEW_GAP_Y = 230
const CARD_PITCH_X = 232
const CARD_PITCH_Y = 88

// ---- transient AI runtime node budgets (v0.5.0) ---------------------------
// High-frequency AI events must never permanently explode graph node count:
// hard node cap + per-role TTL, pruned by the shared 1 s decay timer.
const MAX_AI_RUNTIME_NODES = 24
const AI_RUN_TTL_MS = 90_000
const AI_RUN_FINISHED_TTL_MS = 12_000
const AI_SPAN_TTL_MS = 45_000
const TOPOLOGY_EVENT_TYPES = new Set<string>([
  'PROCESS_STARTED', 'PROCESS_STOPPED', 'CONNECTION_OPENED', 'CONNECTION_CLOSED',
  'HERMES_DETECTED', 'LM_STUDIO_DETECTED', 'MCP_SERVER_DETECTED', 'SEMANTIC_DETECTED',
  'MODEL_LOADED', 'MODEL_AVAILABLE', 'SEMANTIC_LOST', 'GPU_PROCESS_ATTACHED',
  'GPU_PROCESS_DETACHED', 'SERVICE_STARTED', 'SERVICE_STOPPED', 'SERVICE_STATUS_CHANGED',
  'CONTAINER_STARTED', 'CONTAINER_STOPPED', 'CONTAINER_CREATED', 'CONTAINER_REMOVED',
  'WSL_STATE_CHANGED', 'VM_DETECTED', 'VM_STATE_CHANGED', 'VM_LOST',
])

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
  private familySyncTimer: number | undefined
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
  // ---- camera/viewport -------------------------------------------------
  /** layout lifecycle state (surfaced by viewportHealth()) */
  private layoutState: 'idle' | 'active' = 'idle'
  /** v1.0.2 rack composition shape (surfaced by topologyMetrics()) */
  private rackColumns = 0
  private rackRows = 0
  /** v1.0.2 final pass: semantic composition zones (model space). */
  private zoneLayout: ZoneInfo[] = []
  /** Deterministic per-view geometry. Metric ticks never invalidate it. */
  private viewLayoutCache = new Map<string, ViewLayoutCacheEntry>()
  private topologyRevision = 0
  private layoutCacheHits = 0
  private layoutCacheMisses = 0
  private cameraRaf = 0
  private cameraRaf2 = 0
  private cameraToken = 0
  private interactionTimer: number | undefined
  private interacting = false
  private lastFitMs = 0
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
    cy.on('add', 'node', (ev) => {
      // Derived family cards are a view projection, not topology churn. Counting
      // them here used to trigger a second full composition on every family sync.
      if (ev.target.data('family')) return
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
    cy.on('pan zoom drag', this.markInteraction)
    cy.on('free', this.markInteraction)
    perf.setGraphSource({
      nodes: () => this.cy.nodes().length,
      edges: () => this.cy.edges().length,
      visibleNodes: () => this.cy.nodes(':visible').length,
      visibleEdges: () => this.cy.edges(':visible').length,
      particles: () => this.overlay.stats().particles,
      overlayRunning: () => this.overlay.stats().running,
      overlayActivity: () => this.overlay.stats().activity,
      mountedCards: () => Number(this.cy.container()?.querySelector('.graph-card-layer')?.getAttribute('data-mounted') ?? 0),
      graphAspectRatio: () => this.currentGeometry().graphAspectRatio,
      viewportAspectRatio: () => this.currentGeometry().viewportAspectRatio,
      layoutCacheHits: () => this.layoutCacheHits,
      layoutCacheMisses: () => this.layoutCacheMisses,
      fitMs: () => this.lastFitMs,
    })
  }

  private markInteraction = (): void => {
    if (!this.interacting) {
      this.interacting = true
      this.cy.container()?.dispatchEvent(new CustomEvent('esw:interaction', { detail: { active: true } }))
    }
    if (this.interactionTimer !== undefined) window.clearTimeout(this.interactionTimer)
    this.interactionTimer = window.setTimeout(() => {
      this.interactionTimer = undefined
      this.interacting = false
      this.cy.container()?.dispatchEvent(new CustomEvent('esw:interaction', { detail: { active: false } }))
      this.refreshCardOverlay()
    }, 160)
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
    this.applyFilter()
    this.composeCurrentView()
    this.scheduleCamera('current')
  }

  setTelemetry(t: TelemetryInfo | undefined): void {
    this.telemetrySource =
      t?.source && t.source !== 'NONE' ? t.source : 'SOCKET EVENTS (TIER 0)'
  }

  setSignalAnimations(on: boolean): void {
    this.overlay.setEnabled(on)
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
    this.refreshCardOverlay()
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
    return this.graphInteriorPosition(120)
  }

  /**
   * Bounded model-space placement for runtime-added nodes (v1.0.2 AG7 fix).
   *
   * New nodes land INSIDE the graph's content bounding box — NEVER at the
   * camera extent (viewport center). The extent follows the camera, which
   * can be panned to extreme coordinates (e.g. FIT-ALL tests pan far away);
   * a node placed there poisons the next fit()'s bounding box, collapsing
   * the camera to minZoom and leaving the graph off-screen (viewport=0).
   */
  private graphInteriorPosition(jitter: number): { x: number; y: number } {
    const bb = this.cy.elements().boundingBox()
    if (bb && Number.isFinite(bb.x1) && Number.isFinite(bb.y1) &&
      Number.isFinite(bb.x2) && Number.isFinite(bb.y2)) {
      return {
        x: bb.x1 + (bb.x2 - bb.x1) * 0.15 + (Math.random() - 0.5) * jitter,
        y: bb.y1 + (bb.y2 - bb.y1) * 0.10 + (Math.random() - 0.5) * jitter,
      }
    }
    return {
      x: 200 + (Math.random() - 0.5) * jitter,
      y: 100 + (Math.random() - 0.5) * jitter,
    }
  }

  /**
   * Deterministic vacant-slot placement for nodes arriving between full rack
   * compositions. It keeps live process/socket churn readable immediately,
   * while the cumulative maintenance pass below eventually reclassifies the
   * node into its final semantic zone.
   */
  private incrementalRackPosition(nodeId: string, anchorId?: string): { x: number; y: number } {
    const nodes = this.cy.nodes()
    if (!nodes.length) return { x: 200, y: 100 }
    const occupied: Array<{ x: number; y: number }> = []
    nodes.forEach((node) => {
      const p = node.position()
      if (Number.isFinite(p.x) && Number.isFinite(p.y)) occupied.push({ x: p.x, y: p.y })
    })
    const anchor = anchorId ? this.cy.getElementById(anchorId) : null
    const bb = nodes.boundingBox()
    const base = anchor?.length
      ? anchor.position()
      : {
          x: bb.x1 + Math.max(CARD_PITCH_X, bb.w * 0.15),
          y: bb.y1 + Math.max(CARD_PITCH_Y, bb.h * 0.10),
        }
    let hash = 2166136261
    for (let i = 0; i < nodeId.length; i++) {
      hash ^= nodeId.charCodeAt(i)
      hash = Math.imul(hash, 16777619)
    }
    hash >>>= 0
    const vacant = (candidate: { x: number; y: number }): boolean =>
      occupied.every((p) =>
        Math.abs(p.x - candidate.x) >= CARD_PITCH_X * 0.72 ||
        Math.abs(p.y - candidate.y) >= CARD_PITCH_Y * 0.72,
      )
    for (let radius = 0; radius <= 14; radius++) {
      const ring: Array<{ dx: number; dy: number }> = []
      if (radius === 0) ring.push({ dx: 0, dy: 0 })
      else {
        for (let dx = -radius; dx <= radius; dx++) {
          for (let dy = -radius; dy <= radius; dy++) {
            if (Math.max(Math.abs(dx), Math.abs(dy)) === radius) ring.push({ dx, dy })
          }
        }
      }
      const start = ring.length ? hash % ring.length : 0
      for (let i = 0; i < ring.length; i++) {
        const offset = ring[(start + i) % ring.length]
        const candidate = {
          x: base.x + offset.dx * CARD_PITCH_X,
          y: base.y + offset.dy * CARD_PITCH_Y,
        }
        if (vacant(candidate)) return candidate
      }
    }
    const columns = Math.max(12, this.rackColumns || Math.ceil(Math.sqrt(nodes.length)))
    return {
      x: bb.x1 + (hash % columns) * CARD_PITCH_X,
      y: bb.y2 + CARD_PITCH_Y,
    }
  }

  /** Cheap overlap alarm used only after node-add batches. */
  private hasLayoutPileup(): boolean {
    const buckets = new Map<string, number>()
    const bucketX = CARD_PITCH_X * 0.45
    const bucketY = CARD_PITCH_Y * 0.45
    for (const node of this.cy.nodes(':visible')) {
      if (node.hasClass('fading')) continue
      const p = node.position()
      const key = `${Math.round(p.x / bucketX)}:${Math.round(p.y / bucketY)}`
      const count = (buckets.get(key) ?? 0) + 1
      if (count >= 7) return true
      buckets.set(key, count)
    }
    return false
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
    this.invalidateTopology()
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
    if (this.familyView === 'families') {
      this.syncFamilyView()
      this.applyFilter()
      this.composeCurrentView(true)
      this.scheduleCamera('current')
    }
    if (this.focusNode) this.applyFocus()
    if (this.view !== 'system') this.applyView()
    perf.recordUpdate(performance.now() - t0)
  }

  private flattenNode(n: TopoNode): Record<string, unknown> {
    const { data, ...rest } = n
    const flat = { ...rest, ...data } as Record<string, unknown>
    const rawTitle = data.display_name ?? data.name ?? data.model_id ?? data.address ?? rest.label ?? n.id
    flat.cardTitle = String(rawTitle ?? n.id).replace(/\s+/g, ' ').split(' · ')[0].slice(0, 30)
    const kind = String(data.kind ?? rest.kind ?? '')
    flat.cardGlyph = kind === 'SERVICE' ? '■' : kind === 'PROCESS' ? '•' : kind === 'LISTENING_PORT' ? '◆' : '◇'
    return flat
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
    if (this.familyView === 'families') {
      if (ev.event_type === 'PROCESS_METRICS_UPDATED') this.updateFamilyMetricsForMember(ev.source)
      else if (TOPOLOGY_EVENT_TYPES.has(ev.event_type)) {
        this.scheduleFamilySync()
      }
    }
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
    this.invalidateTopology()
    if (born) data.born = true
    // Place live additions in the closest vacant rack slot. The previous
    // random ±180 placement accumulated steady small batches around the same
    // anchor/interior point, eventually producing the hours-later pile-up.
    const position = this.incrementalRackPosition(node.id, anchorId)
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
        this.scheduleFamilySync()
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
      this.invalidateTopology()
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
        if (cur.length) {
          cur.remove()
          this.invalidateTopology()
        }
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
        if (cur.length) {
          cur.remove()
          this.invalidateTopology()
        }
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
    const compact = this.cy.zoom() < ZOOM_FAR
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

  /** Metric-only family refresh. Avoids the old O(nodes + edges) family
   * rebuild on every PROCESS_METRICS_UPDATED event. */
  private updateFamilyMetricsForMember(sid: string): void {
    const families = this.cy.nodes('[?family]')
    let family: NodeSingular | null = null
    families.forEach((candidate) => {
      if (family) return
      const members = (candidate.data('member_ids') as string[] | undefined) ?? []
      if (members.includes(sid)) family = candidate as NodeSingular
    })
    if (!family) return
    const members = ((family as NodeSingular).data('member_ids') as string[] | undefined) ?? []
    let cpu = 0
    let memory = 0
    let connections = 0
    for (const memberId of members) {
      const member = this.cy.getElementById(memberId)
      if (!member.length) continue
      cpu += Number(member.data('cpu_percent') ?? 0)
      memory += Number(member.data('memory_mb') ?? 0)
      connections += Number(member.data('conn_count') ?? 0)
    }
    this.cy.batch(() => {
      ;(family as NodeSingular).data('cpu_percent', cpu)
      ;(family as NodeSingular).data('memory_mb', memory)
      ;(family as NodeSingular).data('conn_count', connections)
    })
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
  }

  /** Coalesce bursty lifecycle events into one family projection refresh.
   * A busy socket feed previously rebuilt the whole projection per event. */
  private scheduleFamilySync(): void {
    if (this.familySyncTimer !== undefined) return
    this.familySyncTimer = window.setTimeout(() => {
      this.familySyncTimer = undefined
      if (this.familyView !== 'families') return
      this.syncFamilyView()
      if (this.filter !== 'all') this.applyFilter()
    }, 350)
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
    this.refreshCardOverlay()
  }

  // ------------------------------------------------------------ layout / view

  private refreshLabelFor(id: string): void {
    const el = this.cy.getElementById(id)
    if (!el.length || !el.data('family')) return
    const name = String(el.data('name') ?? '')
    const count = (el.data('member_ids') as string[] | undefined)?.length ?? 0
    el.data('label', `${name} ×${count}`)
  }

  private invalidateTopology(): void {
    this.topologyRevision += 1
    this.viewLayoutCache.clear()
  }

  private targetViewportAspect(): number {
    const width = Math.max(1, this.cy.width())
    const height = Math.max(1, this.cy.height())
    return Math.max(1.55, Math.min(1.95, width / height))
  }

  private viewLayoutKey(): string {
    return `${this.filter}:${this.familyView}`
  }

  private nodeSignature(nodes: cytoscape.NodeCollection): string {
    const ids = nodes.map((node) => node.id()).sort()
    let hash = 2166136261
    for (const id of ids) {
      for (let i = 0; i < id.length; i++) {
        hash ^= id.charCodeAt(i)
        hash = Math.imul(hash, 16777619)
      }
    }
    return `${ids.length}:${hash >>> 0}`
  }

  private storeViewLayout(
    key: string,
    nodes: cytoscape.NodeCollection,
    zones: ZoneInfo[],
    columns: number,
    rows: number,
  ): void {
    const positions = new Map<string, { x: number; y: number }>()
    nodes.forEach((node) => {
      const p = node.position()
      positions.set(node.id(), { x: p.x, y: p.y })
    })
    this.viewLayoutCache.set(key, {
      revision: this.topologyRevision,
      signature: this.nodeSignature(nodes),
      positions,
      zones: zones.map((zone) => ({ ...zone })),
      columns,
      rows,
    })
  }

  private restoreViewLayout(key: string, nodes: cytoscape.NodeCollection): boolean {
    const cached = this.viewLayoutCache.get(key)
    if (!cached || cached.revision !== this.topologyRevision || cached.signature !== this.nodeSignature(nodes)) {
      this.layoutCacheMisses += 1
      return false
    }
    this.cy.batch(() => {
      nodes.forEach((node) => {
        const p = cached.positions.get(node.id())
        if (p) node.position(p)
      })
    })
    this.zoneLayout = cached.zones.map((zone) => ({ ...zone }))
    this.rackColumns = cached.columns
    this.rackRows = cached.rows
    this.layoutCacheHits += 1
    this.cy.container()?.dispatchEvent(new CustomEvent('esw:layout'))
    return true
  }

  /** Compact deterministic layout for the CURRENT visible representation.
   * Hidden inventory keeps its full-layout coordinates and is never deleted. */
  private composeCurrentView(force = false): void {
    this.cy.style().update()
    const nodes = this.cy.nodes(':visible')
    if (!nodes.length) return
    const key = this.viewLayoutKey()
    if (!force && this.restoreViewLayout(key, nodes)) return
    const t0 = performance.now()
    if (key === 'all:nodes') {
      this.composeRackLayout()
      perf.recordLayout(performance.now() - t0)
      return
    }

    const visibleIds = new Set(nodes.map((node) => node.id()))
    const degree = new Map<string, number>()
    this.cy.edges(':visible').forEach((edge) => {
      const source = edge.source().id()
      const target = edge.target().id()
      if (!visibleIds.has(source) || !visibleIds.has(target)) return
      degree.set(source, (degree.get(source) ?? 0) + 1)
      degree.set(target, (degree.get(target) ?? 0) + 1)
    })
    const priority = (node: NodeSingular): number => {
      const kind = String(node.data('kind') ?? '')
      if (kind === 'SYSTEM' || kind === 'GPU' || kind === 'SEMANTIC' || kind === 'LOCAL_LLM') return 0
      if (node.data('family')) return 1
      if ((degree.get(node.id()) ?? 0) > 0) return 2
      return 3
    }
    const ordered = nodes.toArray().sort((a, b) =>
      priority(a) - priority(b) ||
      (degree.get(b.id()) ?? 0) - (degree.get(a.id()) ?? 0) ||
      a.id().localeCompare(b.id()),
    )
    const targetAspect = this.targetViewportAspect()
    let columns = 1
    let delta = Number.POSITIVE_INFINITY
    for (let candidate = 1; candidate <= Math.min(36, ordered.length); candidate++) {
      const rows = Math.ceil(ordered.length / candidate)
      const width = (candidate - 1) * CARD_PITCH_X + 184
      const height = (rows - 1) * CARD_PITCH_Y + 64
      const next = Math.abs(width / Math.max(1, height) - targetAspect)
      if (next < delta) {
        delta = next
        columns = candidate
      }
    }
    const rows = Math.max(1, Math.ceil(ordered.length / columns))
    this.cy.batch(() => {
      ordered.forEach((node, index) => {
        const column = index % columns
        const row = Math.floor(index / columns)
        let hash = 0
        for (let i = 0; i < node.id().length; i++) hash = (hash * 31 + node.id().charCodeAt(i)) | 0
        node.position({
          x: column * CARD_PITCH_X + ((hash >>> 4) % 9) - 4,
          y: row * CARD_PITCH_Y + ((hash >>> 9) % 7) - 3,
        })
        node.data('rackBand', column)
      })
    })
    const label = `${this.filter.toUpperCase()} · ${this.familyView.toUpperCase()} · CURRENT VISIBLE TOPOLOGY`
    const width = Math.max(184, (columns - 1) * CARD_PITCH_X + 184)
    this.zoneLayout = [{ label, role: 'filtered-view', x0: -12, x1: width + 12, y0: -50 }]
    this.rackColumns = columns
    this.rackRows = rows
    this.storeViewLayout(key, nodes, this.zoneLayout, columns, rows)
    this.cy.container()?.dispatchEvent(new CustomEvent('esw:layout'))
    perf.recordLayout(performance.now() - t0)
  }

  private scheduleCamera(target: CameraTarget): void {
    const token = ++this.cameraToken
    if (this.cameraRaf) cancelAnimationFrame(this.cameraRaf)
    if (this.cameraRaf2) cancelAnimationFrame(this.cameraRaf2)
    this.cameraRaf = requestAnimationFrame(() => {
      this.cameraRaf = 0
      this.cameraRaf2 = requestAnimationFrame(() => {
        this.cameraRaf2 = 0
        if (token !== this.cameraToken) return
        const t0 = performance.now()
        this.cy.resize()
        this.cy.style().update()
        const overview = this.cy.nodes('.overview-node:visible')
        // Curved edge control points can extend far outside their endpoints;
        // including them in fit() recreated the thin horizontal-strip bug.
        // Framing the visible nodes still frames the full truthful topology.
        const elements = target === 'overview' && overview.length
          ? overview
          : this.cy.nodes(':visible')
        if (!elements.length) return
        this.cy.fit(elements, target === 'overview' ? 54 : 62)
        if (target === 'overview' && this.cy.zoom() > 0.92) {
          this.cy.zoom({
            level: 0.92,
            renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 },
          } as unknown as ZoomOptions)
        }
        this.lastFitMs = performance.now() - t0
        perf.recordFit(this.lastFitMs)
        this.refreshCardOverlay()
        this.cy.container()?.dispatchEvent(new CustomEvent('esw:layout'))
      })
    })
  }

  private currentGeometry(): { graphAspectRatio: number; viewportAspectRatio: number; viewportCoverage: number } {
    const nodes = this.cy.nodes(':visible')
    const bb = nodes.length ? nodes.boundingBox() : null
    const width = Math.max(1, this.cy.width())
    const height = Math.max(1, this.cy.height())
    const graphAspectRatio = bb && bb.h > 0 ? bb.w / bb.h : 0
    const renderedArea = bb ? (bb.w * this.cy.zoom()) * (bb.h * this.cy.zoom()) : 0
    return {
      graphAspectRatio: Number(graphAspectRatio.toFixed(3)),
      viewportAspectRatio: Number((width / height).toFixed(3)),
      viewportCoverage: Number(Math.min(1, renderedArea / (width * height)).toFixed(3)),
    }
  }

  /**
   * Deterministic reference-style machine map.
   *
   * The first camera view is a truthful operational cross-section: anchors,
   * highest-degree connected nodes, running services and active processes.
   * It is presented as two broad card fields rather than trying to shrink the
   * entire Windows process table into one frame. Every remaining real node is
   * still laid out in adjacent zones and remains available through pan/zoom or
   * FIT ALL. No nodes or edges are synthesized and none are discarded.
   */
  private composeRackLayout(): void {
    const cy = this.cy
    const nodes = cy.nodes()
    const n = nodes.length
    if (n === 0) return
    const edges = cy.edges()

    // 1) union-find connected components over REAL edges (any edge kind =
    //    a real relationship — semantic, infra, socket, parentage)
    const parent = new Map<string, string>()
    const compSize = new Map<string, number>()
    const find = (x: string): string => {
      let r = x
      while (parent.get(r) !== r) r = parent.get(r)!
      while (parent.get(x) !== x) { const p = parent.get(x)!; parent.set(x, r); x = p }
      return r
    }
    const union = (a: string, b: string): void => {
      const ra = find(a)
      const rb = find(b)
      if (ra === rb) return
      if ((compSize.get(ra) ?? 1) < (compSize.get(rb) ?? 1)) {
        parent.set(ra, rb)
        compSize.set(rb, (compSize.get(ra) ?? 1) + (compSize.get(rb) ?? 1))
      } else {
        parent.set(rb, ra)
        compSize.set(ra, (compSize.get(ra) ?? 1) + (compSize.get(rb) ?? 1))
      }
    }
    nodes.forEach((nd) => { parent.set(nd.id(), nd.id()); compSize.set(nd.id(), 1) })
    const nodeIds = new Set(nodes.map((nd) => nd.id()))
    edges.forEach((e) => {
      // defensive: skip edges whose endpoints are not in the node set
      // (a snapshot/event race can deliver an edge before its nodes land)
      try {
        const s0 = e.source().id()
        const t0 = e.target().id()
        if (!s0 || !t0 || !nodeIds.has(s0) || !nodeIds.has(t0)) return
        union(s0, t0)
      } catch { /* dangling edge — ignore for composition */ }
    })

    const degree = new Map<string, number>()
    edges.forEach((e) => {
      try {
        const s0 = e.source().id()
        const t0 = e.target().id()
        if (!s0 || !t0) return
        degree.set(s0, (degree.get(s0) ?? 0) + 1)
        degree.set(t0, (degree.get(t0) ?? 0) + 1)
      } catch { /* dangling edge — ignore for composition */ }
    })
    const compRank = new Map<string, number>()
    const roots = [...new Set(nodes.map((nd) => find(nd.id())))]
      .sort((a, b) => (compSize.get(b) ?? 0) - (compSize.get(a) ?? 0))
    roots.forEach((r, i) => compRank.set(r, i))

    // 2) role classification (truthful: derived from node data, never invented)
    const isAnchorKind = (kk: string): boolean =>
      kk === 'SYSTEM' || kk === 'SEMANTIC' || kk === 'GPU' || kk === 'LOCAL_LLM' ||
      kk === 'AI_RUNTIME' || kk === 'WSL' || kk === 'DOCKER_ENGINE' ||
      kk === 'DOCKER_NETWORK' || kk === 'VM' || kk === 'CONTAINER'
    const roleOf = (nd: NodeSingular): 'anchor' | 'core' | 'banked' | 'orphan' => {
      const kk = String(nd.data('kind') ?? '')
      if (isAnchorKind(kk)) return 'anchor'
      const deg = degree.get(nd.id()) ?? 0
      if (kk === 'SERVICE') {
        return String(nd.data('status') ?? '') === 'running' && deg > 0 ? 'core' : 'banked'
      }
      if (kk === 'LISTENING_PORT' || kk === 'LOCAL_ENDPOINT' || kk === 'EXTERNAL_ENDPOINT') {
        return deg > 0 ? 'core' : 'orphan'
      }
      return deg > 0 ? 'core' : 'orphan'
    }

    // 3) ordered deterministic master lists: connectedness and observed
    //    degree decide prominence; names/ids are stable tie-breakers.
    const orderKey = (el: NodeSingular): [number, number, string] => {
      const r = find(el.id())
      const rank = compRank.get(r) ?? 62
      return [rank, -(degree.get(el.id()) ?? 0), el.id()]
    }
    const byOrderKey = (a: NodeSingular, b: NodeSingular): number => {
      const k1 = orderKey(a)
      const k2 = orderKey(b)
      if (k1[0] !== k2[0]) return k1[0] - k2[0]
      if (k1[1] !== k2[1]) return k1[1] - k2[1]
      return k1[2] < k2[2] ? -1 : 1
    }
    const byName = (a: NodeSingular, b: NodeSingular): number => {
      const na = String(a.data('name') ?? a.id())
      const nb = String(b.data('name') ?? b.id())
      return na < nb ? -1 : na > nb ? 1 : 0
    }
    const anchors: NodeSingular[] = []
    const coreN: NodeSingular[] = []
    const banked: NodeSingular[] = []
    const orphanN: NodeSingular[] = []
    nodes.forEach((nd) => {
      const role = roleOf(nd as NodeSingular)
      if (role === 'anchor') anchors.push(nd as NodeSingular)
      else if (role === 'core') coreN.push(nd as NodeSingular)
      else if (role === 'banked') banked.push(nd as NodeSingular)
      else orphanN.push(nd as NodeSingular)
    })
    anchors.sort(byOrderKey)
    coreN.sort(byOrderKey)
    banked.sort((a, b) => {
      const ar = String(a.data('status') ?? '').toLowerCase() === 'running' ? 0 : 1
      const br = String(b.data('status') ?? '').toLowerCase() === 'running' ? 0 : 1
      return ar !== br ? ar - br : byName(a, b)
    })
    orphanN.sort((a, b) => {
      const ac = Number(a.data('cpu_percent') ?? 0)
      const bc = Number(b.data('cpu_percent') ?? 0)
      return ac !== bc ? bc - ac : byName(a, b)
    })

    // 4) Viewport-aware rack geometry. v1.0.2 used fixed eight/nine-row
    // column banks, so hundreds of nodes produced an 8:1+ horizontal strip.
    // Solve the total column count against the real viewport aspect and the
    // actual population in each semantic zone instead.
    const OVERVIEW_ZONE_SIZE = 24
    const strHash = (s: string): number => {
      let h = 0
      for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0
      return h
    }
    const used = new Set<string>()
    const takeUnique = (source: NodeSingular[], limit: number): NodeSingular[] => {
      const out: NodeSingular[] = []
      for (const nd of source) {
        if (used.has(nd.id())) continue
        used.add(nd.id())
        out.push(nd)
        if (out.length >= limit) break
      }
      return out
    }
    const overviewA = takeUnique([...anchors, ...coreN], OVERVIEW_ZONE_SIZE)
    const runningServices = banked.filter((nd) => String(nd.data('status') ?? '').toLowerCase() === 'running')
    const overviewBSeed = [
      ...coreN,
      ...runningServices,
      ...orphanN.filter((nd) => Number(nd.data('cpu_percent') ?? 0) > 0),
      ...banked,
      ...orphanN,
    ]
    const overviewB = takeUnique(overviewBSeed, OVERVIEW_ZONE_SIZE)
    if (overviewA.length < OVERVIEW_ZONE_SIZE) {
      overviewA.push(...takeUnique([...banked, ...orphanN], OVERVIEW_ZONE_SIZE - overviewA.length))
    }
    if (overviewB.length < OVERVIEW_ZONE_SIZE) {
      overviewB.push(...takeUnique([...anchors, ...coreN, ...banked, ...orphanN], OVERVIEW_ZONE_SIZE - overviewB.length))
    }

    const remainingConnected = [...anchors, ...coreN].filter((nd) => !used.has(nd.id()))
    const remainingServices = banked.filter((nd) => !used.has(nd.id()))
    const remainingBackground = orphanN.filter((nd) => !used.has(nd.id()))

    const targetAspect = this.targetViewportAspect()
    const maxCandidate = Math.min(52, Math.max(16, Math.ceil(Math.sqrt(n) * 2.2)))
    let totalColumns = 16
    let bestDelta = Number.POSITIVE_INFINITY
    for (let candidate = 12; candidate <= maxCandidate; candidate++) {
      const leftColumns = Math.max(5, Math.floor(candidate / 2))
      const rightColumns = Math.max(5, candidate - leftColumns)
      const overviewRows = Math.max(
        Math.ceil(overviewA.length / leftColumns),
        Math.ceil(overviewB.length / rightColumns),
        1,
      )
      const mainRows = Math.max(
        Math.ceil(remainingConnected.length / leftColumns),
        Math.ceil(remainingServices.length / rightColumns),
        1,
      )
      const backgroundRows = Math.max(1, Math.ceil(remainingBackground.length / candidate))
      const width = (candidate - 1) * CARD_PITCH_X + 184 + VIEW_GAP_X
      const height = (overviewRows + mainRows + backgroundRows) * CARD_PITCH_Y + VIEW_GAP_Y * 2
      const delta = Math.abs(width / Math.max(1, height) - targetAspect)
      if (delta < bestDelta) {
        bestDelta = delta
        totalColumns = candidate
      }
    }
    const leftColumns = Math.max(5, Math.floor(totalColumns / 2))
    const rightColumns = Math.max(5, totalColumns - leftColumns)
    const rightX = leftColumns * CARD_PITCH_X + VIEW_GAP_X
    const posMap = new Map<string, { x: number; y: number }>()
    const zoneLayout: ZoneInfo[] = []
    const place = (
      list: NodeSingular[],
      role: string,
      label: string,
      x0: number,
      y0: number,
      columns: number,
      overview = false,
    ): { width: number; height: number } => {
      if (!list.length) return { width: 0, height: 0 }
      const safeColumns = Math.max(1, columns)
      const rows = Math.ceil(list.length / safeColumns)
      const cols = Math.min(safeColumns, list.length)
      list.forEach((nd, i) => {
        const c = i % safeColumns
        const r = Math.floor(i / safeColumns)
        const h = strHash(nd.id())
        const jx = ((h >>> 4) % 17) - 8
        const jy = ((h >>> 9) % 13) - 6
        posMap.set(nd.id(), { x: x0 + c * CARD_PITCH_X + jx, y: y0 + r * CARD_PITCH_Y + jy })
        if (overview) nd.scratch('_overview', true)
      })
      const width = Math.max(184, (cols - 1) * CARD_PITCH_X + 184)
      const height = Math.max(64, (rows - 1) * CARD_PITCH_Y + 64)
      zoneLayout.push({ label, role, x0: x0 - 12, x1: x0 + width + 12, y0: y0 - 50 })
      return { width, height }
    }

    const overviewShapeA = place(
      overviewA,
      'overview-primary',
      'LIVE SYSTEM TOPOLOGY  ·  OBSERVED PROCESSES, LINKS & PROVIDERS',
      0,
      0,
      leftColumns,
      true,
    )
    const overviewShapeB = place(
      overviewB,
      'overview-secondary',
      'SYSTEM CUSTODY  ·  SERVICES, INFRASTRUCTURE & ACTIVE WORKLOADS',
      rightX,
      0,
      rightColumns,
      true,
    )
    const overviewHeight = Math.max(overviewShapeA.height, overviewShapeB.height)
    const mainY = overviewHeight + VIEW_GAP_Y
    const connectedShape = place(
      remainingConnected,
      'core',
      'CONNECTED CORE  ·  EXTENDED LIVE GRAPH',
      0,
      mainY,
      leftColumns,
    )
    const servicesShape = place(
      remainingServices,
      'banked',
      'SERVICE REGISTRY  ·  RUNNING & STOPPED',
      rightX,
      mainY,
      rightColumns,
    )
    const mainHeight = Math.max(connectedShape.height, servicesShape.height)
    const backgroundY = mainY + mainHeight + VIEW_GAP_Y
    place(
      remainingBackground,
      'orphan',
      'BACKGROUND WORKLOADS  ·  UNLINKED PROCESS INVENTORY',
      0,
      backgroundY,
      totalColumns,
    )

    // 5) Write positions and visual role metadata in one batch.
    cy.batch(() => {
      nodes.forEach((nd) => {
        const pos = posMap.get(nd.id())
        if (pos) nd.position(pos)
        const originalRole = roleOf(nd as NodeSingular)
        nd.data('rackBand', pos ? Math.round(pos.x / CARD_PITCH_X) : 0)
        nd.removeClass('svc-bank orphan-bank core-node anchor-node overview-node')
        if (originalRole === 'banked') nd.addClass('svc-bank')
        else if (originalRole === 'orphan') nd.addClass('orphan-bank')
        else if (originalRole === 'core') nd.addClass('core-node')
        else if (originalRole === 'anchor') nd.addClass('anchor-node')
        if (nd.scratch('_overview')) {
          nd.addClass('overview-node')
          nd.removeScratch('_overview')
        }
      })
    })
    this.zoneLayout = zoneLayout
    this.rackColumns = totalColumns
    this.rackRows = Math.max(1, Math.ceil((backgroundY + Math.max(64, Math.ceil(remainingBackground.length / totalColumns) * CARD_PITCH_Y)) / CARD_PITCH_Y))
    this.storeViewLayout('all:nodes', nodes, zoneLayout, this.rackColumns, this.rackRows)
    cy.container()?.dispatchEvent(new CustomEvent('esw:layout'))
  }

  /** Layout lifecycle (v1.0.2): the deterministic rack composition is the
   * initial layout; fcose stays only for incremental churn (small batches
   * keep their anchor positions on large graphs). */
  private runLayout(kind: 'initial' | 'incremental', batch = 0): void {
    if (kind === 'initial') {
      const t0 = performance.now()
      this.layoutState = 'active'
      try {
        this.composeCurrentView(true)
        delete this.cy.container()?.dataset.composeError
      } catch (e) {
        console.error('[v1.0.2] composeRackLayout failed', e)
        ; (window as unknown as Record<string, unknown>).__esw_composeError = String(e)
        const container = this.cy.container()
        if (container) container.dataset.composeError = String(e)
        // fallback: never leave the map empty
        this.cy.layout({ name: 'cose', animate: false } as never).run()
      }
      this.layoutState = 'idle'
      perf.recordLayout(performance.now() - t0)
      // Wait until Cytoscape and the overlay have observed their final shell
      // size. The old synchronous fit was calculated against stale geometry.
      this.scheduleCamera('current')
      return
    }
    // incremental: small additions on any graph keep positions; on a large
    // graph a real batch (>= threshold) may re-run the deterministic
    // composition so the rack map stays coherent after meaningful churn
    if (batch > 0) {
      const t0 = performance.now()
      this.layoutState = 'active'
      try {
        if (this.viewLayoutKey() === 'all:nodes') this.composeRackLayout()
        else this.composeCurrentView(true)
        delete this.cy.container()?.dataset.maintenanceError
      } catch (e) {
        // Never replace a healthy long-running rack with a global COSE layout
        // because one transient snapshot/edge race failed maintenance.
        console.error('[v1.0.3] incremental rack maintenance failed', e)
        const container = this.cy.container()
        if (container) container.dataset.maintenanceError = String(e)
      }
      this.layoutState = 'idle'
      perf.recordLayout(performance.now() - t0)
    }
  }

  private scheduleIncrementalLayout(): void {
    // Throttle instead of debounce. On a busy live machine, add/remove events
    // may never pause for two seconds; a pure debounce can therefore leave a
    // fast reconnect's reconstructed graph stacked at the origin forever.
    if (this.layoutTimer !== undefined) return
    this.layoutTimer = window.setTimeout(() => {
      this.layoutTimer = undefined
      if (this.pendingNewNodes > 0) {
        const total = this.cy.nodes().length
        const large = total > LARGE_GRAPH_NODES
        const batch = this.pendingNewNodes
        const positioned = this.cy.nodes().some((element) => {
          const p = (element as NodeSingular).position()
          return Math.abs(p.x) > 1 || Math.abs(p.y) > 1
        })
        if (!positioned) {
          this.pendingNewNodes = 0
          this.runLayout('initial')
          return
        }
        const maintenanceThreshold = Math.max(24, Math.round(total * 0.03))
        if (large && batch < maintenanceThreshold && !this.hasLayoutPileup()) {
          // Keep accumulating steady small batches. The old code reset the
          // counter here, so a busy machine adding 1–3 nodes per tick could
          // run forever without ever receiving rack maintenance.
          return
        }
        this.pendingNewNodes = 0
        this.runLayout('incremental', batch)
      }
    }, 450)
  }

  /** Manual RELAYOUT: re-run the initial layout (fit + randomize). */
  relayout(): void {
    this.pendingNewNodes = 0
    this.composeCurrentView(true)
    this.scheduleCamera('current')
  }

  /** Semantic composition zones for the wire underlay (model space). */
  getZones(): ZoneInfo[] {
    return this.zoneLayout
  }

  /**
   * Manual FIT ALL (v1.0.1): true fit of the CURRENTLY VISIBLE topology —
   * never a zoom floor on top, so "fit all" can never shove content
   * off-screen. This is what the toolbar FIT button must mean.
   */
  fit(): void {
    this.composeCurrentView()
    this.scheduleCamera('current')
  }

  /** Frame the operational cross-section. FIT ALL remains the explicit way
   * to frame the complete machine inventory. */
  fitOverview(): void {
    this.scheduleCamera('overview')
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
    const geometry = this.currentGeometry()
    const mountedCards = Number(cy.container()?.querySelector('.graph-card-layer')?.getAttribute('data-mounted') ?? 0)
    return {
      totalNodes: cy.nodes().length,
      visibleNodes: vis.length,
      viewportNodes,
      totalEdges: cy.edges().length,
      visibleEdges: cy.edges(':visible').length,
      zoom: cy.zoom(),
      pan: { x: cy.pan().x, y: cy.pan().y },
      graphBoundingBox: visBB
        ? { x1: visBB.x1, y1: visBB.y1, x2: visBB.x2, y2: visBB.y2, width: visBB.w, height: visBB.h }
        : null,
      containerWidth: W,
      containerHeight: H,
      graphAspectRatio: geometry.graphAspectRatio,
      viewportAspectRatio: geometry.viewportAspectRatio,
      viewportCoverage: geometry.viewportCoverage,
      mountedCards,
      lod: this.zoomBucket,
      fitMs: Number(this.lastFitMs.toFixed(2)),
      layoutCacheHits: this.layoutCacheHits,
      layoutCacheMisses: this.layoutCacheMisses,
      layoutState: this.layoutState,
    }
  }

  /**
   * v1.0.2 topology composition diagnostics (acceptance AG + debugging).
   * Objective, truthful connectedness/structure metrics computed from the
   * REAL rendered graph — never synthetic. Counts nodes/edges, connected
   * components (union-find over real edges), and the composition shape.
   */
  topologyMetrics(): Record<string, unknown> {
    const cy = this.cy
    const nodes = cy.nodes()
    const edges = cy.edges()
    const nn = nodes.length
    const ee = edges.length
    const W = cy.width()
    const H = cy.height()
    // union-find over real edges
    const parent = new Map<string, string>()
    const compSize = new Map<string, number>()
    const find = (x: string): string => {
      let r = x
      while (parent.get(r) !== r) r = parent.get(r)!
      return r
    }
    const union = (a: string, b: string): void => {
      const ra = find(a)
      const rb = find(b)
      if (ra === rb) return
      if ((compSize.get(ra) ?? 1) < (compSize.get(rb) ?? 1)) {
        parent.set(ra, rb); compSize.set(rb, (compSize.get(ra) ?? 1) + (compSize.get(rb) ?? 1))
      } else {
        parent.set(rb, ra); compSize.set(ra, (compSize.get(ra) ?? 1) + (compSize.get(rb) ?? 1))
      }
    }
    nodes.forEach((nd) => { parent.set(nd.id(), nd.id()); compSize.set(nd.id(), 1) })
    edges.forEach((e) => union(e.source().id(), e.target().id()))
    // node metrics
    const degree = new Map<string, number>()
    edges.forEach((e) => {
      degree.set(e.source().id(), (degree.get(e.source().id()) ?? 0) + 1)
      degree.set(e.target().id(), (degree.get(e.target().id()) ?? 0) + 1)
    })
    let connectedNodes = 0
    let viewportNodes = 0
    let viewportEdges = 0
    let viewportConnected = 0
    nodes.forEach((nd) => {
      const p = nd.renderedPosition()
      if (p.x >= 0 && p.x <= W && p.y >= 0 && p.y <= H) {
        viewportNodes += 1
        if ((degree.get(nd.id()) ?? 0) > 0) viewportConnected += 1
      }
      if ((degree.get(nd.id()) ?? 0) > 0) connectedNodes += 1
    })
    edges.forEach((e) => {
      const a = e.source().renderedPosition()
      const b = e.target().renderedPosition()
      if (a.x >= 0 && a.x <= W && a.y >= 0 && a.y <= H &&
        b.x >= 0 && b.x <= W && b.y >= 0 && b.y <= H) viewportEdges += 1
    })
    let largest = 0
    for (const sz of compSize.values()) if (sz > largest) largest = sz
    const svcBanked = cy.nodes('.svc-bank').length
    const orphanBanked = cy.nodes('.orphan-bank').length
    const coreClass = cy.nodes('.core-node').length
    const visibleNodes = cy.nodes(':visible').length
    const visibleEdges = cy.edges(':visible').length
    const geometry = this.currentGeometry()
    return {
      totalNodes: nn,
      totalEdges: ee,
      visibleNodes,
      visibleEdges,
      viewportNodes,
      viewportEdges,
      connectedNodes,
      connectedFraction: nn > 0 ? Number((connectedNodes / nn).toFixed(3)) : 0,
      viewportConnectedFraction: viewportNodes > 0 ? Number((viewportConnected / viewportNodes).toFixed(3)) : 0,
      orphanNodes: nn - connectedNodes,
      orphanFraction: nn > 0 ? Number(((nn - connectedNodes) / nn).toFixed(3)) : 0,
      largestComponentSize: largest,
      largestComponentFraction: nn > 0 ? Number((largest / nn).toFixed(3)) : 0,
      serviceBanked: svcBanked,
      orphanBanked,
      coreClassNodes: coreClass,
      rackColumns: this.rackColumns,
      rackRows: this.rackRows,
      graphAspectRatio: geometry.graphAspectRatio,
      viewportAspectRatio: geometry.viewportAspectRatio,
      viewportCoverage: geometry.viewportCoverage,
      mountedCards: Number(cy.container()?.querySelector('.graph-card-layer')?.getAttribute('data-mounted') ?? 0),
      layoutCacheHits: this.layoutCacheHits,
      layoutCacheMisses: this.layoutCacheMisses,
      fitMs: Number(this.lastFitMs.toFixed(2)),
      zoom: cy.zoom(),
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
    this.composeCurrentView()
    this.scheduleCamera('current')
    perf.recordFilter(performance.now() - t0)
  }

  private applyFilter(): void {
    const f = this.filter
    const nodes = this.cy.nodes()
    const edges = this.cy.edges()
    // Remove only the display bypass used by the previous filtered/family
    // projection. Other LOD styling remains stylesheet/canvas-driven.
    nodes.removeStyle('display')
    edges.removeStyle('display')
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
    // Explicit display bypasses make visibility deterministic across
    // Cytoscape renderer/style-cache versions. They are removed above on the
    // next filter pass, so hidden inventory is never deleted or stranded.
    this.cy.nodes('.fam-hidden').style('display', 'none')
    this.cy.edges('.fam-hidden').style('display', 'none')
    nodes.filter('[?hidden]').style('display', 'none')
    edges.filter('[?hidden]').style('display', 'none')
    this.refreshCardOverlay()
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
    this.refreshCardOverlay()
  }

  private refreshCardOverlay(): void {
    this.cy.container()?.dispatchEvent(new CustomEvent('esw:cards-refresh'))
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
    if (this.layoutTimer !== undefined) {
      window.clearTimeout(this.layoutTimer)
      this.layoutTimer = undefined
    }
    if (this.familySyncTimer !== undefined) {
      window.clearTimeout(this.familySyncTimer)
      this.familySyncTimer = undefined
    }
    if (this.activityDecayTimer !== undefined) {
      clearInterval(this.activityDecayTimer)
      this.activityDecayTimer = undefined
    }
    if (this.cameraRaf) cancelAnimationFrame(this.cameraRaf)
    if (this.cameraRaf2) cancelAnimationFrame(this.cameraRaf2)
    if (this.interactionTimer !== undefined) window.clearTimeout(this.interactionTimer)
    this.cy.off('pan zoom drag', this.markInteraction)
    this.cy.off('free', this.markInteraction)
    this.resizeObs?.disconnect()
    this.resizeObs = undefined
    this.selBoxCleanup?.()
    this.overlay.destroy()
  }
}
