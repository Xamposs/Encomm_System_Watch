interface Props {
  node: Record<string, unknown>
  onClose: () => void
}

function fmtTime(epoch: unknown): string {
  if (typeof epoch !== 'number' || !epoch) return '—'
  return new Date(epoch * 1000).toLocaleTimeString('en-GB', { hour12: false })
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
  }

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
