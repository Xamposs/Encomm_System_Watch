import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import type { Filter } from '../types/system'
import { GraphController, STYLESHEET } from './GraphController'

interface Props {
  controllerRef: React.MutableRefObject<GraphController | null>
  filter: Filter
  search: string
  onSelect: (id: string | null) => void
}

/**
 * Owns the single Cytoscape instance for the app's lifetime.
 * The imperative GraphController is exposed via controllerRef so the
 * WebSocket layer can mutate the graph without triggering React renders.
 */
export function CytoscapeGraph({ controllerRef, filter, search, onSelect }: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const onSelectRef = useRef(onSelect)
  onSelectRef.current = onSelect

  useEffect(() => {
    const wrapper = wrapperRef.current
    if (!wrapper) return

    const cy = cytoscape({
      container: wrapper,
      elements: [],
      style: STYLESHEET,
      minZoom: 0.04,
      maxZoom: 4,
      wheelSensitivity: 0.25,
      hideEdgesOnViewport: false,
      textureOnViewport: true,
      motionBlur: false,
      boxSelectionEnabled: false,
    })
    const controller = new GraphController(cy, wrapper)
    controllerRef.current = controller
    // exposed for acceptance testing / debugging
    ;(window as unknown as Record<string, unknown>).__esw_cy = cy

    cy.on('tap', 'node', (ev) => onSelectRef.current(ev.target.id()))
    cy.on('tap', (ev) => {
      if (ev.target === cy) onSelectRef.current(null)
    })

    return () => {
      controller.destroy()
      controllerRef.current = null
      cy.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    controllerRef.current?.setFilter(filter)
  }, [filter, controllerRef])

  useEffect(() => {
    controllerRef.current?.setSearch(search)
  }, [search, controllerRef])

  return (
    <div className="graph-wrap">
      <div ref={wrapperRef} className="graph-canvas" />
    </div>
  )
}
