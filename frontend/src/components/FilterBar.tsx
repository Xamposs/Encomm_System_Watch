import type { Filter, ViewMode } from '../types/system'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'ALL' },
  { id: 'active', label: 'ACTIVE CONNECTIONS' },
  { id: 'listening', label: 'LISTENING' },
  { id: 'highcpu', label: 'HIGH CPU' },
]

interface Props {
  filter: Filter
  onFilter: (f: Filter) => void
  search: string
  onSearch: (q: string) => void
  onFit: () => void
  onRelayout: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  viewMode: ViewMode
  onViewMode: (m: ViewMode) => void
  focusNode: string | null
  focusHops: number
  onFocusHops: (h: number) => void
  onFocusExit: () => void
  selectionCount: number
  onClearSelection: () => void
  signalsEnabled: boolean
  onSignals: (on: boolean) => void
}

export function FilterBar({
  filter, onFilter, search, onSearch, onFit, onRelayout, onZoomIn, onZoomOut,
  viewMode, onViewMode, focusNode, focusHops,
  onFocusHops, onFocusExit, selectionCount, onClearSelection,
  signalsEnabled, onSignals,
}: Props) {
  return (
    <div className="filterbar">
      <div className="pills">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            className={`pill ${filter === f.id ? 'active' : ''}`}
            onClick={() => onFilter(f.id)}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="view-toggle" title="Process view vs. process-family (parent/child) view">
        <button
          className={`pill ${viewMode === 'nodes' ? 'active' : ''}`}
          onClick={() => onViewMode('nodes')}
        >
          NODES
        </button>
        <button
          className={`pill ${viewMode === 'families' ? 'active' : ''}`}
          onClick={() => onViewMode('families')}
        >
          FAMILIES
        </button>
      </div>

      <input
        className="search"
        type="text"
        placeholder="search processes…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        spellCheck={false}
      />

      {focusNode && (
        <div className="focus-chip" title="FOCUS MODE — double-click a node to focus, EXIT to return">
          <span className="focus-label">FOCUS</span>
          <button
            className={`pill ${focusHops === 1 ? 'active' : ''}`}
            onClick={() => onFocusHops(1)}
          >
            1 HOP
          </button>
          <button
            className={`pill ${focusHops === 2 ? 'active' : ''}`}
            onClick={() => onFocusHops(2)}
          >
            2 HOPS
          </button>
          <button className="fit-btn" onClick={onFocusExit}>EXIT</button>
        </div>
      )}

      {selectionCount > 1 && (
        <div className="sel-chip">
          <span>{selectionCount} SEL</span>
          <button className="fit-btn" onClick={onClearSelection}>CLEAR</button>
        </div>
      )}
      <div className="view-controls">
        <button
          className={`flow-btn ${signalsEnabled ? 'active' : ''}`}
          title="Animate real observed activity: source → cable → destination"
          aria-pressed={signalsEnabled}
          onClick={() => onSignals(!signalsEnabled)}
        >
          <span className="flow-dot" /> LIVE FLOW
        </button>
        <button className="icon-btn" title="Zoom out" onClick={onZoomOut}>−</button>
        <button className="icon-btn" title="Zoom in" onClick={onZoomIn}>+</button>
        <button className="fit-btn" title="Re-run the layout (large graphs settle after big changes)" onClick={onRelayout}>RELAYOUT</button>
        <button className="fit-btn" onClick={onFit}>FIT ALL</button>
      </div>
    </div>
  )
}
