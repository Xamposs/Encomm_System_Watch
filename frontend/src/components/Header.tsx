import type { ConnectionStatus, Stats } from '../types/system'

interface Props {
  status: ConnectionStatus
  mode: 'live' | 'demo'
  stats: Stats | null
  feedTs: number | null
}

export function Header({ status, mode, stats, feedTs }: Props) {
  const feed = feedTs
    ? new Date(feedTs * 1000).toLocaleTimeString('en-GB', { hour12: false })
    : '--:--:--'
  const connLabel =
    status === 'live' ? '● LIVE' : status === 'connecting' ? '● CONNECTING' : '● DISCONNECTED'

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
      </div>
    </header>
  )
}
