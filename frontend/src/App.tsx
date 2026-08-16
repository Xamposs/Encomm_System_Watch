import { useState } from 'react'
import { CytoscapeGraph } from './graph/CytoscapeGraph'
import { useSystemWatch } from './hooks/useSystemWatch'
import { Header } from './components/Header'
import { FilterBar } from './components/FilterBar'
import { Inspector } from './components/Inspector'
import { EventDrawer } from './components/EventDrawer'
import { Legend } from './components/Legend'
import type { Filter } from './types/system'

export default function App() {
  const {
    status, mode, ready, stats, feedTs, events,
    selected, selectNode, drawerOpen, setDrawerOpen, controllerRef,
  } = useSystemWatch()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')

  return (
    <div className="app">
      <Header status={status} mode={mode} stats={stats} feedTs={feedTs} />

      <FilterBar
        filter={filter}
        onFilter={setFilter}
        search={search}
        onSearch={setSearch}
        onFit={() => controllerRef.current?.fit()}
        onZoomIn={() => controllerRef.current?.zoomIn()}
        onZoomOut={() => controllerRef.current?.zoomOut()}
      />

      <main className="main">
        <CytoscapeGraph
          controllerRef={controllerRef}
          filter={filter}
          search={search}
          onSelect={selectNode}
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

      {selected && <Inspector node={selected} onClose={() => selectNode(null)} />}

      <EventDrawer open={drawerOpen} onToggle={() => setDrawerOpen(!drawerOpen)} events={events} />
    </div>
  )
}
