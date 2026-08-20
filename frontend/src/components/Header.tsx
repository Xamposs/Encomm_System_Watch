import type {
  AiMetrics,
  AiProviderState,
  ConnectionStatus,
  InfraSummary,
  Mode,
  SemanticSummary,
  Stats,
  TelemetryInfo,
} from '../types/system'
import { fmtBps } from '../graph/GraphController'

interface Props {
  status: ConnectionStatus
  mode: Mode
  stats: Stats | null
  feedTs: number | null
  telemetry: TelemetryInfo | null
  aiMetrics?: AiMetrics | null
  aiProviders?: Record<string, AiProviderState> | null
  aiFixture?: boolean
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

/** Compact AI telemetry chips — application-level data only (v0.5.0).
 * Nothing renders when no provider is active: no real application
 * telemetry means no clutter. TEST/FIXTURE mode is labeled explicitly. */
export function AiTelemetryChips({
  metrics,
  providers,
  fixture,
}: {
  metrics: AiMetrics | null
  providers: Record<string, AiProviderState> | null
  fixture: boolean
}) {
  const chips: { text: string; cls: string; title: string }[] = []
  if (fixture) {
    chips.push({
      text: 'AI TELEMETRY TEST DATA',
      cls: 'ai-tel-fixture',
      title: 'TEST/FIXTURE/SYNTHETIC provider active — never real telemetry',
    })
  }
  // provider state chip: only when an interface is actually present
  const hermes = providers?.hermes
  if (hermes && hermes.state !== 'UNAVAILABLE') {
    const label =
      hermes.state === 'ACTIVE' ? 'AI TELEMETRY ● ACTIVE'
        : hermes.state === 'DEGRADED' ? 'AI TELEMETRY ● DEGRADED'
          : 'AI TELEMETRY ● IDLE'
    chips.push({
      text: label,
      cls: hermes.state === 'DEGRADED' ? 'ai-tel-degraded' : 'ai-tel-active',
      title: hermes.detail,
    })
  }
  if (metrics && metrics.runs > 0) {
    chips.push({ text: `RUNS ${metrics.runs}`, cls: 'ai-tel-run', title: 'active agent runs' })
  }
  if (metrics && typeof metrics.tokens_per_s === 'number') {
    chips.push({
      text: `TOKENS/s ${metrics.tokens_per_s}`,
      cls: 'ai-tel-tokens',
      title: 'real token metrics (last 60 s)',
    })
  }
  if (metrics && typeof metrics.tools === 'number') {
    chips.push({ text: `TOOLS ${metrics.tools}`, cls: 'ai-tel-tools', title: 'tools observed in active runs' })
  }
  if (metrics?.model) {
    chips.push({ text: `MODEL ${metrics.model}`, cls: 'ai-tel-model', title: 'model of the active run' })
  }
  if (chips.length === 0) return null
  return (
    <div className="ai-summary" title="Application-level AI telemetry — only real evidence">
      {chips.map((c, i) => (
        <span key={i} className={`ai-chip ${c.cls}`} title={c.title}>
          {c.text}
        </span>
      ))}
    </div>
  )
}

/** Compact infrastructure chips — only categories actually detected. */
export function InfraSummaryChips({ infra }: { infra?: InfraSummary }) {
  if (!infra) return null
  const chips: { text: string; cls: string; title: string }[] = []
  if (infra.services.total > 0) {
    chips.push({
      text: `SERVICES ${infra.services.total}`,
      cls: 'infra-services',
      title: `${infra.services.running} RUNNING · ${infra.services.stopped} STOPPED`,
    })
  }
  if (infra.wsl.distributions > 0) {
    chips.push({
      text: `WSL ${infra.wsl.distributions}`,
      cls: 'infra-wsl',
      title: `${infra.wsl.running} RUNNING · ${infra.wsl.distributions} distributions`,
    })
  }
  if (infra.docker.containers > 0) {
    chips.push({
      text: `CONTAINERS ${infra.docker.containers}`,
      cls: 'infra-docker',
      title: `engine ${infra.docker.engine} · ${infra.docker.running} running`,
    })
  } else if (infra.docker.available && infra.docker.engine !== 'RUNNING') {
    chips.push({
      text: 'DOCKER STOPPED',
      cls: 'infra-docker-off',
      title: 'Docker engine not running — containers unavailable',
    })
  }
  if (infra.vms.total > 0) {
    chips.push({
      text: `VM ${infra.vms.total}`,
      cls: 'infra-vm',
      title: `${infra.vms.running} RUNNING · ${infra.vms.providers.join(', ')}`,
    })
  }
  if (chips.length === 0) return null
  return (
    <div className="infra-summary" title="Infrastructure summary — only real detections">
      {chips.map((c, i) => (
        <span key={i} className={`ai-chip ${c.cls}`} title={c.title}>
          {c.text}
        </span>
      ))}
    </div>
  )
}

export function Header({ status, mode, stats, feedTs, telemetry, aiMetrics, aiProviders, aiFixture }: Props) {
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
      <AiTelemetryChips metrics={aiMetrics ?? null} providers={aiProviders ?? null} fixture={aiFixture ?? false} />
      <InfraSummaryChips infra={stats?.infra} />

      <div className="header-right">
        {mode === 'demo' && <div className="demo-badge">DEMO MODE</div>}
        {mode === 'benchmark' && (
          <div className="benchmark-badge" title="TEST-ONLY synthetic graph — never real telemetry">
            BENCHMARK MODE · TEST DATA
          </div>
        )}
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
