import { useEffect, useLayoutEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import type { Filter } from '../types/system'
import { GraphController, STYLESHEET } from './GraphController'
import { WireUnderlay } from './WireUnderlay'
import { SocketOverlay } from './SocketOverlay'
import { CardOverlay } from './CardOverlay'

interface Props {
  controllerRef: React.MutableRefObject<GraphController | null>
  filter: Filter
  search: string
  onSelect: (id: string | null) => void
  onSelectMulti: (count: number) => void
  onFocusNode: (id: string) => void
}

/**
 * Owns the single Cytoscape instance for the app's lifetime.
 * The imperative GraphController is exposed via controllerRef so the
 * WebSocket layer can mutate the graph without triggering React renders.
 *
 * Interactions:
 *  - single click node      -> inspector (inspection only)
 *  - shift + click node     -> additive multi-select (native)
 *  - shift + drag background-> selection box (rubber band)
 *  - double click node      -> FOCUS mode (1 hop)
 *  - left drag background   -> pan (kept; box selection never hijacks it)
 */
export function CytoscapeGraph({ controllerRef, filter, search, onSelect, onSelectMulti, onFocusNode }: Props) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const onSelectRef = useRef(onSelect)
  const onSelectMultiRef = useRef(onSelectMulti)
  const onFocusNodeRef = useRef(onFocusNode)
  onSelectRef.current = onSelect
  onSelectMultiRef.current = onSelectMulti
  onFocusNodeRef.current = onFocusNode

  // The graph controller must exist before the parent starts the websocket;
  // otherwise a very fast localhost snapshot can arrive before there is a
  // graph to receive it and every later event begins at (0, 0).
  useLayoutEffect(() => {
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
      // box selection stays OFF so left-drag on the background keeps
      // panning; multi-select is provided via shift+click / shift+drag
      // (see the tap handlers below)
      boxSelectionEnabled: false,
      selectionType: 'additive',
    })
    const controller = new GraphController(cy, wrapper)
    controllerRef.current = controller
    // reference-fidelity canvases (v1.0.2 final pass): the wire underlay
    // paints the colored edge glow + zone headers BELOW the cards; the
    // socket overlay paints the connection ports ABOVE them.
    const underlay = new WireUnderlay(cy, wrapper, () => controller.getZones())
    const cards = new CardOverlay(cy, wrapper)
    const sockets = new SocketOverlay(cy, wrapper)
    // exposed for acceptance testing / debugging
    ;(window as unknown as Record<string, unknown>).__esw_cy = cy
    ;(window as unknown as Record<string, unknown>).__esw_controller = controller

    cy.on('tap', 'node', (ev) => {
      const shift = !!(ev.originalEvent as MouseEvent | undefined)?.shiftKey
      if (!shift) {
        // plain tap = inspect one node: replace the selection
        cy.elements(':selected').not(ev.target).unselect()
      }
      onSelectRef.current(ev.target.id())
    })
    cy.on('tap', (ev) => {
      if (ev.target === cy) {
        cy.elements(':selected').unselect()
        onSelectRef.current(null)
      }
    })
    // multi-select accounting (native select + our shift+drag box both
    // fire these events)
    cy.on('select unselect', () => {
      const n = cy.elements(':selected').length
      onSelectMultiRef.current(n)
      if (n !== 1) onSelectRef.current(null)
    })
    // double-click a node = FOCUS mode (pure visualization)
    cy.on('dbltap', 'node', (ev) => onFocusNodeRef.current(ev.target.id()))

    controller.initShiftBoxSelection()

    return () => {
      underlay.destroy()
      cards.destroy()
      sockets.destroy()
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
