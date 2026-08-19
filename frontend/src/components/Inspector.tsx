import { fmtBps } from '../graph/GraphController'

interface Props {
  node: Record<string, unknown>
  onClose: () => void
}

function fmtTime(epoch: unknown): string {
  if (typeof epoch !== 'number' || !epoch) return '—'
  return new Date(epoch * 1000).toLocaleTimeString('en-GB', { hour12: false })
}

function ageLabel(lastActivity: unknown): string {
  if (typeof lastActivity !== 'number' || !lastActivity) return '—'
  const ms = Date.now() / 1000 - lastActivity
  if (ms < 0.5) return 'now'
  if (ms < 60) return `${Math.round(ms * 1000)} ms ago`
  return `${Math.round(ms)} s ago`
}

function fmtGb(mb: unknown): string {
  return typeof mb === 'number' ? `${(mb / 1024).toFixed(2)} GB` : '—'
}

/** SEMANTIC IDENTITY block — shown when the backend classified this node. */
function SemanticSection({ node }: { node: Record<string, unknown> }) {
  const semantic =
    (node.semantic as Record<string, unknown> | undefined) ??
    (node.kind === 'SEMANTIC' || node.kind === 'LOCAL_LLM'
      ? {
          semantic_type: node.semantic_type,
          semantic_name: node.semantic_name,
          confidence: node.confidence,
        }
      : undefined)
  if (!semantic) return null
  const evidence = node.evidence as { source: string; detail: string }[] | undefined
  const pids = node.pids as number[] | undefined
  return (
    <div className="inspector-net">
      <div className="inspector-net-title">SEMANTIC IDENTITY</div>
      <div className="inspector-net-row">
        <span className="sem-name">{String(semantic.semantic_name ?? '')}</span>
        <span className={`sem-conf sem-${String(semantic.confidence ?? 'LOW').toLowerCase()}`}>
          {String(semantic.confidence ?? '')}
        </span>
      </div>
      {typeof semantic.semantic_type === 'string' && (
        <div className="inspector-net-row">Type: {semantic.semantic_type}</div>
      )}
      {Array.isArray(pids) && pids.length > 0 && (
        <div className="inspector-net-row">Underlying PID{ pids.length > 1 ? 's' : '' }: {pids.join(', ')}</div>
      )}
      {Array.isArray(evidence) && evidence.length > 0 && (
        <div className="inspector-net-row">
          EVIDENCE
          {evidence.map((e, i) => (
            <div key={i} className="sem-evidence">
              ✓ {e.source} — {e.detail}
            </div>
          ))}
        </div>
      )}
      {typeof node.endpoint === 'string' && (
        <div className="inspector-net-row">Endpoint: {node.endpoint}</div>
      )}
      {typeof node.transport === 'string' && (
        <div className="inspector-net-row">Transport: {node.transport}</div>
      )}
      {typeof node.state === 'string' && (
        <div className="inspector-net-row">State: {node.state}</div>
      )}
      {Array.isArray(node.models) && (node.models as { id: string; state: string }[]).length > 0 && (
        <div className="inspector-net-row">
          MODELS
          {(node.models as { id: string; state: string }[]).map((m) => (
            <div key={m.id} className={`sem-model sem-model-${m.state.toLowerCase()}`}>
              {m.id} · {m.state}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** GPU metrics block for GPU nodes. */
function GpuSection({ node }: { node: Record<string, unknown> }) {
  const processes = node.processes as { pid: number; vram_mb?: number }[] | undefined
  return (
    <div className="inspector-net">
      <div className="inspector-net-title">GPU</div>
      <div className="inspector-net-row">Name: {String(node.name ?? '—')}</div>
      {typeof node.utilization_percent === 'number' && (
        <div className="inspector-net-row">Utilization: {node.utilization_percent}%</div>
      )}
      {(typeof node.vram_used_mb === 'number' || typeof node.vram_total_mb === 'number') && (
        <div className="inspector-net-row">
          VRAM: {fmtGb(node.vram_used_mb)} / {fmtGb(node.vram_total_mb)}
        </div>
      )}
      {typeof node.temperature_c === 'number' && (
        <div className="inspector-net-row">Temperature: {node.temperature_c} °C</div>
      )}
      {typeof node.power_w === 'number' && (
        <div className="inspector-net-row">Power: {node.power_w} W</div>
      )}
      {typeof node.driver === 'string' && (
        <div className="inspector-net-row">Driver: {node.driver}</div>
      )}
      {typeof node.fan_percent === 'number' && (
        <div className="inspector-net-row">Fan: {node.fan_percent}%</div>
      )}
      {Array.isArray(processes) && processes.length > 0 && (
        <div className="inspector-net-row">
          PROCESSES ON GPU
          {processes.map((p) => (
            <div key={p.pid} className="sem-evidence">
              PID {p.pid}
              {typeof p.vram_mb === 'number' ? ` · ${(p.vram_mb / 1024).toFixed(2)} GB` : ' · VRAM n/a'}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** Read-only node inspector. Observation only — no control buttons, by design. */
export function Inspector({ node, onClose }: Props) {
  const kind = String(node.kind ?? 'PROCESS')
  const rows: [string, string][] = []

  if (kind === 'PROCESS') {
    rows.push(
      ['Name', String(node.name ?? '')],
      ['PID', String(node.pid ?? '')],
      ['Status', String(node.status ?? '')],
      ['CPU', typeof node.cpu_percent === 'number' ? `${node.cpu_percent.toFixed(1)}%` : '—'],
      ['Memory', typeof node.memory_mb === 'number' ? `${Math.round(node.memory_mb)} MB` : '—'],
      ['Threads', String(node.num_threads ?? '—')],
      ['Parent PID', String(node.ppid ?? '—')],
      ['Executable', String(node.exe ?? '—')],
      ['User', String(node.username ?? '—')],
      ['Connections', String(node.conn_count ?? '—')],
      ['Started', fmtTime(node.created)],
      ['Command', Array.isArray(node.cmdline) ? (node.cmdline.join(' ') || '—') : String(node.cmdline ?? '—')],
    )
  } else if (kind === 'EXTERNAL_ENDPOINT') {
    rows.push(
      ['Type', 'EXTERNAL ENDPOINT'],
      ['Host', String(node.label ?? '')],
      ['IP', String(node.ip ?? '')],
      ['Ports', Array.isArray(node.ports) ? (node.ports as number[]).join(', ') : String(node.ports ?? '—')],
    )
  } else if (kind === 'LISTENING_PORT') {
    rows.push(
      ['Type', 'LISTENING PORT'],
      ['Address', `${String(node.ip ?? '')}:${String(node.port ?? '')}`],
      ['Protocol', String(node.proto ?? '—')],
    )
  } else if (kind === 'LOCAL_ENDPOINT') {
    rows.push(
      ['Type', 'LOCAL ENDPOINT'],
      ['Address', `${String(node.ip ?? '')}:${String(node.port ?? '')}`],
      ['Protocol', String(node.proto ?? '—')],
    )
  } else if (kind === 'SYSTEM') {
    rows.push(
      ['Type', 'SYSTEM'],
      ['Hostname', String(node.hostname ?? '')],
      ['Platform', String(node.platform ?? '')],
      ['CPU', typeof node.cpu_percent === 'number' ? `${node.cpu_percent.toFixed(1)}%` : '—'],
      ['Memory', typeof node.mem_percent === 'number' ? `${node.mem_percent.toFixed(1)}%` : '—'],
      ['RAM', `${String(node.mem_used_gb ?? '—')} / ${String(node.mem_total_gb ?? '—')} GB`],
      ['Uptime', typeof node.uptime_s === 'number' ? `${Math.round(node.uptime_s / 3600)} h` : '—'],
    )
  } else if (kind === 'SEMANTIC') {
    rows.push(
      ['Type', 'SEMANTIC RESOURCE'],
      ['Identity', String(node.semantic_name ?? '')],
      ['Confidence', String(node.confidence ?? '—')],
      ['State', String(node.state ?? '—')],
      ['Underlying PIDs', Array.isArray(node.pids) ? (node.pids as number[]).join(', ') || '—' : '—'],
    )
  } else if (kind === 'LOCAL_LLM') {
    rows.push(
      ['Type', 'LOCAL LLM'],
      ['Model', String(node.semantic_name ?? '')],
      ['State', String(node.state ?? '—')],
      ['Endpoint', String(node.endpoint ?? '—')],
    )
  } else if (kind === 'GPU') {
    rows.push(
      ['Type', 'GPU'],
      ['GPU', String(node.name ?? '—')],
      ['Utilization', typeof node.utilization_percent === 'number' ? `${node.utilization_percent}%` : '—'],
      ['VRAM', `${fmtGb(node.vram_used_mb)} / ${fmtGb(node.vram_total_mb)}`],
      ['Temperature', typeof node.temperature_c === 'number' ? `${node.temperature_c} °C` : '—'],
      ['Power', typeof node.power_w === 'number' ? `${node.power_w} W` : '—'],
      ['Driver', String(node.driver ?? '—')],
    )
  }

  const hasNet =
    typeof node.net_in_bps === 'number' ||
    typeof node.net_out_bps === 'number' ||
    typeof node.last_activity === 'number'

  return (
    <aside className="inspector">
      <div className="inspector-head">
        <span className="inspector-title">INSPECTOR</span>
        <button className="icon-btn" title="Close" onClick={onClose}>✕</button>
      </div>
      <div className="inspector-node">
        <div className="inspector-label">{String(node.label ?? node.id ?? '')}</div>
        <div className="inspector-kind">{kind}</div>
      </div>
      {/* SEMANTIC section — only when classification exists */}
      <SemanticSection node={node} />
      {/* GPU section — only for GPU nodes */}
      {kind === 'GPU' && <GpuSection node={node} />}
      {/* NETWORK section — only rendered when actual telemetry exists */}
      {hasNet && kind === 'PROCESS' && (
        <div className="inspector-net">
          <div className="inspector-net-title">NETWORK</div>
          <div className="inspector-net-row">
            <span className="net-arrow-down">↓ {typeof node.net_in_bps === 'number' ? fmtBps(node.net_in_bps) : '—'}</span>
          </div>
          <div className="inspector-net-row">
            <span className="net-arrow-up">↑ {typeof node.net_out_bps === 'number' ? fmtBps(node.net_out_bps) : '—'}</span>
          </div>
          <div className="inspector-net-row">
            Connections: {String(node.conn_count ?? '—')}
          </div>
          <div className="inspector-net-row">
            Last activity: {ageLabel(node.last_activity)}
          </div>
        </div>
      )}
      <dl className="inspector-rows">
        {rows.map(([k, v]) => (
          <div className="inspector-row" key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="inspector-foot">READ-ONLY · NO CONTROLS</div>
    </aside>
  )
}
