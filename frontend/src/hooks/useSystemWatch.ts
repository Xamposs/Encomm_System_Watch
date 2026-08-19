import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  ConnectionStatus,
  Stats,
  SystemEvent,
  TelemetryInfo,
} from '../types/system'
import { WatchSocket } from '../services/ws'
import type { GraphController } from '../graph/GraphController'

const EVENT_BUFFER_LIMIT = 800

export function useSystemWatch() {
  const [status, setStatus] = useState<ConnectionStatus>('connecting')
  const [mode, setMode] = useState<'live' | 'demo'>('live')
  const [ready, setReady] = useState(false)
  const [stats, setStats] = useState<Stats | null>(null)
  const [feedTs, setFeedTs] = useState<number | null>(null)
  const [events, setEvents] = useState<SystemEvent[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [telemetry, setTelemetry] = useState<TelemetryInfo | null>(null)
  const eventsRef = useRef<SystemEvent[]>([])
  const controllerRef = useRef<GraphController | null>(null)

  useEffect(() => {
    const socket = new WatchSocket((msg) => {
      switch (msg.type) {
        case 'snapshot':
          setMode(msg.mode)
          setStats(msg.stats)
          setFeedTs(msg.ts)
          setReady(true)
          if (msg.telemetry) {
            setTelemetry(msg.telemetry)
            controllerRef.current?.setTelemetry(msg.telemetry)
          }
          if (msg.gpu?.length) {
            controllerRef.current?.applyGpu(msg.gpu)
          }
          controllerRef.current?.replaceAll(msg.nodes, msg.edges)
          break
        case 'events': {
          const list = msg.data
          for (const ev of list) {
            controllerRef.current?.applyEvent(ev)
            if (ev.event_type === 'TELEMETRY_CAPABILITY_CHANGED') {
              const t = ev.metadata as unknown as TelemetryInfo
              setTelemetry(t)
              controllerRef.current?.setTelemetry(t)
            }
          }
          if (list.length) {
            eventsRef.current = [
              ...eventsRef.current.slice(-(EVENT_BUFFER_LIMIT - list.length)),
              ...list,
            ]
            setEvents(eventsRef.current)
          }
          break
        }
        case 'stats':
          setStats(msg.data)
          setFeedTs(msg.data.ts)
          if (msg.data.telemetry) {
            setTelemetry(msg.data.telemetry)
            controllerRef.current?.setTelemetry(msg.data.telemetry)
          }
          break
        case 'network_activity':
          controllerRef.current?.applyActivity(msg.items, msg.nodes)
          break
        case 'gpu':
          controllerRef.current?.applyGpu(msg.data)
          break
      }
    }, setStatus)
    socket.connect()
    return () => socket.close()
  }, [])

  const selectNode = useCallback((id: string | null) => {
    setSelectedId(id)
    setSelected(id ? (controllerRef.current?.getNodeData(id) ?? null) : null)
    controllerRef.current?.select(id)
  }, [])

  // keep the inspector fresh while metrics stream in
  useEffect(() => {
    if (!selectedId) return
    const t = window.setInterval(() => {
      setSelected(controllerRef.current?.getNodeData(selectedId) ?? null)
    }, 2000)
    return () => window.clearInterval(t)
  }, [selectedId])

  return {
    status,
    mode,
    ready,
    stats,
    feedTs,
    events,
    selectedId,
    selected,
    selectNode,
    drawerOpen,
    setDrawerOpen,
    telemetry,
    controllerRef,
  }
}
