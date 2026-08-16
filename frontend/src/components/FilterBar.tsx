import type { Filter } from '../types/system'

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
  onZoomIn: () => void
  onZoomOut: () => void
}

export function FilterBar({ filter, onFilter, search, onSearch, onFit, onZoomIn, onZoomOut }: Props) {
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
      <input
        className="search"
        type="text"
        placeholder="search processes…"
        value={search}
        onChange={(e) => onSearch(e.target.value)}
        spellCheck={false}
      />
      <div className="view-controls">
        <button className="icon-btn" title="Zoom out" onClick={onZoomOut}>−</button>
        <button className="icon-btn" title="Zoom in" onClick={onZoomIn}>+</button>
        <button className="fit-btn" onClick={onFit}>FIT ALL</button>
      </div>
    </div>
  )
}
