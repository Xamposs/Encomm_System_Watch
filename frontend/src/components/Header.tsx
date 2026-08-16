import type { ConnectionStatus, Stats, TelemetryInfo } from '../types/system'
import { fmtBps } from '../graph/GraphController'

interface Props {
  status: ConnectionStatus
  mode: 'live' | 'demo'
  stats: Stats | null
  feedTs: number | null
  telemetry: TelemetryInfo | null
}

function telemetryLabel(t: TelemetryInfo | null): { text: string; cls: string } {
  if (!t || !t.enabled) return { text: 'TRAFFIC: DISABLED', cls: 'tel-off' }
  if (t.level === 'TIER2') return { text: 'TRAFFIC: PER-EDGE', cls: 'tel-t2' }
  return { text: 'TRAFFIC: SOCKET EVENTS', cls: 'tel-t0' }
}

export function Header({ status, mode, stats, feedTs, telemetry }: Props) {
  const feed = feedTs
    ? new Date(feedTs * 1000).toLocaleTimeString('en-GB', { hour12: false })
    : '--:--:--'
  const connLabel =
    status === 'live' ? '● LIVE' : status === 'connecting' ? '● CONNECTING' : '● DISCONNECTED'

  const net = stats?.net
  const tel = telemetryLabel(telemetry)

  return (
    <header className="header">
      <div className="brand">
        <div className="brand-name">
          ENCOMM <span>SYSTEM WATCH</span>
        </div>
        <div className="brand-sub">LIVE READ-ONLY SYSTEM MAP</div>
      </div>

      <div className="header-right">
        {mode === 'demo' && <div className="demo-badge">DEMO MODE</div>}
        <div className={`conn-label ${status}`}>{connLabel}</div>
        <div className="stat-block">
          <div className="stat-line">
            <span className="stat-num">{stats?.processes ?? '—'}</span> PROCESSES
          </div>
          <div className="stat-line">
            <span className="stat-num">{stats?.active_conns ?? '—'}</span> ACTIVE CONNECTIONS
          </div>
          <div className="stat-line">
            <span className="stat-num">{stats?.listening ?? '—'}</span> LISTENING PORTS
          </div>
        </div>
        <div className="stat-block meta">
          <div className="stat-line">
            CPU <span className="stat-num">{stats ? `${Math.round(stats.cpu_percent)}%` : '—'}</span>
          </div>
          <div className="stat-line">
            RAM <span className="stat-num">{stats ? `${Math.round(stats.mem_percent)}%` : '—'}</span>
          </div>
          <div className="stat-line">
            FEED <span className="stat-num">{feed}</span>
          </div>
        </div>
        {/* bandwidth: only when real numbers exist; the source is explicit */}
        {net && (
          <div className="stat-block net" title={
            net.source === 'CAPTURED'
              ? 'Sum of captured per-connection telemetry (adapter totals in parentheses)'
              : 'System adapter totals (sum of all physical interfaces)'
          }>
            <div className="stat-line">
              NET <span className="stat-num">↓ {fmtBps(net.down_bps)}</span>
            </div>
            <div className="stat-line">
              <span className="stat-num">↑ {fmtBps(net.up_bps)}</span>{' '}
              <span className="net-source">{net.source === 'CAPTURED' ? 'CAPTURED' : 'ADAPTER'}</span>
            </div>
          </div>
        )}
        <div className={`tel-chip ${tel.cls}`} title={telemetry?.detail ?? ''}>
          {tel.text}
          {telemetry?.elevation_required && <span className="tel-elev"> · ELEVATION REQUIRED</span>}
        </div>
      </div>
    </header>
  )
}
