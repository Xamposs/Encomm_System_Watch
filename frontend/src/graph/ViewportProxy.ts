import type { Core } from 'cytoscape'

/** Camera state used to move a frozen overlay bitmap as one compositor layer. */
export interface ViewportSnapshot {
  zoom: number
  panX: number
  panY: number
}

export function captureViewport(cy: Core): ViewportSnapshot {
  const pan = cy.pan()
  return { zoom: Math.max(0.0001, cy.zoom()), panX: pan.x, panY: pan.y }
}

/**
 * Reproject pixels rendered at `from` into Cytoscape's current viewport.
 * This is one GPU-friendly transform regardless of graph size; no nodes,
 * labels, curves or particles are recomputed while the camera is moving.
 */
export function proxyViewport(
  element: HTMLElement,
  cy: Core,
  from: ViewportSnapshot,
): void {
  const now = captureViewport(cy)
  const scale = now.zoom / from.zoom
  const x = now.panX - scale * from.panX
  const y = now.panY - scale * from.panY
  element.style.transformOrigin = '0 0'
  element.style.transform = `matrix(${scale},0,0,${scale},${x},${y})`
}

export function resetViewportProxy(element: HTMLElement): void {
  element.style.transform = ''
  element.style.willChange = ''
}
