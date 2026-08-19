export type NodeKind = 'PROCESS' | 'SYSTEM' | 'EXTERNAL_ENDPOINT' | 'LISTENING_PORT' | 'LOCAL_ENDPOINT'
export type EdgeKind = 'LOCALHOST' | 'EXTERNAL' | 'LISTEN'
export type EventType =
  | 'PROCESS_STARTED'
  | 'PROCESS_STOPPED'
  | 'CONNECTION_OPENED'
  | 'CONNECTION_CLOSED'
  | 'PROCESS_METRICS_UPDATED'
  | 'TRAFFIC_BURST'
  | 'TELEMETRY_CAPABILITY_CHANGED'
export type Filter = 'all' | 'active' | 'listening' | 'highcpu'
export type ConnectionStatus = 'connecting' | 'live' | 'disconnected'
export type ViewMode = 'nodes' | 'families'

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

export interface Stats {
  processes: number
  active_conns: number
  listening: number
  cpu_percent: number
  mem_percent: number
  ts: number
  mode?: 'live' | 'demo'
  telemetry?: TelemetryInfo
  net?: NetStats | null
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
      mode: 'live' | 'demo'
      ts: number
      stats: Stats
      telemetry?: TelemetryInfo
      nodes: TopoNode[]
      edges: TopoEdge[]
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
  | { type: 'ping'; ts: number }
