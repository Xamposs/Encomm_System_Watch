export type NodeKind =
  | 'PROCESS'
  | 'SYSTEM'
  | 'EXTERNAL_ENDPOINT'
  | 'LISTENING_PORT'
  | 'LOCAL_ENDPOINT'
  | 'SEMANTIC'
  | 'LOCAL_LLM'
  | 'GPU'
export type EdgeKind =
  | 'LOCALHOST'
  | 'EXTERNAL'
  | 'LISTEN'
  | 'USES_GPU'
  | 'SERVES_MODEL'
  | 'LOCAL_API'
  | 'PROCESS_PARENT'
  | 'SPAWNED'
  | 'HOSTS'
  | 'MEMBER_OF'
export type EventType =
  | 'PROCESS_STARTED'
  | 'PROCESS_STOPPED'
  | 'CONNECTION_OPENED'
  | 'CONNECTION_CLOSED'
  | 'PROCESS_METRICS_UPDATED'
  | 'TRAFFIC_BURST'
  | 'TELEMETRY_CAPABILITY_CHANGED'
  | 'HERMES_DETECTED'
  | 'LM_STUDIO_DETECTED'
  | 'MCP_SERVER_DETECTED'
  | 'SEMANTIC_DETECTED'
  | 'SEMANTIC_LOST'
  | 'MODEL_LOADED'
  | 'MODEL_AVAILABLE'
  | 'GPU_PROCESS_ATTACHED'
  | 'GPU_PROCESS_DETACHED'
  | 'ETW_HEALTH'
export type Filter = 'all' | 'active' | 'listening' | 'highcpu'
export type ConnectionStatus = 'connecting' | 'live' | 'disconnected'
export type ViewMode = 'nodes' | 'families'
export type SemanticView = 'system' | 'ai'
export type Mode = 'live' | 'demo' | 'benchmark'

export interface TopoNode {
  id: string
  kind: NodeKind
  label: string
  data: Record<string, unknown>
}

export interface TopoEdge {
  id: string
  source: string
  target: string
  kind: EdgeKind
  proto: string
  ports: number[]
  active: boolean
  directed: boolean
}

export interface TelemetryInfo {
  level: 'TIER2' | 'TIER0'
  source: string
  detail: string
  elevation_required: boolean
  enabled: boolean
  readiness?: 'NONE' | 'INITIALIZING' | 'ACTIVE' | 'DEGRADED'
}

export interface NetStats {
  down_bps: number
  up_bps: number
  source: 'CAPTURED' | 'ADAPTER_TOTALS'
  adapter_down_bps: number
  adapter_up_bps: number
}

export interface GpuProcessInfo {
  pid: number
  vram_mb?: number
}

export interface GpuInfo {
  index: number
  name?: string
  utilization_percent?: number
  vram_used_mb?: number
  vram_total_mb?: number
  temperature_c?: number
  power_w?: number
  driver?: string
  fan_percent?: number
  clock_graphics_mhz?: number
  clock_memory_mhz?: number
  processes?: GpuProcessInfo[]
}

export interface SemanticSummary {
  hermes: boolean
  lm_studio: boolean
  models: { id: string; state: string }[]
  mcp: string[]
  gpu: GpuInfo[]
}

export interface Stats {
  processes: number
  active_conns: number
  listening: number
  cpu_percent: number
  mem_percent: number
  ts: number
  mode?: Mode
  telemetry?: TelemetryInfo
  net?: NetStats | null
  gpu?: GpuInfo[]
  semantic?: SemanticSummary
}

/** TEST-ONLY benchmark snapshot metadata (only present in benchmark mode). */
export interface BenchmarkMeta {
  active: boolean
  label: string
  node_count: number
  edge_count: number
  seed: number
}

export interface SystemEvent {
  event_id: string
  event_type: EventType
  source: string
  target: string | null
  timestamp: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  metadata: Record<string, any>
}

/** One aggregated per-edge activity sample (200 ms window). */
export interface NetworkActivityItem {
  edge_id: string
  fwd_bytes: number
  rev_bytes: number
  duration_ms: number
  fwd_bps: number
  rev_bps: number
  level: number // 0 idle, 1 low, 2 medium, 3 high
  last_activity: number
}

/** Per-process aggregate activity sample. */
export interface NetworkActivityNode {
  sid: string
  down_bps: number
  up_bps: number
  last_activity: number
}

export type ServerMessage =
  | {
      type: 'snapshot'
      mode: Mode
      ts: number
      stats: Stats
      telemetry?: TelemetryInfo
      nodes: TopoNode[]
      edges: TopoEdge[]
      gpu?: GpuInfo[]
      semantic?: SemanticSummary
      benchmark?: BenchmarkMeta
    }
  | { type: 'events'; data: SystemEvent[] }
  | { type: 'stats'; data: Stats }
  | {
      type: 'network_activity'
      window_ms: number
      ts: number
      items: NetworkActivityItem[]
      nodes: NetworkActivityNode[]
    }
  | { type: 'gpu'; data: GpuInfo[]; ts: number }
  | { type: 'ping'; ts: number }
