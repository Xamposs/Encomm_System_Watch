import type { ConnectionStatus, SemanticSummary, Stats, TelemetryInfo } from '../types/system'
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
  if (t.level === 'TIER2') {
    // A started ETW session is not the same as usable per-edge bytes.
    if (t.readiness === 'DEGRADED') return { text: 'TRAFFIC: DEGRADED', cls: 'tel-t0' }
    if (t.readiness === 'INITIALIZING') return { text: 'TRAFFIC: PER-EDGE (STARTING)', cls: 'tel-t2' }
    return { text: 'TRAFFIC: PER-EDGE', cls: 'tel-t2' }
  }
  return { text: 'TRAFFIC: SOCKET EVENTS', cls: 'tel-t0' }
}

/** Compact AI status row — only categories actually detected appear. */
export function AiSummary({ semantic }: { semantic?: SemanticSummary }) {
  if (!semantic) return null
  const chips: { text: string; cls: string; title: string }[] = []
  if (semantic.hermes) chips.push({ text: 'HERMES ●', cls: 'ai-hermes', title: 'Hermes agent running' })
  if (semantic.lm_studio) chips.push({ text: 'LM STUDIO ●', cls: 'ai-lm', title: 'LM Studio running' })
  for (const m of semantic.models) {
    chips.push({
      text: m.state === 'LOADED' ? `MODEL ● ${m.id}` : `MODEL ${m.id}`,
      cls: m.state === 'LOADED' ? 'ai-model-loaded' : 'ai-model',
      title: `${m.id} (${m.state})`,
    })
  }
  if (semantic.mcp.length) {
    chips.push({ text: `MCP ${semantic.mcp.length}`, cls: 'ai-mcp', title: semantic.mcp.join(', ') })
  }
  for (const g of semantic.gpu) {
    const util = typeof g.utilization_percent === 'number' ? `${Math.round(g.utilization_percent)}%` : '—'
    const vram =
      typeof g.vram_used_mb === 'number' && typeof g.vram_total_mb === 'number'
        ? `${(g.vram_used_mb / 1024).toFixed(1)}/${(g.vram_total_mb / 1024).toFixed(1)} GB`
        : null
    chips.push({
      text: `GPU ${vram ? `${util} · ${vram}` : util}`,
      cls: 'ai-gpu',
      title: `${g.name ?? 'GPU'} · ${util}${typeof g.temperature_c === 'number' ? ` · ${g.temperature_c}°C` : ''}`,
    })
  }
  if (chips.length === 0) return null
  return (
    <div className="ai-summary" title="AI semantic summary — only real detections">
      {chips.map((c, i) => (
        <span key={i} className={`ai-chip ${c.cls}`} title={c.title}>
          {c.text}
        </span>
      ))}
    </div>
  )
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

      <AiSummary semantic={stats?.semantic} />

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
