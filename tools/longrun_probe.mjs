#!/usr/bin/env node
/**
 * ENCOMM SYSTEM WATCH — long-run resource probe (v0.3.1).
 *
 * Loads the app in a headless browser (via CDP 9222), then samples every
 * probeInterval seconds for --minutes minutes:
 *   - browser JS heap (Performance.getMetrics -> JSHeapUsedSize)
 *   - cytoscape node/edge counts (must stay bounded on a live machine)
 *   - overlay particle/activity state (rAF loop must stop when idle)
 *   - event drawer row count (DOM bounded)
 *   - backend /api/health + /api/telemetry/debug (queue depth, counters)
 *   - backend process RSS (tasklist)
 *
 * Optional traffic phase: --traffic-from M --traffic-to N runs the real
 * loopback traffic harness (tools/network_activity_test/run.py) repeatedly
 * so the probe exercises live activity, particles and decay.
 *
 * Usage:
 *   node tools/longrun_probe.mjs --minutes 25 [--url http://127.0.0.1:8765/]
 *       [--traffic-from 5 --traffic-to 10] [--out esw-longrun.csv]
 *
 * Exits 0 with a PASS/LEAK-SUSPECT verdict; prints a summary. The CSV is
 * written next to the script (or to --out).
 */
import { spawn, execSync } from 'node:child_process'
import { writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const CDP_HTTP = 'http://127.0.0.1:9222'
const API = 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const args = process.argv.slice(2)
const get = (name, def) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 && args[i + 1] ? args[i + 1] : def
}
const MINUTES = Number(get('minutes', '25'))
const URL = get('url', 'http://127.0.0.1:8765/')
const INTERVAL = 10 // seconds between samples
const TRAFFIC_FROM = Number(get('traffic-from', '-1'))
const TRAFFIC_TO = Number(get('traffic-to', '-1'))
const TRAFFIC_PORT = Number(get('traffic-port', '19734'))
const OUT = get('out', join(__dirname, 'shots', 'longrun.csv'))

mkdirSync(join(__dirname, 'shots'), { recursive: true })

function backendPid() {
  try {
    const out = execSync('netstat -ano', { encoding: 'utf8' })
    for (const line of out.split('\n')) {
      if (line.includes(':8765') && line.includes('LISTENING')) {
        const m = line.trim().split(/\s+/)
        return Number(m[m.length - 1])
      }
    }
  } catch { /* ignore */ }
  return null
}

function backendRssMB(pid) {
  if (!pid) return null
  try {
    // timeout is critical: execSync BLOCKS the event loop, which would
    // also starve the CDP timeout timers if tasklist ever stalls
    const out = execSync(`tasklist /FI "PID eq ${pid}" /FO CSV /NH`,
      { encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'] })
    const row = out.trim().split('\n')[0]
    if (!row) return null
    const m = row.match(/"([^"]*)"/g)
    if (!m || m.length < 5) return null
    // strip everything but digits: "80,416 K" -> 80416
    const mem = m[4].replace(/[^\d]/g, '')
    if (!mem) return null
    return Math.round(Number(mem) / 1024)
  } catch { return null }
}

class CDP {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data)
      if (m.id && this.pending.has(m.id)) {
        this.pending.get(m.id)(m)
        this.pending.delete(m.id)
      }
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 20000)
      this.pending.set(id, (m) => {
        clearTimeout(t)
        if (m.error) reject(new Error(`${method}: ${JSON.stringify(m.error)}`))
        else resolve(m)
      })
    })
  }
  async eval(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (r.result?.exceptionDetails) throw new Error('eval exception: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 300))
    return r.result?.result?.value
  }
}

async function newTarget() {
  const r = await fetch(`${CDP_HTTP}/json/new?${encodeURIComponent(URL)}`, { method: 'PUT' })
  return r.json()
}

async function main() {
  const pid = backendPid()
  console.log(`long-run probe: ${MINUTES} min @ ${URL} (backend pid ${pid}, interval ${INTERVAL}s)`)
  const target = await newTarget()
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  const cdp = new CDP(ws)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await sleep(8000) // initial snapshot + layout

  const rows = []
  const header = 't_s,heapMB,nodes,edges,particles,activity,overlayRunning,eventRows,backendRSS_MB,queueDepth,eventsReceived,eventsMapped,eventsDropped,edgesTracked,loopErrors'
  rows.push(header)
  const t0 = Date.now()
  const totalMs = MINUTES * 60 * 1000

  let trafficPid = null
  const trafficWindows = []
  const launchTraffic = () => {
    trafficPid = spawn('python', ['tools/network_activity_test/run.py', '--port', String(TRAFFIC_PORT), '--watch', '45'], {
      cwd: join(__dirname, '..'), stdio: 'ignore',
    })
    trafficPid.on('exit', () => { trafficPid = null })
  }

  while (Date.now() - t0 < totalMs) {
    const t = Math.round((Date.now() - t0) / 1000)
    const inTraffic = TRAFFIC_FROM >= 0 && t >= TRAFFIC_FROM * 60 && t < TRAFFIC_TO * 60
    if (inTraffic && !trafficPid && t % 45 < INTERVAL) launchTraffic()

    let heap = null
    try {
      // Runtime.getHeapUsage is the modern heap API (Performance domain
      // metrics like JSHeapUsedSize were removed in newer Chrome builds)
      const h = await cdp.send('Runtime.getHeapUsage')
      if (h.result?.usedSize) heap = Math.round(h.result.usedSize / (1024 * 1024))
    } catch { /* page mid-reload */ }
    let nodes = -1, edges = -1, particles = -1, activity = -1, running = -1, eventRows = -1
    try {
      nodes = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes().length : -1`)
      edges = await cdp.eval(`window.__esw_cy ? window.__esw_cy.edges().length : -1`)
      const ov = JSON.parse(await cdp.eval(`window.__esw_controller ? JSON.stringify(window.__esw_controller.overlayStats()) : 'null'`))
      if (ov) { particles = ov.particles; activity = ov.activity; running = ov.running ? 1 : 0 }
      eventRows = await cdp.eval(`document.querySelectorAll('.event-row').length`)
    } catch { /* ignore */ }
    let dbg = null
    try { dbg = await (await fetch(`${API}/api/telemetry/debug`, { signal: AbortSignal.timeout(5000) })).json() } catch { /* ignore */ }
    let health = null
    try { health = await (await fetch(`${API}/api/health`, { signal: AbortSignal.timeout(5000) })).json() } catch { /* ignore */ }
    const rss = backendRssMB(pid)

    rows.push([
      t, heap, nodes, edges, particles, activity, running, eventRows,
      rss ?? '', dbg?.provider?.queue_depth ?? '', dbg?.provider?.events_received ?? '',
      dbg?.aggregator?.events_mapped_to_edges ?? '', dbg?.provider?.events_dropped ?? '',
      dbg?.edges_tracked ?? '', health?.loop_errors ?? '',
    ].join(','))
    if (t % 60 === 0 || t < 30) {
      console.log(`  t+${String(t).padStart(4)}s heap=${heap}MB rss=${rss}MB nodes=${nodes} edges=${edges} particles=${particles} act=${activity} rows=${eventRows} q=${dbg?.provider?.queue_depth ?? '?'} mapped=${dbg?.aggregator?.events_mapped_to_edges ?? '?'}`)
    }
    await sleep(INTERVAL * 1000)
  }
  if (trafficPid) trafficPid.kill()
  writeFileSync(OUT, rows.join('\n') + '\n')
  console.log(`csv: ${OUT}`)

  // trend analysis: first vs last quarter means for heap + RSS
  const nums = rows.slice(1).map((r) => r.split(','))
  const col = (i) => nums.map((r) => Number(r[i])).filter((v) => Number.isFinite(v) && v > 0)
  const mean = (a) => a.reduce((s, v) => s + v, 0) / Math.max(1, a.length)
  const heapS = col(1)
  const rssS = col(8)
  const heapQ1 = mean(heapS.slice(0, Math.max(1, Math.floor(heapS.length / 4))))
  const heapQ4 = mean(heapS.slice(-Math.max(1, Math.floor(heapS.length / 4))))
  const rssQ1 = mean(rssS.slice(0, Math.max(1, Math.floor(rssS.length / 4))))
  const rssQ4 = mean(rssS.slice(-Math.max(1, Math.floor(rssS.length / 4))))
  const heapDelta = heapQ4 - heapQ1
  const rssDelta = rssQ4 - rssQ1
  const heapMax = Math.max(...heapS, 0)
  const rssMax = Math.max(...rssS, 0)
  // CSV columns: 0=t 1=heap 2=nodes 3=edges 4=particles 5=activity 6=running
  //              7=eventRows 8=rss 9=queue 10=received 11=mapped 12=dropped
  //              13=edgesTracked 14=loopErrors
  const particlesMax = Math.max(...col(4), 0)
  const activityMax = Math.max(...col(5), 0)
  const rowsMax = Math.max(...col(7), 0)
  const queueMax = Math.max(...col(9), 0)
  const mappedTotal = nums.length ? Number(nums[nums.length - 1][11]) : 0

  console.log('\n==== LONG-RUN SUMMARY ====')
  console.log(`duration: ${MINUTES} min (${nums.length} samples)`)
  console.log(`browser heap: startQ ${heapQ1.toFixed(0)}MB -> endQ ${heapQ4.toFixed(0)}MB (max ${heapMax}MB, delta ${heapDelta >= 0 ? '+' : ''}${heapDelta.toFixed(1)}MB)`)
  console.log(`backend RSS:  startQ ${rssQ1.toFixed(0)}MB -> endQ ${rssQ4.toFixed(0)}MB (max ${rssMax}MB, delta ${rssDelta >= 0 ? '+' : ''}${rssDelta.toFixed(1)}MB)`)
  console.log(`particles max ${particlesMax} (budget 140) · activity edges max ${activityMax} (budget 400)`)
  console.log(`event rows max ${rowsMax} (render cap 150) · queue depth max ${queueMax} (cap 500)`)
  console.log(`events mapped total ${mappedTotal}`)

  const leak =
    (heapDelta > 30 && heapQ4 > heapQ1 * 1.25) ||
    (rssDelta > 60 && rssQ4 > rssQ1 * 1.3)
  const bounded = queueMax <= 500 && particlesMax <= 140 && activityMax <= 400 && rowsMax <= 150
  console.log(`verdict: ${leak ? 'LEAK-SUSPECT' : 'BOUNDED'} (heap ${heapDelta >= 0 ? '+' : ''}${heapDelta.toFixed(1)}MB, rss ${rssDelta >= 0 ? '+' : ''}${rssDelta.toFixed(1)}MB, all queues bounded: ${bounded})`)
  ws.close()
  process.exit(leak ? 1 : 0)
}

main().catch((e) => { console.error('PROBE ERROR:', e.message); process.exit(2) })
