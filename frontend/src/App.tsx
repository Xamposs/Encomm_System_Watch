import { useState } from 'react'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { useSystemWatch } from './hooks/useSystemWatch'
import { Header } from './components/Header'
import { FilterBar } from './components/FilterBar'
import { Inspector } from './components/Inspector'
import { EventDrawer } from './components/EventDrawer'
import { Legend } from './components/Legend'
import type { Filter, SemanticView, ViewMode } from './types/system'

export default function App() {
  const {
    status, mode, ready, stats, feedTs, events,
    selected, selectNode, drawerOpen, setDrawerOpen, telemetry, controllerRef,
  } = useSystemWatch()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [viewMode, setViewMode] = useState<ViewMode>('nodes')
  const [semanticView, setSemanticView] = useState<SemanticView>('system')
  const [focusNode, setFocusNode] = useState<string | null>(null)
  const [focusHops, setFocusHops] = useState(1)
  const [selectionCount, setSelectionCount] = useState(0)

  const applyFocus = (id: string | null, hops = focusHops) => {
    setFocusNode(id)
    controllerRef.current?.setFocus(id, hops)
  }

  const onSemanticViewChange = (v: SemanticView) => {
    setSemanticView(v)
    controllerRef.current?.setSemanticView(v === 'ai')
  }

  return (
    <div className="app">
      <Header
        status={status}
        mode={mode}
        stats={stats}
        feedTs={feedTs}
        telemetry={telemetry}
      />

      <FilterBar
        filter={filter}
        onFilter={setFilter}
        search={search}
        onSearch={setSearch}
        onFit={() => controllerRef.current?.fit()}
        onZoomIn={() => controllerRef.current?.zoomIn()}
        onZoomOut={() => controllerRef.current?.zoomOut()}
        viewMode={viewMode}
        onViewMode={(m) => {
          setViewMode(m)
          controllerRef.current?.setViewMode(m)
        }}
        semanticView={semanticView}
        onSemanticView={onSemanticViewChange}
        focusNode={focusNode}
        focusHops={focusHops}
        onFocusHops={(h) => {
          setFocusHops(h)
          if (focusNode) controllerRef.current?.setFocus(focusNode, h)
        }}
        onFocusExit={() => applyFocus(null)}
        selectionCount={selectionCount}
        onClearSelection={() => {
          controllerRef.current?.clearSelection()
          setSelectionCount(0)
          selectNode(null)
        }}
      />

      <main className="main">
        <CytoscapeGraph
          controllerRef={controllerRef}
          filter={filter}
          search={search}
          onSelect={selectNode}
          onSelectMulti={(n) => setSelectionCount(n)}
          onFocusNode={(id) => applyFocus(id === focusNode ? null : id, 1)}
        />
        <Legend />
        {!ready && (
          <div className="boot-overlay">
            <span className={status === 'disconnected' ? 'boot-err' : ''}>
              {status === 'disconnected' ? 'LINK LOST — RETRYING…' : 'ESTABLISHING LINK…'}
            </span>
          </div>
        )}
      </main>

      {selectionCount > 1 && (
        <div className="selection-bar">
          <span className="selection-count">{selectionCount} NODES SELECTED</span>
          <span className="selection-hint">INSPECTION ONLY · SHIFT+DRAG TO SELECT · SHIFT+CLICK TOGGLE</span>
          <button className="fit-btn" onClick={() => {
            controllerRef.current?.clearSelection()
            setSelectionCount(0)
          }}>CLEAR</button>
        </div>
      )}

      {selectionCount === 1 && selected && (
        <Inspector node={selected} onClose={() => selectNode(null)} />
      )}

      <EventDrawer open={drawerOpen} onToggle={() => setDrawerOpen(!drawerOpen)} events={events} />
    </div>
  )
}
