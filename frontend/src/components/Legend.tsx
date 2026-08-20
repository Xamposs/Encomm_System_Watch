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
      </div>
      <div className="legend-row">
        <span className="lg-edge lg-pulse">●</span> LIFECYCLE EVENT
        <span className="lg-edge lg-fwd">●</span> DATA OUT
        <span className="lg-edge lg-rev">●</span> DATA IN
      </div>
      <div className="legend-row">
        <span className="lg-node lg-sem">▭</span> SEMANTIC
        <span className="lg-node lg-llm">▭</span> LOCAL LLM
        <span className="lg-node lg-gpu">▭</span> GPU
        <span className="lg-edge lg-sem-e">╌╌</span> AI RELATION
      </div>
      <div className="legend-row">
        <span className="lg-node lg-svc">⚙</span> SERVICE
        <span className="lg-node lg-wsl">⬡</span> WSL
        <span className="lg-node lg-dkr">◆</span> DOCKER
        <span className="lg-node lg-vm">▣</span> VM
        <span className="lg-edge lg-infra-e">╌╌</span> INFRA RELATION
      </div>
      <div className="legend-row legend-hint">SHIFT+DRAG SELECT · DBL-CLICK FOCUS · 100% READ-ONLY</div>
    </div>
  )
}
