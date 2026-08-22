/**
 * PerfMonitor — development/test diagnostics for the live graph (v0.3.1).
 *
 * Collects timings and counters along the update path (WebSocket message
 * handling, cytoscape batches, layouts, activity batches, AI toggle,
 * search/filter) plus element counts, overlay particle state, animation
 * frame activity and a memory trend (Chromium `performance.memory`).
 *
 * The monitor is passive: it never changes rendering behavior and adds no
 * UI by itself. It is exposed as `window.__esw_perf` for acceptance tests,
 * and the PerfPanel component renders its snapshot ONLY in benchmark mode
 * (or when forced via `window.__esw_perf.show()`), so the normal production
 * UI stays clean.
 */

export interface SampleStat {
  last: number
  max: number
  total: number
  count: number
}

export interface WsStat {
  last: number
  max: number
  count: number
}

export interface PerfSnapshot {
  benchmarkMode: boolean
  nodes: number
  edges: number
  visibleNodes: number
  visibleEdges: number
  updateMs: SampleStat
  layoutMs: SampleStat
  activityBatchMs: SampleStat
  aiToggleMs: number | null
  searchMs: number | null
  filterMs: number | null
  wsMs: Record<string, WsStat>
  particles: number
  overlayRunning: boolean
  overlayActivity: number
  fps: number | null
  heapMB: number | null
  heapSamples: number
  rAFActive: boolean
  idleRafActive: boolean
  mountedCards: number
  cardCreates: number
  cardUpdates: number
  cardRemovals: number
  cardDrawMs: SampleStat
  wireDrawMs: SampleStat
  socketDrawMs: SampleStat
  signalDrawMs: SampleStat
  interactionFrameMs: SampleStat
  overlayRedrawsPerSecond: number
  fitMs: number
  graphAspectRatio: number
  viewportAspectRatio: number
  layoutCacheHits: number
  layoutCacheMisses: number
}

function newStat(): SampleStat {
  return { last: 0, max: 0, total: 0, count: 0 }
}

class PerfMonitor {
  private benchmarkMode = false
  private graphSource: {
    nodes: () => number
    edges: () => number
    visibleNodes: () => number
    visibleEdges: () => number
    particles: () => number
    overlayRunning: () => boolean
    overlayActivity: () => number
    mountedCards: () => number
    graphAspectRatio: () => number
    viewportAspectRatio: () => number
    layoutCacheHits: () => number
    layoutCacheMisses: () => number
    fitMs: () => number
  } | null = null

  private updateMs = newStat()
  private layoutMs = newStat()
  private activityBatchMs = newStat()
  private wsMs: Record<string, WsStat> = {}
  private aiToggleMs: number | null = null
  private searchMs: number | null = null
  private filterMs: number | null = null
  private cardDrawMs = newStat()
  private wireDrawMs = newStat()
  private socketDrawMs = newStat()
  private signalDrawMs = newStat()
  private interactionFrameMs = newStat()
  private cardCreates = 0
  private cardUpdates = 0
  private cardRemovals = 0
  private overlayRedraws: number[] = []
  private overlayRaf = new Set<string>()

  // frame activity: counted while benchmark mode is on (validation only)
  private raf = 0
  private frameCount = 0
  private fpsWindowStart = 0
  private fps: number | null = null
  private rAFActive = false

  private heapSamples: number[] = []
  private heapTimer: number | undefined

  constructor() {
    if (typeof window !== 'undefined') {
      ;(window as unknown as Record<string, unknown>).__esw_perf = this
    }
  }

  setBenchmarkMode(on: boolean): void {
    this.benchmarkMode = on
    if (on) this.startFrameSampling()
    else this.stopFrameSampling()
  }

  setGraphSource(src: PerfMonitor['graphSource']): void {
    this.graphSource = src
    if (this.heapTimer === undefined) {
      this.heapTimer = window.setInterval(() => this.sampleHeap(), 5000)
    }
  }

  get benchmark(): boolean {
    return this.benchmarkMode
  }

  // ------------------------------------------------------------ timings

  recordUpdate(ms: number): void {
    this.updateMs.last = ms
    this.updateMs.max = Math.max(this.updateMs.max, ms)
    this.updateMs.total += ms
    this.updateMs.count += 1
  }

  recordLayout(ms: number): void {
    this.layoutMs.last = ms
    this.layoutMs.max = Math.max(this.layoutMs.max, ms)
    this.layoutMs.total += ms
    this.layoutMs.count += 1
  }

  recordActivityBatch(ms: number): void {
    this.activityBatchMs.last = ms
    this.activityBatchMs.max = Math.max(this.activityBatchMs.max, ms)
    this.activityBatchMs.total += ms
    this.activityBatchMs.count += 1
  }

  recordWs(type: string, ms: number): void {
    const st = (this.wsMs[type] ??= { last: 0, max: 0, count: 0 })
    st.last = ms
    st.max = Math.max(st.max, ms)
    st.count += 1
  }

  recordAiToggle(ms: number): void {
    this.aiToggleMs = ms
  }

  recordSearch(ms: number): void {
    this.searchMs = ms
  }

  recordFilter(ms: number): void {
    this.filterMs = ms
  }

  recordFit(_ms: number): void {
    // The live value is supplied by GraphController so it cannot drift from
    // viewportHealth(); keep this method as the instrumentation call site.
  }

  private addSample(stat: SampleStat, ms: number): void {
    stat.last = ms
    stat.max = Math.max(stat.max, ms)
    stat.total += ms
    stat.count += 1
  }

  recordOverlayDraw(kind: 'card' | 'wire' | 'socket' | 'signal', ms: number): void {
    this.addSample(
      kind === 'card' ? this.cardDrawMs :
        kind === 'wire' ? this.wireDrawMs :
          kind === 'socket' ? this.socketDrawMs : this.signalDrawMs,
      ms,
    )
    const now = performance.now()
    this.overlayRedraws.push(now)
    while (this.overlayRedraws.length && now - this.overlayRedraws[0] > 1000) this.overlayRedraws.shift()
  }

  recordInteractionFrame(ms: number): void {
    this.addSample(this.interactionFrameMs, ms)
  }

  recordCardCounts(creates: number, updates: number, removals: number): void {
    this.cardCreates = creates
    this.cardUpdates = updates
    this.cardRemovals = removals
  }

  setOverlayRaf(name: string, active: boolean): void {
    if (active) this.overlayRaf.add(name)
    else this.overlayRaf.delete(name)
  }

  // ------------------------------------------------------------ frames

  private frameLoop = (): void => {
    this.raf = requestAnimationFrame(this.frameLoop)
    const now = performance.now()
    this.frameCount += 1
    if (this.fpsWindowStart === 0) this.fpsWindowStart = now
    const span = now - this.fpsWindowStart
    if (span >= 1000) {
      this.fps = Math.round((this.frameCount * 1000) / span)
      this.frameCount = 0
      this.fpsWindowStart = now
    }
  }

  private startFrameSampling(): void {
    if (this.rAFActive) return
    this.rAFActive = true
    this.frameCount = 0
    this.fpsWindowStart = 0
    this.raf = requestAnimationFrame(this.frameLoop)
  }

  private stopFrameSampling(): void {
    this.rAFActive = false
    cancelAnimationFrame(this.raf)
    this.fps = null
  }

  // ------------------------------------------------------------ memory

  private sampleHeap(): void {
    const mem = (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory
    if (!mem) return
    this.heapSamples.push(mem.usedJSHeapSize / (1024 * 1024))
    if (this.heapSamples.length > 40) this.heapSamples.shift()
  }

  /** TEST ONLY (acceptance): expose the raw heap series for leak checks. */
  heapSeriesMB(): number[] {
    return [...this.heapSamples]
  }

  // ------------------------------------------------------------ snapshot

  snapshot(): PerfSnapshot {
    const g = this.graphSource
    const now = performance.now()
    while (this.overlayRedraws.length && now - this.overlayRedraws[0] > 1000) this.overlayRedraws.shift()
    return {
      benchmarkMode: this.benchmarkMode,
      nodes: g?.nodes() ?? 0,
      edges: g?.edges() ?? 0,
      visibleNodes: g?.visibleNodes() ?? 0,
      visibleEdges: g?.visibleEdges() ?? 0,
      updateMs: { ...this.updateMs },
      layoutMs: { ...this.layoutMs },
      activityBatchMs: { ...this.activityBatchMs },
      aiToggleMs: this.aiToggleMs,
      searchMs: this.searchMs,
      filterMs: this.filterMs,
      wsMs: Object.fromEntries(
        Object.entries(this.wsMs).map(([k, v]) => [k, { ...v }]),
      ),
      particles: g?.particles() ?? 0,
      overlayRunning: g?.overlayRunning() ?? false,
      overlayActivity: g?.overlayActivity() ?? 0,
      fps: this.fps,
      heapMB: this.heapSamples.length ? this.heapSamples[this.heapSamples.length - 1] : null,
      heapSamples: this.heapSamples.length,
      rAFActive: this.rAFActive,
      // Benchmark FPS sampling is diagnostic and tracked separately as
      // rAFActive. idleRafActive answers only whether a render overlay is live.
      idleRafActive: this.overlayRaf.size > 0,
      mountedCards: g?.mountedCards() ?? 0,
      cardCreates: this.cardCreates,
      cardUpdates: this.cardUpdates,
      cardRemovals: this.cardRemovals,
      cardDrawMs: { ...this.cardDrawMs },
      wireDrawMs: { ...this.wireDrawMs },
      socketDrawMs: { ...this.socketDrawMs },
      signalDrawMs: { ...this.signalDrawMs },
      interactionFrameMs: { ...this.interactionFrameMs },
      overlayRedrawsPerSecond: this.overlayRedraws.length,
      fitMs: g?.fitMs() ?? 0,
      graphAspectRatio: g?.graphAspectRatio() ?? 0,
      viewportAspectRatio: g?.viewportAspectRatio() ?? 0,
      layoutCacheHits: g?.layoutCacheHits() ?? 0,
      layoutCacheMisses: g?.layoutCacheMisses() ?? 0,
    }
  }
}

export const perf = new PerfMonitor()
