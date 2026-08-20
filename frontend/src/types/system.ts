export type NodeKind =
  | 'PROCESS'
  | 'SYSTEM'
  | 'EXTERNAL_ENDPOINT'
  | 'LISTENING_PORT'
  | 'LOCAL_ENDPOINT'
  | 'SEMANTIC'
  | 'LOCAL_LLM'
  | 'GPU'
  // ---- infrastructure observability (v0.4.0) ----
  | 'SERVICE'
  | 'WSL'
  | 'DOCKER_ENGINE'
  | 'CONTAINER'
  | 'DOCKER_NETWORK'
  | 'VM'
  // ---- transient AI runtime nodes (v0.5.0) ----
  | 'AI_RUNTIME'
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
  // ---- infrastructure relationships (v0.4.0) ----
  | 'HOSTED_BY'
  | 'EXPOSES'
  | 'CONNECTED_TO'
  | 'BACKED_BY'
  // ---- application-level AI relationships (v0.5.0) ----
  | 'AI_CALL'
export type AiEventType =
  | 'AGENT_RUN_STARTED'
  | 'AGENT_RUN_FINISHED'
  | 'MODEL_REQUEST_STARTED'
  | 'MODEL_REQUEST_FINISHED'
  | 'TOOL_CALL_STARTED'
  | 'TOOL_CALL_FINISHED'
  | 'MCP_CALL_STARTED'
  | 'MCP_CALL_FINISHED'
  | 'AGENT_MESSAGE'
  | 'RETRY'
  | 'AI_ERROR'
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
  // ---- infrastructure events (v0.4.0, change-only) ----
  | 'SERVICE_STARTED'
  | 'SERVICE_STOPPED'
  | 'SERVICE_STATUS_CHANGED'
  | 'CONTAINER_STARTED'
  | 'CONTAINER_STOPPED'
  | 'CONTAINER_CREATED'
  | 'CONTAINER_REMOVED'
  | 'WSL_STATE_CHANGED'
  | 'VM_DETECTED'
  | 'VM_LOST'
  | 'VM_STATE_CHANGED'
  // ---- application-level AI telemetry (v0.5.0) ----
  | AiEventType
export type Filter = 'all' | 'active' | 'listening' | 'highcpu'
export type ConnectionStatus = 'connecting' | 'live' | 'disconnected'
export type ViewMode = 'nodes' | 'families'
export type SemanticView = 'system' | 'ai' | 'infra'
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

/** Compact infrastructure summary for header chips (v0.4.0). */
export interface InfraSummary {
  services: { total: number; running: number; stopped: number }
  wsl: { distributions: number; running: number }
  docker: {
    available: boolean
    engine: string
    containers: number
    running: number
  }
  vms: { total: number; running: number; providers: string[] }
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
  infra?: InfraSummary
}

/** TEST-ONLY benchmark snapshot metadata (only present in benchmark mode). */
export interface BenchmarkMeta {
  active: boolean
  label: string
  node_count: number
  edge_count: number
  seed: number
}

// ---- application-level AI telemetry (v0.5.0) --------------------------------

/** One normalized application-level AI telemetry event (metadata only). */
export interface AiTelemetryEvent {
  event_id: string
  timestamp: string
  source: string
  event_type: AiEventType
  test_only?: boolean
  agent_id?: string
  agent_name?: string
  model_id?: string
  tool_name?: string
  trace_id?: string
  span_id?: string
  parent_span_id?: string
  status?: string
  duration_ms?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  tps?: number
  context_tokens?: number
  metadata?: Record<string, unknown>
  /** Graph placement hint computed by the backend buffer (never guessed). */
  runtime?: {
    node_id: string
    parent_node_id: string | null
    kind: string
    label?: string
    test_only?: boolean
    finished?: boolean
  }
}

export type AiProviderStateName = 'ACTIVE' | 'AVAILABLE_NO_DATA' | 'UNAVAILABLE' | 'DEGRADED'

/** Provider status (section 26): ACTIVE / AVAILABLE_NO_DATA / UNAVAILABLE /
 * DEGRADED with the truthful per-metric availability matrix. */
export interface AiProviderState {
  name: string
  kind: string
  state: AiProviderStateName
  detail: string
  test_only: boolean
  availability: Record<string, boolean>
  last_poll?: number
  last_error?: string | null
}

/** Compact AI metrics — only fields with real values are present. */
export interface AiMetrics {
  runs: number
  tokens_per_s?: number
  tools?: number
  model?: string
  errors?: number
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
      infra?: InfraSummary
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
  // ---- application-level AI telemetry (v0.5.0) ----
  | { type: 'ai_activity'; ts: number; fixture: boolean; events: AiTelemetryEvent[] }
  | { type: 'ai_metrics'; ts: number; fixture: boolean; metrics: AiMetrics }
  | {
      type: 'ai_provider_status'
      ts: number
      fixture: boolean
      providers: Record<string, AiProviderState>
    }
  | { type: 'ping'; ts: number }
