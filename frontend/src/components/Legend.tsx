export function Legend() {
  return (
    <div className="legend">
      <div className="legend-row">
        <span className="lg-node lg-proc">▭</span> PROCESS
        <span className="lg-node lg-ext">◯</span> EXTERNAL
        <span className="lg-node lg-lst">▢</span> LISTEN
        <span className="lg-node lg-sys">▭</span> SYSTEM
      </div>
      <div className="legend-row">
        <span className="lg-edge lg-loc">——</span> LOCAL
        <span className="lg-edge lg-lst-e">╌╌</span> LISTEN
        <span className="lg-edge lg-ext-e">──▶</span> REMOTE
        <span className="lg-edge lg-pulse">●</span> PULSE = REAL EVENT
      </div>
    </div>
  )
}
