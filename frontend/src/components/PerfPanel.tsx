import { useEffect, useState } from 'react'
import { perf, type PerfSnapshot } from '../graph/PerfMonitor'

interface Props {
  /** Only rendered (and auto-shown) while benchmark mode is active. */
  mode: 'live' | 'demo' | 'benchmark'
}

function ms(v: number | null | undefined, digits = 1): string {
  return typeof v === 'number' ? `${v.toFixed(digits)} ms` : '—'
}

/**
 * Development/test diagnostics overlay — shown ONLY in benchmark mode (or
 * when forced via `window.__esw_perf.show()`). Never part of the normal
 * production UI. Every value is a measured counter; nothing here fakes data.
 */
export function PerfPanel({ mode }: Props) {
  const [snap, setSnap] = useState<PerfSnapshot | null>(null)
  const [forced, setForced] = useState(false)

  useEffect(() => {
    const show = (): void => setForced(true)
    ;(window as unknown as Record<string, unknown>).__esw_perfShow = show
    const t = window.setInterval(() => setSnap(perf.snapshot()), 1000)
    return () => {
      window.clearInterval(t)
      delete (window as unknown as Record<string, unknown>).__esw_perfShow
    }
  }, [])

  if (mode !== 'benchmark' && !forced) return null
  if (!snap) return null

  const ws = Object.entries(snap.wsMs).sort((a, b) => b[1].count - a[1].count)
  return (
    <div className="perf-panel" title="DEVELOPMENT DIAGNOSTICS — measured values only">
      <div className="perf-title">
        PERF DIAGNOSTICS <span className="perf-test">TEST/BENCHMARK</span>
      </div>
      <div className="perf-row">
        <span>nodes</span><b>{snap.nodes}</b>
        <span>visible</span><b>{snap.visibleNodes}</b>
      </div>
      <div className="perf-row">
        <span>edges</span><b>{snap.edges}</b>
        <span>visible</span><b>{snap.visibleEdges}</b>
      </div>
      <div className="perf-row">
        <span>update</span><b>{ms(snap.updateMs.last)}</b>
        <span>max</span><b>{ms(snap.updateMs.max)}</b>
      </div>
      <div className="perf-row">
        <span>layout</span><b>{ms(snap.layoutMs.last)}</b>
        <span>max</span><b>{ms(snap.layoutMs.max)}</b>
      </div>
      <div className="perf-row">
        <span>activity batch</span><b>{ms(snap.activityBatchMs.last)}</b>
      </div>
      <div className="perf-row">
        <span>AI toggle</span><b>{ms(snap.aiToggleMs)}</b>
        <span>search</span><b>{ms(snap.searchMs)}</b>
      </div>
      <div className="perf-row">
        <span>filter</span><b>{ms(snap.filterMs)}</b>
      </div>
      <div className="perf-row">
        <span>particles</span><b>{snap.particles}</b>
        <span>overlay act</span><b>{snap.overlayActivity}</b>
      </div>
      <div className="perf-row">
        <span>overlay rAF</span><b>{snap.overlayRunning ? 'RUNNING' : 'stopped'}</b>
        <span>fps</span><b>{snap.fps ?? '—'}</b>
      </div>
      <div className="perf-row">
        <span>heap</span>
        <b>{snap.heapMB !== null ? `${snap.heapMB.toFixed(0)} MB` : '—'}</b>
        <span>samples</span><b>{snap.heapSamples}</b>
      </div>
      {ws.length > 0 && (
        <div className="perf-ws">
          {ws.slice(0, 4).map(([k, v]) => (
            <div className="perf-row" key={k}>
              <span>ws {k}</span><b>{ms(v.last)}</b><span className="perf-dim">×{v.count}</span>
            </div>
          ))}
        </div>
      )}
      <div className="perf-note">MEASURED · NOT REAL TELEMETRY</div>
    </div>
  )
}
