import type { Core, EventObjectNode, NodeSingular } from 'cytoscape'
import { perf } from './PerfMonitor'

type Tone = 'cyan' | 'blue' | 'green' | 'amber' | 'magenta' | 'violet' | 'red'
type LodMode = 'near' | 'mid' | 'far'

interface CardRecord {
  root: HTMLDivElement
  code: HTMLSpanElement
  icon: HTMLSpanElement
  title: HTMLSpanElement
  metric: HTMLSpanElement
  detail: HTMLSpanElement
  signature: string
  transform: string
  state: string
  signalTimer?: number
}

const KIND_ICON: Record<string, string> = {
  PROCESS: '▤',
  SYSTEM: '◆',
  EXTERNAL_ENDPOINT: '◎',
  LISTENING_PORT: '◉',
  LOCAL_ENDPOINT: '⌁',
  SEMANTIC: '✦',
  LOCAL_LLM: '◈',
  GPU: '⚙',
  SERVICE: '▣',
  WSL: '◇',
  DOCKER_ENGINE: '⬡',
  CONTAINER: '⬢',
  DOCKER_NETWORK: '⌘',
  VM: '▲',
  AI_RUNTIME: '✹',
}

function stableNumber(value: string): number {
  let h = 2166136261
  for (let i = 0; i < value.length; i++) {
    h ^= value.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return Math.abs(h >>> 0)
}

function clean(value: unknown): string {
  return String(value ?? '').replace(/\s+/g, ' ').trim()
}

function titleFor(node: NodeSingular): string {
  const candidates = [
    node.data('display_name'),
    node.data('name'),
    node.data('model_id'),
    node.data('address'),
    node.data('label'),
    node.id(),
  ]
  const title = candidates.map(clean).find(Boolean) ?? node.id()
  return title.split(' · ')[0].replace(/\bPID\s+\d+.*$/i, '').trim() || title
}

function fmtMemory(value: unknown): string {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return ''
  return n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${Math.round(n)} MB`
}

function detailFor(node: NodeSingular): string {
  const kind = clean(node.data('kind'))
  if (kind === 'PROCESS') {
    const parts = [`PID ${clean(node.data('pid')) || '—'}`]
    const cpu = Number(node.data('cpu_percent'))
    if (Number.isFinite(cpu)) parts.push(`CPU ${cpu.toFixed(cpu >= 10 ? 0 : 1)}%`)
    const mem = fmtMemory(node.data('memory_mb'))
    if (mem) parts.push(mem)
    return parts.join(' · ')
  }
  if (kind === 'SERVICE') {
    const status = clean(node.data('status')).toUpperCase() || 'UNKNOWN'
    const pid = clean(node.data('pid'))
    return `${status}${pid && pid !== '0' ? ` · PID ${pid}` : ''} · WINDOWS SERVICE`
  }
  if (kind === 'GPU') {
    const util = Number(node.data('utilization_percent'))
    const used = Number(node.data('vram_used_mb'))
    const total = Number(node.data('vram_total_mb'))
    const parts = [Number.isFinite(util) ? `${Math.round(util)}% LOAD` : 'GPU TELEMETRY']
    if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {
      parts.push(`${(used / 1024).toFixed(1)}/${(total / 1024).toFixed(1)} GB VRAM`)
    }
    return parts.join(' · ')
  }
  if (kind === 'LISTENING_PORT') {
    const proto = clean(node.data('proto')).toUpperCase()
    const port = clean(node.data('port')) || clean(node.data('local_port')) || clean(node.data('label'))
    return `${proto || 'SOCKET'} LISTENER · ${port}`
  }
  if (kind === 'EXTERNAL_ENDPOINT' || kind === 'LOCAL_ENDPOINT') {
    const proto = clean(node.data('proto')).toUpperCase()
    const port = clean(node.data('port')) || clean(node.data('remote_port'))
    return `${kind === 'EXTERNAL_ENDPOINT' ? 'REMOTE PEER' : 'LOCAL SOCKET'}${proto ? ` · ${proto}` : ''}${port ? `:${port}` : ''}`
  }
  if (kind === 'SEMANTIC') {
    return `${clean(node.data('semantic_type')) || 'AI SIGNAL'} · ${clean(node.data('confidence')) || 'OBSERVED'}`
  }
  if (kind === 'LOCAL_LLM') return `${clean(node.data('state')) || 'AVAILABLE'} · LOCAL MODEL RUNTIME`
  if (kind === 'AI_RUNTIME') return `${clean(node.data('ai_role')) || 'AI ACTIVITY'} · LIVE TELEMETRY`
  if (kind === 'WSL') return `${clean(node.data('state')) || 'DETECTED'} · WSL DISTRIBUTION`
  if (kind === 'DOCKER_ENGINE') return `${clean(node.data('state')) || 'DETECTED'} · CONTAINER ENGINE`
  if (kind === 'CONTAINER') return `${clean(node.data('state')) || 'DETECTED'} · CONTAINER`
  if (kind === 'DOCKER_NETWORK') return 'DOCKER NETWORK · OBSERVED'
  if (kind === 'VM') return `${clean(node.data('state')) || 'DETECTED'} · VIRTUAL MACHINE`
  if (kind === 'SYSTEM') return 'LOCAL HOST · SYSTEM ROOT'
  return `${kind || 'NODE'} · OBSERVED`
}

function toneFor(node: NodeSingular): Tone {
  const kind = clean(node.data('kind'))
  if (node.data('highCpu')) return 'red'
  if (kind === 'SERVICE') return clean(node.data('status')).toLowerCase() === 'running' ? 'amber' : 'red'
  if (kind === 'SEMANTIC' || kind === 'AI_RUNTIME') return 'magenta'
  if (kind === 'LOCAL_LLM' || kind === 'VM') return 'violet'
  if (kind === 'GPU' || kind === 'LISTENING_PORT' || kind === 'CONTAINER') return 'green'
  if (kind === 'EXTERNAL_ENDPOINT' || kind === 'DOCKER_ENGINE') return 'blue'
  if (kind === 'WSL' || kind === 'DOCKER_NETWORK') return 'cyan'
  if (kind === 'SYSTEM') return 'amber'
  const palette: Tone[] = ['cyan', 'blue', 'magenta', 'amber', 'violet', 'cyan']
  return palette[stableNumber(node.id()) % palette.length]
}

function nodeCode(node: NodeSingular): string {
  const kind = clean(node.data('kind'))
  const prefix = kind === 'SERVICE' ? 's' : kind === 'PROCESS' ? 'p' : 'n'
  const pid = clean(node.data('pid'))
  return `${prefix}${pid || String(stableNumber(node.id()) % 1000).padStart(3, '0')}`
}

function metricFor(node: NodeSingular): string {
  const cpu = Number(node.data('cpu_percent'))
  if (Number.isFinite(cpu) && cpu > 0) return `${cpu.toFixed(cpu >= 10 ? 0 : 1)}%`
  const conn = Number(node.data('conn_count'))
  if (Number.isFinite(conn) && conn > 0) return `${Math.round(conn)} LINK${conn === 1 ? '' : 'S'}`
  const status = clean(node.data('status')).toUpperCase()
  return status === 'RUNNING' ? 'LIVE' : 'OBS'
}

/**
 * HTML card layer aligned with Cytoscape nodes. Cytoscape keeps ownership of
 * graph geometry, hit-testing, pan, zoom and selection; this layer is purely
 * visual and therefore cannot alter or invent topology.
 */
export class CardOverlay {
  private root: HTMLDivElement
  private cards = new Map<string, CardRecord>()
  private raf = 0
  private destroyed = false
  private lodMode: LodMode | null = null
  private interacting = false
  private creates = 0
  private updates = 0
  private removals = 0

  private static readonly NEAR_ZOOM = 0.5
  private static readonly MID_ZOOM = 0.09
  private static readonly MAX_NEAR_CARDS = 180

  constructor(
    private cy: Core,
    private container: HTMLElement,
  ) {
    this.root = document.createElement('div')
    this.root.className = 'graph-card-layer'
    this.container.appendChild(this.root)
    this.container.addEventListener('esw:layout', this.requestDraw)
    this.container.addEventListener('esw:cards-refresh', this.requestDraw)
    this.container.addEventListener('esw:signal', this.onSignal)
    this.container.addEventListener('esw:interaction', this.onInteraction)
    cy.on('pan zoom resize', this.requestDraw)
    cy.on('position', 'node', this.requestDraw)
    cy.on('add data', 'node', this.onNodeChange)
    cy.on('remove', 'node', this.onNodeRemove)
    cy.on('select unselect', 'node', this.requestDraw)
    cy.on('destroy', this.onDestroy)
    this.requestDraw()
    window.setTimeout(this.requestDraw, 0)
  }

  private onDestroy = (): void => {
    this.destroyed = true
  }

  private onNodeChange = (ev: EventObjectNode): void => {
    if (this.interacting || this.lodMode !== 'near') return
    const record = this.cards.get(ev.target.id())
    if (record) this.updateContent(record, ev.target)
    else if (ev.type === 'add') this.requestDraw()
  }

  private onNodeRemove = (ev: EventObjectNode): void => {
    this.removeCard(ev.target.id())
  }

  private makePart(className: string): HTMLSpanElement {
    const el = document.createElement('span')
    el.className = className
    return el
  }

  private ensureCard(node: NodeSingular): CardRecord {
    const existing = this.cards.get(node.id())
    if (existing) return existing
    const root = document.createElement('div')
    const record = {
      root,
      code: this.makePart('graph-card-code'),
      icon: this.makePart('graph-card-icon'),
      title: this.makePart('graph-card-title'),
      metric: this.makePart('graph-card-metric'),
      detail: this.makePart('graph-card-detail'),
      signature: '',
      transform: '',
      state: '',
      signalTimer: undefined as number | undefined,
    }
    root.className = 'graph-card'
    root.dataset.nodeId = node.id()
    root.append(
      record.code,
      record.icon,
      record.title,
      record.metric,
      record.detail,
      this.makePart('graph-card-state'),
    )
    this.cards.set(node.id(), record)
    this.root.appendChild(root)
    this.creates += 1
    this.updateContent(record, node)
    return record
  }

  private updateContent(record: CardRecord, node: NodeSingular): void {
    const tone = toneFor(node)
    const code = nodeCode(node)
    const icon = KIND_ICON[clean(node.data('kind'))] ?? '◇'
    const title = titleFor(node)
    const metric = metricFor(node)
    const detail = detailFor(node)
    const signature = `${tone}\u0000${code}\u0000${icon}\u0000${title}\u0000${metric}\u0000${detail}`
    if (record.signature === signature) return
    this.updates += 1
    record.signature = signature
    record.root.dataset.tone = tone
    record.code.textContent = code
    record.icon.textContent = icon
    record.title.textContent = title
    record.metric.textContent = metric
    record.detail.textContent = detail
  }

  private removeCard(id: string): void {
    const record = this.cards.get(id)
    if (!record) return
    if (record.signalTimer !== undefined) window.clearTimeout(record.signalTimer)
    record.root.remove()
    this.cards.delete(id)
    this.removals += 1
  }

  private clearCards(): void {
    for (const id of [...this.cards.keys()]) this.removeCard(id)
    this.root.dataset.mounted = '0'
  }

  private onSignal = (event: Event): void => {
    if (this.lodMode !== 'near') return
    const detail = (event as CustomEvent<{
      nodeId: string
      phase: 'source' | 'target'
      color: string
    }>).detail
    if (!detail) return
    this.root.dataset.signalCount = String(Number(this.root.dataset.signalCount ?? 0) + 1)
    this.root.dataset.lastSignal = `${detail.phase}:${detail.nodeId}`
    const record = this.cards.get(detail.nodeId)
    if (!record) return
    const className = detail.phase === 'source' ? 'signal-source' : 'signal-target'
    record.root.style.setProperty('--signal-color', detail.color)
    record.root.classList.add(className)
    if (record.signalTimer !== undefined) window.clearTimeout(record.signalTimer)
    record.signalTimer = window.setTimeout(() => {
      record.root.classList.remove('signal-source', 'signal-target')
      record.signalTimer = undefined
    }, detail.phase === 'source' ? 520 : 720)
  }

  private onInteraction = (event: Event): void => {
    this.interacting = Boolean((event as CustomEvent<{ active?: boolean }>).detail?.active)
    this.root.dataset.interacting = this.interacting ? 'true' : 'false'
    this.requestDraw()
  }

  private setLodMode(mode: LodMode): void {
    if (this.lodMode === mode) return
    const modeChanged = this.lodMode !== mode
    this.lodMode = mode
    this.root.dataset.mode = mode
    this.container.classList.toggle('graph-low-detail', mode !== 'near')
    this.cy.batch(() => {
      const nodes = this.cy.nodes()
      nodes.toggleClass('card-lod-near', mode === 'near')
      nodes.toggleClass('card-lod-mid', mode === 'mid')
      nodes.toggleClass('card-lod-far', mode === 'far')
    })
    this.cy.style().update()
    if (modeChanged && mode !== 'near') this.clearCards()
  }

  private updateState(record: CardRecord, node: NodeSingular): void {
    const state = [
      node.selected() ? 'selected' : '',
      node.hasClass('ai-dim') || node.hasClass('focus-dim') ? 'dim' : '',
      node.hasClass('fading') ? 'fading' : '',
      node.hasClass('svc-bank') || node.hasClass('orphan-bank') ? 'banked' : '',
      node.data('benchmark') ? 'benchmark' : '',
    ].join('|')
    if (record.state === state) return
    record.state = state
    record.root.classList.toggle('is-selected', node.selected())
    record.root.classList.toggle('is-dim', node.hasClass('ai-dim') || node.hasClass('focus-dim'))
    record.root.classList.toggle('is-fading', node.hasClass('fading'))
    record.root.classList.toggle('is-banked', node.hasClass('svc-bank') || node.hasClass('orphan-bank'))
    record.root.classList.toggle('is-benchmark', !!node.data('benchmark'))
  }

  private pruneCards(wanted: Set<string>): void {
    for (const id of [...this.cards.keys()]) {
      if (!wanted.has(id)) this.removeCard(id)
    }
  }

  private requestDraw = (): void => {
    if (this.destroyed || this.raf) return
    perf.setOverlayRaf('cards', true)
    this.raf = requestAnimationFrame(() => {
      this.raf = 0
      try { this.draw() } finally { perf.setOverlayRaf('cards', false) }
    })
  }

  private draw(): void {
    const started = performance.now()
    const zoom = this.cy.zoom()
    const mode: LodMode = zoom >= CardOverlay.NEAR_ZOOM
      ? 'near'
      : zoom >= CardOverlay.MID_ZOOM ? 'mid' : 'far'
    this.setLodMode(mode)
    this.root.dataset.visibleNodes = String(this.cy.nodes(':visible').length)
    this.root.dataset.zoom = zoom.toFixed(4)
    const sample = this.cy.nodes(':visible').first()
    if (sample.length) {
      this.root.dataset.sampleClasses = sample.classes().join(' ')
      this.root.dataset.sampleWidth = sample.renderedWidth().toFixed(1)
      this.root.dataset.sampleHeight = sample.renderedHeight().toFixed(1)
      this.root.dataset.sampleOpacity = String(sample.style('background-opacity'))
    }
    if (mode !== 'near') {
      perf.recordCardCounts(this.creates, this.updates, this.removals)
      perf.recordOverlayDraw('card', performance.now() - started)
      return
    }
    const margin = 110
    const w = this.container.clientWidth
    const h = this.container.clientHeight
    const wanted = new Set<string>()
    const center = { x: w / 2, y: h / 2 }
    const candidates: Array<{ node: NodeSingular; x: number; y: number; distance: number }> = []
    this.cy.nodes(':visible').forEach((node) => {
      if (!node.visible()) return
      const p = node.renderedPosition()
      const onstage = p.x > -margin && p.x < w + margin && p.y > -margin && p.y < h + margin
      if (!onstage) return
      candidates.push({ node, x: p.x, y: p.y, distance: Math.hypot(p.x - center.x, p.y - center.y) })
    })
    candidates.sort((a, b) => a.distance - b.distance || a.node.id().localeCompare(b.node.id()))
    for (const candidate of candidates.slice(0, CardOverlay.MAX_NEAR_CARDS)) {
      const { node, x, y } = candidate
      wanted.add(node.id())
      // While dragging/zooming, mutate transforms only. New DOM mounts,
      // content writes and pruning wait for the settle event.
      const record = this.interacting ? this.cards.get(node.id()) : this.ensureCard(node)
      if (!record) continue
      const transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) translate(-50%, -50%) scale(${zoom.toFixed(4)})`
      if (record.transform !== transform) {
        record.transform = transform
        record.root.style.transform = transform
      }
      if (!this.interacting) {
        this.updateContent(record, node)
        this.updateState(record, node)
      }
    }
    if (!this.interacting) this.pruneCards(wanted)
    this.root.dataset.mounted = String(this.cards.size)
    this.root.dataset.cap = String(CardOverlay.MAX_NEAR_CARDS)
    this.root.dataset.candidates = String(candidates.length)
    perf.recordCardCounts(this.creates, this.updates, this.removals)
    const elapsed = performance.now() - started
    perf.recordOverlayDraw('card', elapsed)
    if (this.interacting) perf.recordInteractionFrame(elapsed)
  }

  destroy(): void {
    this.destroyed = true
    if (this.raf) cancelAnimationFrame(this.raf)
    perf.setOverlayRaf('cards', false)
    this.cy.off('pan zoom resize', this.requestDraw)
    this.cy.off('position', 'node', this.requestDraw)
    this.cy.off('add data', 'node', this.onNodeChange)
    this.cy.off('remove', 'node', this.onNodeRemove)
    this.cy.off('select unselect', 'node', this.requestDraw)
    this.container.removeEventListener('esw:layout', this.requestDraw)
    this.container.removeEventListener('esw:cards-refresh', this.requestDraw)
    this.container.removeEventListener('esw:signal', this.onSignal)
    this.container.removeEventListener('esw:interaction', this.onInteraction)
    this.clearCards()
    this.root.remove()
  }
}
