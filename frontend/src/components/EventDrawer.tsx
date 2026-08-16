import { useEffect, useRef, useState } from 'react'
import type { EventType, SystemEvent } from '../types/system'
import { fmtBps } from '../graph/GraphController'

interface Props {
  open: boolean
  onToggle: () => void
  events: SystemEvent[]
}

const TYPE_LABEL: Record<EventType, string> = {
  PROCESS_STARTED: 'PROCESS STARTED',
  PROCESS_STOPPED: 'PROCESS STOPPED',
  CONNECTION_OPENED: 'CONNECTION OPENED',
  CONNECTION_CLOSED: 'CONNECTION CLOSED',
  PROCESS_METRICS_UPDATED: 'METRICS',
  TRAFFIC_BURST: 'TRAFFIC BURST',
  TELEMETRY_CAPABILITY_CHANGED: 'TELEMETRY',
}

const TYPE_CLASS: Record<EventType, string> = {
  PROCESS_STARTED: 'ev-start',
  PROCESS_STOPPED: 'ev-stop',
  CONNECTION_OPENED: 'ev-open',
  CONNECTION_CLOSED: 'ev-close',
  PROCESS_METRICS_UPDATED: 'ev-metrics',
  TRAFFIC_BURST: 'ev-burst',
  TELEMETRY_CAPABILITY_CHANGED: 'ev-telemetry',
}

function describe(ev: SystemEvent): string {
  const m = ev.metadata ?? {}
  switch (ev.event_type) {
    case 'PROCESS_STARTED':
    case 'PROCESS_STOPPED':
      return `${m.name ?? '?'} PID ${m.pid ?? '?'}`
    case 'CONNECTION_OPENED':
    case 'CONNECTION_CLOSED':
      return `${m.src_label ?? '?'} → ${m.tgt_label ?? '?'} ${m.proto ?? ''}:${m.edge_port ?? '?'}`
    case 'PROCESS_METRICS_UPDATED':
      return `${m.name ?? '?'} CPU ${Number(m.cpu_percent ?? 0).toFixed(1)}% · ${Math.round(Number(m.memory_mb ?? 0))}MB`
    case 'TRAFFIC_BURST':
      return `${m.src_label ?? '?'} → ${m.tgt_label ?? '?'} ${fmtBps(Number(m.rate_bps ?? 0))} (${Math.round(Number(m.bytes ?? 0) / 1024)} KB in ${m.window_ms ?? '?'} ms)`
    case 'TELEMETRY_CAPABILITY_CHANGED':
      return `${m.level ?? '?'} · ${m.source ?? '?'}${m.elevation_required ? ' · ELEVATION REQUIRED' : ''}`
  }
}

export function EventDrawer({ open, onToggle, events }: Props) {
  const listRef = useRef<HTMLDivElement>(null)
  const [stickToBottom, setStickToBottom] = useState(true)

  useEffect(() => {
    const el = listRef.current
    if (el && stickToBottom) el.scrollTop = el.scrollHeight
  }, [events, stickToBottom])

  const onScroll = () => {
    const el = listRef.current
    if (!el) return
    setStickToBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 48)
  }

  return (
    <section className={`drawer ${open ? 'open' : ''}`}>
      <button className="drawer-toggle" onClick={onToggle}>
        <span className="drawer-arrow">{open ? '▾' : '▸'}</span>
        LIVE EVENT DRAWER
        <span className="drawer-count">{events.length}</span>
      </button>
      <div className="drawer-body">
        <div className="event-list" ref={listRef} onScroll={onScroll}>
          {events.map((ev) => (
            <div key={ev.event_id} className={`event-row ${TYPE_CLASS[ev.event_type]}`}>
              <span className="ev-time">{ev.timestamp.slice(11, 23)}</span>
              <span className="ev-type">{TYPE_LABEL[ev.event_type]}</span>
              <span className="ev-desc">{describe(ev)}</span>
            </div>
          ))}
          {events.length === 0 && <div className="event-empty">awaiting events…</div>}
        </div>
      </div>
    </section>
  )
}
