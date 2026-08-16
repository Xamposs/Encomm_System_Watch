export type NodeKind = 'PROCESS' | 'SYSTEM' | 'EXTERNAL_ENDPOINT' | 'LISTENING_PORT' | 'LOCAL_ENDPOINT'
export type EdgeKind = 'LOCALHOST' | 'EXTERNAL' | 'LISTEN'
export type EventType =
  | 'PROCESS_STARTED'
  | 'PROCESS_STOPPED'
  | 'CONNECTION_OPENED'
  | 'CONNECTION_CLOSED'
  | 'PROCESS_METRICS_UPDATED'
export type Filter = 'all' | 'active' | 'listening' | 'highcpu'
export type ConnectionStatus = 'connecting' | 'live' | 'disconnected'

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

export interface Stats {
  processes: number
  active_conns: number
  listening: number
  cpu_percent: number
  mem_percent: number
  ts: number
  mode?: 'live' | 'demo'
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

export type ServerMessage =
  | { type: 'snapshot'; mode: 'live' | 'demo'; ts: number; stats: Stats; nodes: TopoNode[]; edges: TopoEdge[] }
  | { type: 'events'; data: SystemEvent[] }
  | { type: 'stats'; data: Stats }
  | { type: 'ping'; ts: number }
