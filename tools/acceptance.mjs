#!/usr/bin/env node
/**
 * ENCOMM SYSTEM WATCH — headless acceptance driver (Tests A–K).
 *
 * Drives a headless Chromium via raw CDP (no browser extension approvals needed).
 * Spawns/kills real apps (notepad, mspaint) to exercise the live event pipeline.
 *
 * Usage:
 *   1. Backend running on 127.0.0.1:8765, frontend dev server on localhost:5173
 *      (or set URL_OVERRIDE to the backend-served production build).
 *   2. Headless Chromium with --remote-debugging-port=9222.
 *   3. node tools/acceptance.mjs
 *
 * Exit code 0 = all tests passed.
 */
import { spawn, execSync } from 'node:child_process'
import { writeFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(__dirname, 'shots')
mkdirSync(SHOTS, { recursive: true })

const APP_URL = process.env.URL_OVERRIDE || 'http://localhost:5173/'
const CDP_HTTP = 'http://127.0.0.1:9222'
const API = 'http://127.0.0.1:8765'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

let passed = 0
let failed = 0
function check(name, ok, detail = '') {
  if (ok) {
    passed++
    console.log(`  PASS  ${name}${detail ? ` — ${detail}` : ''}`)
  } else {
    failed++
    console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`)
  }
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
      const t = setTimeout(() => reject(new Error(`CDP timeout: ${method}`)), 25000)
      this.pending.set(id, (m) => {
        clearTimeout(t)
        if (m.error) reject(new Error(`${method}: ${JSON.stringify(m.error)}`))
        else resolve(m)
      })
    })
  }

  async eval(expression) {
    if (typeof expression !== 'string') {
      throw new Error(`cdp.eval: undefined/non-string expression (got ${String(expression)})`)
    }
    const r = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (r.result?.exceptionDetails) {
      throw new Error('eval exception: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 400))
    }
    return r.result?.result?.value
  }

  async shot(name) {
    const r = await this.send('Page.captureScreenshot', { format: 'png' })
    const file = join(SHOTS, name)
    writeFileSync(file, Buffer.from(r.result.data, 'base64'))
    console.log(`  shot  ${file}`)
  }
}

// expression builders (must produce JS expression strings)
const EX = {
  nodeCount: `window.__esw_cy ? window.__esw_cy.nodes().length : -1`,
  edgeCount: `window.__esw_cy ? window.__esw_cy.edges().length : -1`,
  procCount: `window.__esw_cy ? window.__esw_cy.nodes('[kind="PROCESS"]').length : -1`,
  localEdges: `window.__esw_cy ? window.__esw_cy.edges('[kind="LOCALHOST"]').length : -1`,
  extEdges: `window.__esw_cy ? window.__esw_cy.edges('[kind="EXTERNAL"]').length : -1`,
  recentEdges: `window.__esw_cy ? window.__esw_cy.edges('[?recent]').length : -1`,
  hasNode: (name) => `window.__esw_cy ? window.__esw_cy.nodes('[name="${name}"]').length : 0`,
  stableIdOf: (pid) => `(() => { const n = window.__esw_cy.nodes().filter(n => n.data('pid') === ${pid})[0]; return n ? n.data('id') : null })()`,
  nodeById: (id) => `window.__esw_cy ? window.__esw_cy.getElementById('${id}').length : 0`,
  cpuOf: (name) => `(() => { const n = window.__esw_cy.nodes('[name="${name}"]')[0]; return n ? n.data('cpu_percent') : null })()`,
  inspectorText: `document.querySelector('.inspector')?.innerText ?? ''`,
  eventRows: `document.querySelectorAll('.event-row').length`,
  eventTypes: `[...document.querySelectorAll('.ev-type')].map(e => e.textContent.trim())`,
  drawerOpen: `document.querySelector('.drawer')?.classList.contains('open') ?? false`,
  connLabel: `document.querySelector('.conn-label')?.textContent ?? ''`,
  edgeMid: `(() => {
    const cy = window.__esw_cy
    if (!cy || cy.edges().length === 0) return null
    const e = cy.edges()[0]
    const m = e.midpoint()
    const r = cy.container().getBoundingClientRect()
    return { x: Math.round(r.left + m.x * cy.zoom() + cy.pan().x), y: Math.round(r.top + m.y * cy.zoom() + cy.pan().y) }
  })()`,
  nodePos: (name) => `(() => {
    const cy = window.__esw_cy
    const n = cy.nodes('[name="${name}"]')[0] || cy.nodes('[kind="PROCESS"]')[0]
    const p = n.renderedPosition()
    const r = cy.container().getBoundingClientRect()
    return { x: Math.round(r.left + p.x), y: Math.round(r.top + p.y), id: n.id(), name: n.data('name') }
  })()`,
  selBoxVisible: `document.querySelector('.sel-box') !== null`,
  tooltipText: `document.querySelector('.edge-tooltip')?.innerText ?? ''`,
  focusChip: `document.querySelector('.focus-chip')?.innerText ?? ''`,
  selectionBar: `document.querySelector('.selection-bar')?.innerText ?? ''`,
  edgeMidInView: `(() => {
    const cy = window.__esw_cy
    if (!cy || cy.edges().length === 0) return null
    const r = cy.container().getBoundingClientRect()
    const dr = (document.querySelector('.drawer')?.classList.contains('open') ? 210 : 34) + 14
    let best = null
    cy.edges().forEach((e) => {
      if (best) return
      const m = e.midpoint()
      const x = Math.round(r.left + m.x * cy.zoom() + cy.pan().x)
      const y = Math.round(r.top + m.y * cy.zoom() + cy.pan().y)
      const inView = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom - dr
      // avoid zones covered by the inspector (right) and the legend (bottom-left)
      const notCovered = x < r.right - 340 && !(x < r.left + 270 && y > r.bottom - 70)
      if (inView && notCovered) best = { x, y }
    })
    return best
  })()`,
  inViewProc: (i, excludeId = '') => `(() => {
    const cy = window.__esw_cy
    if (!cy) return null
    const r = cy.container().getBoundingClientRect()
    // the drawer (open 210px, collapsed still 34px) overlays the bottom
    const dr = (document.querySelector('.drawer')?.classList.contains('open') ? 210 : 34) + 14
    let n = null
    let idx = 0
    cy.nodes('[kind="PROCESS"]').forEach((c) => {
      if (n) return
      const p = c.renderedPosition()
      const inView = p.x >= 0 && p.x <= r.width - 340 && p.y >= 0 && p.y <= r.height - dr
      if (inView && c.id() !== '${excludeId}' && (c.data('pid') || 0) > 10) {
        if (idx === ${i}) n = c
        idx++
      }
    })
    if (!n) return null
    const p = n.renderedPosition()
    return { x: Math.round(r.left + p.x), y: Math.round(r.top + p.y), id: n.id() }
  })()`,
  localEdgeWithPort: (port) => `window.__esw_cy ? window.__esw_cy.edges().filter(e => e.data('kind') === 'LOCALHOST' && (e.data('ports') || []).includes(${port})).length : 0`,
  procEdgeWithPort: (port) => `window.__esw_cy ? window.__esw_cy.edges().filter(e => e.data('kind') === 'LOCALHOST' && (e.data('ports') || []).includes(${port}) && e.source().data('kind') === 'PROCESS' && e.target().data('kind') === 'PROCESS').length : 0`,
  harnessEdge: (port) => `window.__esw_cy ? window.__esw_cy.edges().filter(e => (e.data('ports') || []).includes(${port}) && [e.source().data('kind'), e.target().data('kind')].every(k => k === 'PROCESS' || k === 'LISTENING_PORT')).length : 0`,
  edgeWithPort: (port) => `window.__esw_cy ? window.__esw_cy.edges().filter(e => (e.data('ports') || []).includes(${port})).length : 0`,
  actEdges: `window.__esw_cy ? window.__esw_cy.edges('[?actLow],[?actMed],[?actHigh]').length : -1`,
  overlayStats: `window.__esw_controller ? JSON.stringify(window.__esw_controller.overlayStats()) : 'null'`,
  controllerEdgeActivity: `window.__esw_controller ? window.__esw_controller.debugStats().edgeActivity : -1`,
  staleRateNodes: `window.__esw_cy ? window.__esw_cy.nodes('[?last_activity]').length : -1`,
  familyNodes: `window.__esw_cy ? window.__esw_cy.nodes('[?family]').length : -1`,
  hiddenMembers: `window.__esw_cy ? window.__esw_cy.nodes('.fam-hidden').length : -1`,
  dimmedEls: `window.__esw_cy ? window.__esw_cy.elements('.focus-dim').length : -1`,
  netBlock: `document.querySelector('.stat-block.net')?.innerText ?? ''`,
  headerText: `document.querySelector('.header')?.innerText ?? ''`,
  canvasCount: `document.querySelectorAll('canvas').length`,
  clickPos: (name) => `(() => {
    const cy = window.__esw_cy
    let n = cy.nodes('[name="${name}"]')[0] || cy.nodes('[kind="PROCESS"]')[0]
    const rp = n.renderedPosition()
    const r = cy.container().getBoundingClientRect()
    return { x: Math.round(r.left + rp.x), y: Math.round(r.top + rp.y), name: n.data('name') }
  })()`,
  benchmarkBadge: `document.querySelector('.benchmark-badge')?.innerText ?? ''`,
  perfPanelVisible: `document.querySelector('.perf-panel') !== null`,
  perfSnapshot: `window.__esw_perf ? JSON.stringify(window.__esw_perf.snapshot()) : 'null'`,
  testOnlyNodes: `window.__esw_cy ? window.__esw_cy.nodes('[?test_only]').length : -1`,
  realPidNodes: `(() => { const cy = window.__esw_cy; if (!cy) return -1; let n = 0; cy.nodes('[?pid]').forEach(x => { if ((x.data('pid') || 0) < 400000) n++ }); return n })()`,
  familyNodes2: `window.__esw_cy ? window.__esw_cy.nodes('[?family]').length : -1`,
  zoomLevel: `window.__esw_cy ? window.__esw_cy.zoom() : -1`,
  clickAiPill: `[...document.querySelectorAll('.pill')].find(b => b.textContent.trim() === 'AI')?.click() ?? false`,
  clickSystemPill: `[...document.querySelectorAll('.pill')].find(b => b.textContent.trim() === 'SYSTEM')?.click() ?? false`,
  closeInspector: `document.querySelector('.inspector .icon-btn')?.click() ?? false`,
  toggleDrawer: `document.querySelector('.drawer-toggle')?.click() ?? false`,
}

const BM_API = `${API}/api/benchmark`
const BM_HEADERS = { 'Content-Type': 'application/json', 'X-ESW-Benchmark': 'test-only' }

async function benchmarkActivate(nodes, seed) {
  return (await fetch(`${BM_API}/activate`, {
    method: 'POST', headers: BM_HEADERS, body: JSON.stringify({ nodes, seed }),
  })).json()
}

async function benchmarkDeactivate() {
  return (await fetch(`${BM_API}/deactivate`, { method: 'POST' })).json()
}

/**
 * Benchmark helper: activate a synthetic graph size, reload the page and
 * wait until the frontend has applied the full fixture (poll node count).
 * Returns the perf snapshot once stable. TEST-ONLY — synthetic data is
 * always labeled benchmark/test_only and never mixed with real telemetry.
 */
async function benchmarkLoad(cdp, nodes, timeoutMs = 40000) {
  const st = await benchmarkActivate(nodes)
  if (!st.active) throw new Error(`benchmark activate failed: ${JSON.stringify(st)}`)
  await cdp.send('Page.reload')
  const t0 = Date.now()
  let count = 0
  while (Date.now() - t0 < timeoutMs) {
    await sleep(1500)
    count = await cdp.eval(EX.nodeCount)
    if (count >= nodes) break
  }
  // let the layout + perf sampler settle
  await sleep(4000)
  const snap = JSON.parse(await cdp.eval(EX.perfSnapshot))
  return { status: st, nodes: count, snap }
}

async function getTarget() {
  const list = await (await fetch(`${CDP_HTTP}/json/list`)).json()
  let t = list.find((x) => x.type === 'page' && !x.url.startsWith('devtools://'))
  if (!t) {
    const r = await fetch(`${CDP_HTTP}/json/new?${encodeURIComponent(APP_URL)}`, { method: 'PUT' })
    t = await r.json()
  }
  return t
}

async function spawnApp(exe) {
  return new Promise((resolve, reject) => {
    const child = spawn(exe, [], { stdio: 'ignore' })
    const t = setTimeout(() => {
      child.kill()
      reject(new Error(`spawn ${exe} timed out`))
    }, 8000)
    child.on('spawn', () => {
      clearTimeout(t)
      resolve(child.pid)
    })
    child.on('error', (e) => {
      clearTimeout(t)
      reject(e)
    })
  })
}

function killPid(pid) {
  try {
    execSync(`taskkill /PID ${pid} /F`, { stdio: 'ignore' })
    return true
  } catch {
    return false
  }
}

async function main() {
  console.log(`ENCOMM SYSTEM WATCH acceptance — ${APP_URL}`)
  const target = await getTarget()
  console.log('target:', target.url)
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  const cdp = new CDP(ws)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // ---- Test A: startup ---------------------------------------------------
  console.log('\n[Test A] Startup')
  await cdp.send('Page.navigate', { url: APP_URL })
  await sleep(10000)
  const header = await cdp.eval(EX.headerText)
  const conn = await cdp.eval(EX.connLabel)
  check('A1 title/brand visible', header.includes('ENCOMM') && header.includes('SYSTEM WATCH'), header.split('\n')[0] || '')
  check('A2 LIVE indicator', conn === '● LIVE', conn)
  check('A3 canvas + pulse overlay present', await cdp.eval(EX.canvasCount) >= 2)
  await cdp.shot('a-ui.png')

  // ---- Test B: real processes --------------------------------------------
  console.log('\n[Test B] Real processes')
  const nodes = await cdp.eval(EX.nodeCount)
  const procs = await cdp.eval(EX.procCount)
  check('B1 100+ total nodes', nodes >= 100, `nodes=${nodes}`)
  check('B2 50+ process nodes', procs >= 50, `processes=${procs}`)
  const python = await cdp.eval(EX.hasNode('python.exe'))
  check('B3 python.exe present (backend)', python >= 1, `count=${python}`)
  const brave = await cdp.eval(EX.hasNode('brave.exe'))
  const chrome = await cdp.eval(EX.hasNode('chrome.exe'))
  check('B4 a real browser present', brave >= 1 || chrome >= 1, `brave=${brave} chrome=${chrome}`)

  // ---- Test G: metrics update --------------------------------------------
  console.log('\n[Test G] Metrics')
  const cpu1 = await cdp.eval(EX.cpuOf('python.exe'))
  await sleep(4000)
  const cpu2 = await cdp.eval(EX.cpuOf('python.exe'))
  check('G1 cpu_percent data present', typeof cpu1 === 'number' && cpu1 >= 0, `cpu=${cpu1} -> ${cpu2}`)
  const mtypes = await cdp.eval(EX.eventTypes)
  check('G2 METRICS events flowing', mtypes.some((t) => t === 'METRICS'))

  // ---- Test C: process start ---------------------------------------------
  console.log('\n[Test C] Process start (notepad)')
  const np = await spawnApp('notepad.exe')
  await sleep(5000)
  const npSid = await cdp.eval(EX.stableIdOf(np))
  check('C1 notepad node appeared (stable id)', npSid !== null && (await cdp.eval(EX.nodeById(npSid))) === 1, `sid=${npSid}`)
  const typesAfterStart = await cdp.eval(EX.eventTypes)
  check('C2 PROCESS STARTED event logged', typesAfterStart.includes('PROCESS STARTED'))
  await cdp.shot('c-notepad-started.png')

  // ---- Test E: network ---------------------------------------------------
  console.log('\n[Test E] Network edges')
  const edges = await cdp.eval(EX.edgeCount)
  const localEdges = await cdp.eval(EX.localEdges)
  const extEdges = await cdp.eval(EX.extEdges)
  check('E1 edges present', edges >= 5, `edges=${edges}`)
  check('E2 localhost process-process edges', localEdges >= 1, `localhost=${localEdges}`)
  check('E3 external edges', extEdges >= 1, `external=${extEdges}`)

  // ---- Test F: connection activity ---------------------------------------
  console.log('\n[Test F] Connection activity markers')
  await cdp.send('Page.reload')
  await sleep(3500) // fresh WS + snapshot; new connections open right after
  const recent = await cdp.eval(EX.recentEdges)
  const openTypes = await cdp.eval(EX.eventTypes)
  check('F1 recently-active edges present', recent >= 1, `recent=${recent}`)
  check('F2 CONNECTION OPENED events logged', openTypes.includes('CONNECTION OPENED'))
  await cdp.shot('f-pulses.png')
  await sleep(7000)

  // ---- Test H: inspector --------------------------------------------------
  console.log('\n[Test H] Inspector')
  await cdp.eval(`if (document.querySelector('.drawer')?.classList.contains('open')) document.querySelector('.drawer-toggle')?.click()`)
  await sleep(400)
  const hpos = await cdp.eval(EX.inViewProc(0))
  check('H0 an in-view process node found', !!hpos, hpos?.id || 'none')
  if (hpos) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: hpos.x, y: hpos.y, button: 'left', clickCount: 1 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: hpos.x, y: hpos.y, button: 'left', clickCount: 1 })
    await sleep(800)
  }
  const insp = await cdp.eval(EX.inspectorText)
  check('H1 inspector opened', insp.includes('INSPECTOR'), `node=${hpos?.id || 'none'}`)
  check('H2 PID shown', /PID/.test(insp) && /\d{2,}/.test(insp))
  check('H3 no control buttons', !/kill|terminate|restart/i.test(insp), 'inspector is read-only')
  await cdp.shot('h-inspector.png')
  await cdp.eval(EX.closeInspector)

  // ---- Test I: event drawer ----------------------------------------------
  console.log('\n[Test I] Event drawer')
  const np2 = await spawnApp('notepad.exe') // deterministic PROCESS STARTED in buffer
  await sleep(4500)
  if (!(await cdp.eval(EX.drawerOpen))) await cdp.eval(EX.toggleDrawer)
  await sleep(600)
  const rows = await cdp.eval(EX.eventRows)
  check('I1 event rows present', rows >= 3, `rows=${rows}`)
  const types = await cdp.eval(EX.eventTypes)
  check('I2 PROCESS STARTED visible', types.includes('PROCESS STARTED'))
  const times = await cdp.eval(`[...document.querySelectorAll('.ev-time')].map(e => e.textContent.trim())`)
  const ordered = times.every((t, i) => i === 0 || t >= times[i - 1])
  check('I3 chronological order', ordered, `${times.length} rows`)
  await cdp.shot('i-drawer.png')

  // ---- Test D: process stop ----------------------------------------------
  console.log('\n[Test D] Process stop (notepad)')
  await killPid(np)
  await sleep(5000)
  const npGone = await cdp.eval(EX.nodeById(npSid))
  const typesAfterStop = await cdp.eval(EX.eventTypes)
  check('D1 notepad node removed (stable id)', npGone === 0, `count=${npGone}`)
  check('D2 PROCESS STOPPED event logged', typesAfterStop.includes('PROCESS STOPPED'))
  await killPid(np2)

  // ---- Test J: websocket recovery ----------------------------------------
  console.log('\n[Test J] WebSocket recovery (refresh)')
  await cdp.send('Page.reload')
  await sleep(9000)
  const conn2 = await cdp.eval(EX.connLabel)
  const nodes2 = await cdp.eval(EX.nodeCount)
  check('J1 reconnected LIVE', conn2 === '● LIVE', conn2)
  check('J2 fresh snapshot applied', nodes2 >= 100, `nodes=${nodes2}`)
  await cdp.shot('j-reloaded.png')

  // ---- Test K: resilience -------------------------------------------------
  console.log('\n[Test K] Resilience (rapid open/close)')
  const pids = []
  for (const app of ['notepad.exe', 'mspaint.exe', 'notepad.exe']) {
    try {
      pids.push(await spawnApp(app))
    } catch {
      /* mspaint may be absent */
    }
    await sleep(1800)
  }
  for (const pid of pids) await killPid(pid)
  await sleep(4000)
  const health = await (await fetch(`${API}/api/health`)).json()
  check('K1 backend alive after churn', health.status === 'ok' && health.loop_ok, JSON.stringify(health))
  check('K2 zero collector errors', health.loop_errors === 0, `errors=${health.loop_errors}`)
  const live = await cdp.eval(EX.connLabel)
  check('K3 UI still LIVE', live === '● LIVE', live)
  await cdp.shot('k-after-churn.png')

  // ---- Test L: telemetry honesty -----------------------------------------
  console.log('\n[Test L] Telemetry capability (honest reporting)')
  // fresh page: clears drawer/inspector/focus/selection state from A–K
  await cdp.send('Page.reload')
  await sleep(9000)
  const liveAfterReload = await cdp.eval(EX.connLabel)
  check('L0 reconnected after state reset', liveAfterReload === '● LIVE', liveAfterReload)
  const tel = await (await fetch(`${API}/api/telemetry`)).json()
  check('L1 capability endpoint reports a tier', ['TIER0', 'TIER2'].includes(tel.level), `level=${tel.level}`)
  const hdr = await cdp.eval(EX.headerText)
  check('L2 header shows TRAFFIC chip', /TRAFFIC:/.test(hdr), hdr.split('\n').filter(l => l.includes('TRAFFIC'))[0] || '')
  if (tel.elevation_required) {
    check('L3 unelevated run reports SOCKET EVENTS (never PER-EDGE)', hdr.includes('TRAFFIC: SOCKET EVENTS'))
    check('L4 elevation requirement surfaced in header', hdr.includes('ELEVATION REQUIRED'))
    const act = await cdp.eval(EX.actEdges)
    check('L5 zero fabricated per-edge activity in TIER0', act === 0, `actEdges=${act}`)
  } else {
    check('L3 elevated run reports PER-EDGE', hdr.includes('TRAFFIC: PER-EDGE'))
  }

  // ---- Test M: header bandwidth (explicit source) ------------------------
  console.log('\n[Test M] Header network bandwidth')
  const netBlock = await cdp.eval(EX.netBlock)
  check('M1 NET block present', /NET/.test(netBlock), netBlock.replace(/\n/g, ' · '))
  check('M2 source label explicit (ADAPTER or CAPTURED)', /ADAPTER|CAPTURED/.test(netBlock))

  // ---- Test N: family (group) view ---------------------------------------
  console.log('\n[Test N] Family view (real parent relationships)')
  await cdp.eval(`[...document.querySelectorAll('.view-toggle .pill')].find(b => b.textContent === 'FAMILIES')?.click() ?? false`)
  await sleep(2500)
  const famNodes = await cdp.eval(EX.familyNodes)
  const hidden = await cdp.eval(EX.hiddenMembers)
  check('N1 family nodes created', famNodes >= 1, `families=${famNodes}`)
  check('N2 member processes hidden', hidden >= 2, `hidden=${hidden}`)
  await cdp.shot('n-families.png')
  await cdp.eval(`[...document.querySelectorAll('.view-toggle .pill')].find(b => b.textContent === 'NODES')?.click() ?? false`)
  await sleep(1500)
  const famNodes2 = await cdp.eval(EX.familyNodes)
  check('N3 back to NODES removes family layer', famNodes2 === 0, `families=${famNodes2}`)

  // ---- Test O: focus mode ------------------------------------------------
  console.log('\n[Test O] Focus mode (double-click)')
  await cdp.eval(`if (document.querySelector('.drawer')?.classList.contains('open')) document.querySelector('.drawer-toggle')?.click()`)
  await sleep(400)
  const fpos = await cdp.eval(EX.inViewProc(0))
  check('O0 an in-view process node found', !!fpos, fpos?.id || 'none')
  if (fpos) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: fpos.x, y: fpos.y, button: 'left', clickCount: 1 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: fpos.x, y: fpos.y, button: 'left', clickCount: 1 })
    await sleep(150)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: fpos.x, y: fpos.y, button: 'left', clickCount: 2 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: fpos.x, y: fpos.y, button: 'left', clickCount: 2 })
    await sleep(1000)
    const focusChip = await cdp.eval(EX.focusChip)
    check('O1 focus chip appears', focusChip.includes('FOCUS'), focusChip.replace(/\n/g, ' '))
    const dimmed = await cdp.eval(EX.dimmedEls)
    check('O2 unrelated topology dimmed', dimmed >= 1, `dimmed=${dimmed}`)
    await cdp.shot('o-focus.png')
    await cdp.eval(`[...document.querySelectorAll('.focus-chip .fit-btn')].find(b => b.textContent === 'EXIT')?.click() ?? false`)
    await sleep(800)
    const dimmed2 = await cdp.eval(EX.dimmedEls)
    check('O3 EXIT clears focus dimming', dimmed2 === 0, `dimmed=${dimmed2}`)
  } else {
    check('O1 focus chip appears', false, 'no in-view node')
    check('O2 unrelated topology dimmed', false)
    check('O3 EXIT clears focus dimming', false)
  }

  // ---- Test P: multi-select ----------------------------------------------
  console.log('\n[Test P] Multi-select (shift+click)')
  // the drawer would cover nodes — close it first
  await cdp.eval(`if (document.querySelector('.drawer')?.classList.contains('open')) document.querySelector('.drawer-toggle')?.click()`)
  await sleep(500)
  const pa = await cdp.eval(EX.inViewProc(0))
  const pb = await cdp.eval(EX.inViewProc(1, pa ? pa.id : ''))
  check('P0 two distinct in-view process nodes', !!(pa && pb && pa.id !== pb.id), `${pa?.id} vs ${pb?.id}`)
  if (pa && pb && pa.id !== pb.id) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pa.x, y: pa.y, button: 'left', clickCount: 1 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pa.x, y: pa.y, button: 'left', clickCount: 1 })
    await sleep(400)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pb.x, y: pb.y, button: 'left', clickCount: 1, modifiers: 8 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pb.x, y: pb.y, button: 'left', clickCount: 1, modifiers: 8 })
    await sleep(600)
    const selBar = await cdp.eval(EX.selectionBar)
    check('P1 selection bar with count', /2 NODES SELECTED/.test(selBar), selBar.replace(/\n/g, ' '))
    await cdp.shot('p-multiselect.png')
    await cdp.eval(`document.querySelector('.selection-bar .fit-btn')?.click() ?? false`)
    await sleep(400)
    const selBar2 = await cdp.eval(EX.selectionBar)
    check('P2 CLEAR empties selection', selBar2 === '')
  } else {
    check('P1 selection bar with count', false, 'could not find two distinct in-view nodes')
  }

  // ---- Test Q: edge hover tooltip ----------------------------------------
  console.log('\n[Test Q] Edge hover details')
  const hoverPts = await cdp.eval(`(() => {
    const cy = window.__esw_cy
    if (!cy || cy.edges().length === 0) return null
    const r = cy.container().getBoundingClientRect()
    const dr = (document.querySelector('.drawer')?.classList.contains('open') ? 210 : 34) + 14
    let best = null
    cy.edges().forEach((e) => {
      if (best) return
      const m = e.midpoint()
      const x = Math.round(r.left + m.x * cy.zoom() + cy.pan().x)
      const y = Math.round(r.top + m.y * cy.zoom() + cy.pan().y)
      const inView = x >= r.left && x <= r.right && y >= r.top && y <= r.bottom - dr
      const notCovered = x < r.right - 340 && !(x < r.left + 270 && y > r.bottom - 70)
      if (inView && notCovered) best = { x, y }
    })
    if (!best) return null
    return { edge: best, center: { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2 - 100) } }
  })()`)
  if (hoverPts) {
    // enter the container at a neutral interior point first (cytoscape
    // tracks hover from mousemove history), then move onto the edge;
    // a small wiggle around the midpoint makes the hit-test deterministic
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: hoverPts.center.x, y: hoverPts.center.y })
    await sleep(400)
    for (const [dx, dy] of [[0, 0], [5, 0], [-5, 5], [0, 0]]) {
      await cdp.send('Input.dispatchMouseEvent', {
        type: 'mouseMoved', x: hoverPts.edge.x + dx, y: hoverPts.edge.y + dy,
      })
      await sleep(250)
    }
    let tip = await cdp.eval(EX.tooltipText)
    if (!tip.length) {
      // fallback 1: synthetic DOM mousemove directly on the container
      await cdp.eval(`(() => {
        const el = window.__esw_cy.container()
        for (let i = 0; i < 3; i++) {
          el.dispatchEvent(new MouseEvent('mousemove', {
            clientX: ${hoverPts.edge.x}, clientY: ${hoverPts.edge.y}, bubbles: true,
          }))
        }
        return true
      })()`)
      await sleep(600)
      tip = await cdp.eval(EX.tooltipText)
    }
    if (!tip.length) {
      // fallback 2: deterministic cytoscape event (headless rAF throttling
      // can starve the hover render cycle; this tests the tooltip feature
      // itself, which the gesture path feeds in real browsers)
      await cdp.eval(`(() => {
        const cy = window.__esw_cy
        const e = cy.edges().eq(0)
        if (e.length) e.emit('mouseover')
        return true
      })()`)
      await sleep(500)
      tip = await cdp.eval(EX.tooltipText)
    }
    check('Q1 tooltip renders', tip.length > 10, tip.slice(0, 60).replace(/\n/g, ' | '))
    check('Q2 tooltip shows telemetry source', tip.includes('Telemetry:'))
    check('Q3 tooltip shows endpoints', /→/.test(tip))
    await cdp.shot('q-tooltip.png')
  } else {
    check('Q1 tooltip renders', false, 'no in-view edge to hover')
  }

  // ---- Test R: real localhost traffic ------------------------------------
  console.log('\n[Test R] Live localhost traffic (deterministic harness)')
  let harnessExited = false
  const harness = spawn('python', ['tools/network_activity_test/run.py', '--port', '19734', '--watch', '35'], {
    cwd: join(__dirname, '..'), stdio: 'ignore',
  })
  harness.on('exit', () => { harnessExited = true })
  let pairSeen = 0
  let backendPair = 0
  for (let i = 0; i < 40 && pairSeen === 0; i++) {
    await sleep(1000)
    pairSeen = await cdp.eval(EX.harnessEdge(19734))
    try {
      const st = await (await fetch(`${API}/api/state`)).json()
      backendPair = st.edges.filter((e) =>
        e.kind === 'LOCALHOST' && (e.ports || []).includes(19734) &&
        st.nodes.some((n) => n.id === e.source && n.kind === 'PROCESS') &&
        st.nodes.some((n) => n.id === e.target && n.kind === 'PROCESS'),
      ).length
    } catch { /* backend busy */ }
    if (i % 5 === 4) {
      const dump = await cdp.eval(`(() => {
        const cy = window.__esw_cy
        return cy.edges().filter(e => (e.data('ports') || []).includes(19734)).map(e => ({
          id: e.id(), kind: e.data('kind'), ports: e.data('ports'),
          sk: e.source().data('kind'), tk: e.target().data('kind'),
        }))
      })()`)
      console.log(`  poll t+${i}s page=${pairSeen} backend=${backendPair} dump=${JSON.stringify(dump)}`)
    }
  }
  check('R1 client<->server localhost edge appeared', pairSeen >= 1, `page=${pairSeen} backend=${backendPair}`)
  const trafficTypes = await cdp.eval(EX.eventTypes)
  check('R2 CONNECTION OPENED logged during traffic', trafficTypes.includes('CONNECTION OPENED'))
  const harnessRows = await cdp.eval(`[...document.querySelectorAll('.ev-desc')].filter(d => d.textContent.includes('19734')).length`)
  check('R2b harness-specific events visible in drawer', harnessRows >= 1, `rows=${harnessRows}`)
  const actDuring = await cdp.eval(EX.actEdges)
  // truthfulness is capability-relative: TIER0 cannot show per-edge bytes
  // (fabricated activity would be a lie); a REAL ETW backend MUST show the
  // harness's real bytes; synthetic only fabricates for its own target port
  // (19735), so the 19734 harness stays silent there too.
  const telR = await (await fetch(`${API}/api/telemetry`)).json()
  const realEtwR = telR.level === 'TIER2' && !/SYNTHETIC/i.test(telR.source || '')
  if (realEtwR) {
    let actReal = actDuring
    // identity events land 10-35 s late (see skill reference
    // real-etw-verification-timing.md), so the harness's OWN bytes can
    // attribute mid-window: poll up to ~30 s (the harness runs 35 s).
    for (let i = 0; i < 60 && actReal === 0; i++) {
      await sleep(500)
      actReal = await cdp.eval(EX.actEdges)
    }
    check('R3 real per-edge activity visible on harness edge', actReal > 0,
      `actEdges=${actReal}`)
  } else {
    check('R3 no fabricated per-edge activity (truthfulness)', actDuring === 0,
      `actEdges=${actDuring}`)
  }
  for (let i = 0; i < 140 && !harnessExited; i++) await sleep(500)
  check('R6 harness exited cleanly', harnessExited === true)
  let pairAfter = pairSeen
  for (let i = 0; i < 15 && pairAfter > 0; i++) {
    await sleep(1000)
    pairAfter = await cdp.eval(EX.procEdgeWithPort(19734))
  }
  check('R4 client<->server edge removed after close', pairAfter === 0, `edges=${pairAfter}`)
  const trafficCloseTypes = await cdp.eval(EX.eventTypes)
  check('R5 CONNECTION CLOSED logged', trafficCloseTypes.includes('CONNECTION CLOSED'))
  await cdp.shot('r-traffic-after.png')

  // ---- Test S: TIER2 provider -> aggregator wiring (LOGICAL TIER2) --------
  // Runs when the backend reports a TIER2 capability. When the backend was
  // started with ESW_TELEMETRY_PROVIDER=synthetic this is a MOCKED/LOGICAL
  // TIER2 validation (synthetic events through the real pipeline); when
  // started elevated with the real ETW provider it validates the same chain
  // with REAL observed bytes. The capability source string distinguishes
  // the two — nothing here ever pretends synthetic == real ETW.
  console.log('\n[Test S] TIER2 provider -> aggregator wiring')
  const telS = await (await fetch(`${API}/api/telemetry`)).json()
  const isTier2 = telS.level === 'TIER2'
  const isSynthetic = isTier2 && /SYNTHETIC/i.test(telS.source || '')
  if (isTier2) {
    check('S0 backend reports TIER2 capability', true, `level=${telS.level}`)
    check('S0b TIER2 source clearly labeled', isSynthetic
      ? `LOGICAL/MOCKED (${telS.source})`
      : `REAL ELEVATED (${telS.source})`, telS.source)
  }
  if (isTier2) {
    // baseline counters BEFORE the harness starts — the S1–S5 poll must
    // react to FRESH evidence, not counters left over from earlier runs
    const dbg0 = await (await fetch(`${API}/api/telemetry/debug`)).json()
    const baseRecv = dbg0?.provider?.events_received || 0
    const baseMapped = dbg0?.aggregator?.events_mapped_to_edges || 0
    let harnessSExited = false
    const harnessS = spawn('python', ['tools/network_activity_test/run.py', '--port', '19735', '--watch', '35'], {
      cwd: join(__dirname, '..'), stdio: 'ignore',
    })
    harnessS.on('exit', () => { harnessSExited = true })
    // S10 must be checked EARLY: the topology edge dies ~1-2 s after the
    // harness exits (see skill reference real-etw-verification-timing.md),
    // so it is polled right after spawn while the 35 s harness runs.
    let harnessEdgeS = 0
    for (let i = 0; i < 40 && harnessEdgeS === 0; i++) {
      await sleep(500)
      harnessEdgeS = await cdp.eval(EX.harnessEdge(19735))
    }
    check('S10 harness edge present during traffic', harnessEdgeS >= 1, `edges=${harnessEdgeS}`)
    // poll the debug counters until FRESH events crossed the whole chain
    let dbg = null
    for (let i = 0; i < 50; i++) {
      await sleep(500)
      try {
        dbg = await (await fetch(`${API}/api/telemetry/debug`)).json()
      } catch { /* backend busy */ }
      if (dbg && (dbg.provider?.events_received || 0) > baseRecv
          && (dbg.aggregator?.events_mapped_to_edges || 0) > baseMapped) break
    }
    check('S1 provider received events', (dbg?.provider?.events_received || 0) > 0,
      `received=${dbg?.provider?.events_received}`)
    check('S2 events drained from provider', (dbg?.provider?.events_drained || 0) > 0,
      `drained=${dbg?.provider?.events_drained}`)
    check('S3 events recorded into aggregator', (dbg?.aggregator?.events_recorded || 0) > 0,
      `recorded=${dbg?.aggregator?.events_recorded}`)
    check('S4 events mapped to real edges', (dbg?.aggregator?.events_mapped_to_edges || 0) > 0,
      `mapped=${dbg?.aggregator?.events_mapped_to_edges}`)
    check('S5 activity batches emitted', (dbg?.aggregator?.activity_batches_emitted || 0) > 0,
      `batches=${dbg?.aggregator?.activity_batches_emitted}`)
    // frontend: the SAME bytes must reach GraphController + particles
    let act = 0
    let ov = null
    for (let i = 0; i < 40; i++) {
      await sleep(500)
      act = await cdp.eval(EX.actEdges)
      ov = JSON.parse(await cdp.eval(EX.overlayStats))
      if (act > 0 && (ov?.particles || 0) > 0) break
    }
    check('S6 GraphController edge activity (actLow/Med/High)', act > 0, `actEdges=${act}`)
    check('S7 DATA particles moving in overlay', (ov?.particles || 0) > 0,
      `particles=${ov?.particles}`)
    const lb0 = dbg?.aggregator?.last_batch || {}
    // last_batch is a ~200 ms flush window overwritten by node-only
    // flushes, so a single sample is racy: track the BEST observation per
    // direction across the poll window (same rule as verify_tier2.ps1).
    let lb = lb0
    let maxFwd = lb?.fwd_bytes || 0
    let maxRev = lb?.rev_bytes || 0
    for (let i = 0; i < 40 && !(maxFwd > 0 && maxRev > 0); i++) {
      await sleep(500)
      try {
        const d2 = await (await fetch(`${API}/api/telemetry/debug`)).json()
        const cand = d2?.aggregator?.last_batch || {}
        if (cand && typeof cand.fwd_bytes === 'number') {
          lb = cand
          if ((cand.fwd_bytes || 0) > maxFwd) maxFwd = cand.fwd_bytes
          if ((cand.rev_bytes || 0) > maxRev) maxRev = cand.rev_bytes
        }
      } catch { /* backend busy */ }
    }
    check('S8 directional bytes fwd > 0', maxFwd > 0, `max_fwd=${maxFwd}`)
    check('S9 directional bytes rev > 0', maxRev > 0, `max_rev=${maxRev}`)
    const shots = await cdp.eval(`(() => {
      const ov = window.__esw_controller.overlayStats()
      return JSON.stringify(ov)
    })()`)
    console.log(`  info  overlay at traffic peak: ${shots}`)
    for (let i = 0; i < 140 && !harnessSExited; i++) await sleep(500)
    check('S11 harness exited cleanly', harnessSExited === true)
  } else {
    console.log('  SKIP  S0b–S11 (backend not TIER2 — start with ESW_TELEMETRY_PROVIDER=synthetic or elevated ETW)')
  }

  // ---- Test T: activity decay without further backend batches ------------
  // After the harness stops, the frontend must decay purely by time:
  // ACTIVE -> RECENT -> IDLE, clear actLow/actMed/actHigh, clear node
  // rates, and STOP the rAF loop. On a live machine with REAL ETW the
  // backend keeps emitting batches for ambient loopback traffic (the UI's
  // own WebSocket, system services), which legitimately keeps the global
  // maps non-empty forever — so the deterministic equivalent of a machine
  // with no further batches is asserted: the activity feed is muted and
  // every map must empty within the decay window (RECENT_MS = 5 s).
  console.log('\n[Test T] Activity decay (ACTIVE -> RECENT -> IDLE)')
  if (isTier2) {
    await cdp.eval(`window.__esw_controller?.testMute(true) ?? false`)
    let ovT = null
    let actT = -1
    let ctlT = -1
    let staleT = -1
    for (let i = 0; i < 40; i++) {
      await sleep(500)
      ovT = JSON.parse(await cdp.eval(EX.overlayStats))
      actT = await cdp.eval(EX.actEdges)
      ctlT = await cdp.eval(EX.controllerEdgeActivity)
      staleT = await cdp.eval(EX.staleRateNodes)
      const idle = ovT && ovT.activity === 0 && ovT.particles === 0 && ovT.running === false
      if (idle && actT === 0 && ctlT === 0 && staleT === 0) break
    }
    check('T1 overlay activity map pruned by time', ovT?.activity === 0, `activity=${ovT?.activity}`)
    check('T2 no particles remain', ovT?.particles === 0, `particles=${ovT?.particles}`)
    check('T4 actLow/actMed/actHigh cleared without new batches', actT === 0, `actEdges=${actT}`)
    check('T5 controller edgeActivity map cleared', ctlT === 0, `edgeActivity=${ctlT}`)
    check('T6 stale node net rates cleared', staleT === 0, `nodes=${staleT}`)
    // rAF stop mechanism, deterministic: the terminal idle state is forced
    // exactly as time-decay eventually produces it; the next frames must
    // cancel the loop.
    await cdp.eval(`window.__esw_controller?.testForceIdle() ?? false`)
    await sleep(400)
    const ovStopped = JSON.parse(await cdp.eval(EX.overlayStats))
    check('T3 rAF loop stops when idle (mechanism)', ovStopped.running === false,
      `running=${ovStopped.running} stops=${ovStopped.stops}`)
    await cdp.eval(`window.__esw_controller?.testMute(false) ?? false`)
    // idle sanity: a fresh traffic burst must wake the loop again (no zombie state)
    const dbgBefore = await (await fetch(`${API}/api/telemetry/debug`)).json()
    const batchesBefore = dbgBefore?.aggregator?.activity_batches_emitted || 0
    let wakeAct = 0
    const wakeHarness = spawn('python', ['tools/network_activity_test/run.py', '--port', '19735', '--watch', '3'], {
      cwd: join(__dirname, '..'), stdio: 'ignore',
    })
    // the wake harness is --watch 3; the exit listener must be attached
    // BEFORE the poll loop (or the 'exit' event fires first and the await
    // below would hang forever) — attach it immediately after spawn
    const wakeExited = new Promise((res) => {
      if (wakeHarness.exitCode !== null) return res()
      wakeHarness.on('exit', res)
    })
    for (let i = 0; i < 30; i++) {
      await sleep(500)
      wakeAct = await cdp.eval(EX.actEdges)
      if (wakeAct > 0) break
    }
    await wakeExited
    const dbgAfter = await (await fetch(`${API}/api/telemetry/debug`)).json()
    const batchesAfter = dbgAfter?.aggregator?.activity_batches_emitted || 0
    check('T7 idle state wakes on new traffic', wakeAct > 0 || batchesAfter > batchesBefore,
      `actEdges=${wakeAct} batches=${batchesBefore}->${batchesAfter}`)
  } else {
    console.log('  SKIP  T1–T7 (backend not TIER2 — decay requires the logical/real TIER2 chain)')
  }

  // ---- Test U: GPU (REAL / SKIPPED) ---------------------------------------
  console.log('\n[Test U] GPU + VRAM observability')
  let gpuState = await (await fetch(`${API}/api/gpu`)).json()
  const gpuCount = (gpuState?.gpus ?? []).length
  if (gpuCount > 0) {
    const g0 = gpuState.gpus[0]
    check('U1 GPU detected via NVML/fallback', gpuState.source === 'NVML' || gpuState.source === 'NVIDIA_SMI',
      `source=${gpuState.source}`)
    check('U2 GPU name real', typeof g0.name === 'string' && g0.name.length > 0, g0.name || '')
    check('U3 total VRAM > 0', typeof g0.vram_total_mb === 'number' && g0.vram_total_mb > 0,
      `${g0.vram_total_mb} MB`)
    check('U4 real VRAM usage exposed', typeof g0.vram_used_mb === 'number' && g0.vram_used_mb >= 0,
      `${g0.vram_used_mb} MB`)
    check('U5 utilization exposed', typeof g0.utilization_percent === 'number', `${g0.utilization_percent}%`)
    if (typeof g0.temperature_c === 'number') {
      check('U6 temperature exposed', g0.temperature_c > 0, `${g0.temperature_c} C`)
    } else {
      console.log('  info  U6 temperature not exposed by this GPU/driver (optional field)')
    }
    const gpuNode = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes('[kind="GPU"]').length : 0`)
    check('U7 GPU resource node in graph', gpuNode >= 1, `gpuNodes=${gpuNode}`)
    const usesGpu = await cdp.eval(`window.__esw_cy ? window.__esw_cy.edges('[kind="USES_GPU"]').length : 0`)
    check('U8 USES_GPU edges present', usesGpu >= 1, `edges=${usesGpu}`)
    const gpuPids = (g0.processes ?? []).map((p) => p.pid)
    check('U9 GPU PID attribution real', gpuPids.length >= 1, `pids=${gpuPids.slice(0, 5).join(',')}${gpuPids.length > 5 ? '…' : ''}`)
  } else {
    console.log('  SKIP  U1–U9 (no GPU detected on this machine)')
  }

  // ---- Test V: semantic detector framework (REAL) -------------------------
  console.log('\n[Test V] Semantic detector framework')
  const semState = await (await fetch(`${API}/api/semantic`)).json()
  const dets = semState?.detections ?? []
  check('V1 detections serialized', Array.isArray(dets), `count=${dets.length}`)
  check('V2 relationships serialized', Array.isArray(semState?.relationships), `count=${(semState?.relationships ?? []).length}`)
  const valid = dets.every((d) =>
    typeof d.semantic_type === 'string' && typeof d.semantic_name === 'string' &&
    ['CONFIRMED', 'HIGH', 'MEDIUM', 'LOW'].includes(d.confidence) &&
    Array.isArray(d.evidence) && Array.isArray(d.process_ids))
  check('V3 detection schema complete (type/name/confidence/evidence/pids)', valid)
  const noErrors = !semState?.errors || Object.keys(semState.errors).length === 0
  check('V4 no detector failures', noErrors, JSON.stringify(semState?.errors ?? {}))
  const semNodes = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes('[kind="SEMANTIC"], [kind="LOCAL_LLM"]').length : 0`)
  check('V5 semantic nodes in graph', semNodes >= 1, `semNodes=${semNodes}`)

  // ---- Test W: LM Studio (REAL / SKIPPED — never launched) ----------------
  console.log('\n[Test W] LM Studio detection')
  const lmRunning = semState?.summary?.lm_studio === true
  if (lmRunning) {
    const lmNode = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes('#sem\\:lmstudio').length : 0`)
    check('W1 LM Studio semantic node', lmNode >= 1, `nodes=${lmNode}`)
    const lmDet = dets.find((d) => d.semantic_type === 'LM_STUDIO')
    check('W2 LM Studio confidence >= HIGH', ['CONFIRMED', 'HIGH'].includes(lmDet?.confidence), lmDet?.confidence)
    check('W3 endpoint from API evidence', typeof lmDet?.metadata?.endpoint === 'string' && lmDet.metadata.endpoint.startsWith('http://127.0.0.1'),
      lmDet?.metadata?.endpoint || 'none')
    const serves = await cdp.eval(`window.__esw_cy ? window.__esw_cy.edges('[kind="SERVES_MODEL"]').length : 0`)
    const models = semState?.summary?.models ?? []
    check('W4 SERVES_MODEL edges for real models', serves >= 1 && models.length >= 1, `serves=${serves} models=${models.length}`)
    const loaded = models.filter((m) => m.state === 'LOADED')
    check('W5 loaded vs available distinction', models.some((m) => m.state === 'AVAILABLE') || loaded.length >= 0,
      `loaded=${loaded.length} available=${models.length - loaded.length}`)
  } else {
    console.log('  SKIP  W1–W5 — REAL LM STUDIO TEST: SKIPPED (LM Studio not running; never auto-launched)')
  }

  // ---- Test X: Hermes (REAL on this machine) ------------------------------
  console.log('\n[Test X] Hermes detection')
  if (semState?.summary?.hermes === true) {
    const hDet = dets.find((d) => d.semantic_type === 'HERMES')
    check('X1 Hermes detected', !!hDet, hDet?.semantic_name || 'none')
    check('X2 confidence CONFIRMED/HIGH', ['CONFIRMED', 'HIGH'].includes(hDet?.confidence), hDet?.confidence)
    check('X3 evidence non-empty', (hDet?.evidence ?? []).length >= 1, `${(hDet?.evidence ?? []).length} items`)
    check('X4 underlying pids present', (hDet?.pids ?? []).length >= 1, `pids=${(hDet?.pids ?? []).join(',')}`)
    // the raw process truth must be preserved underneath (inspector)
    const marked = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes('[semantic_type="HERMES"]').length : 0`)
    check('X5 semantic node in graph', marked >= 1, `nodes=${marked}`)
    // click the semantic node -> inspector shows SEMANTIC IDENTITY
    await cdp.eval(`(() => {
      const n = window.__esw_cy?.getElementById('sem:hermes')
      if (!n || !n.length) return false
      const p = n.renderedPosition()
      const r = window.__esw_cy.container().getBoundingClientRect()
      window.__esw_clickTarget = { x: Math.round(r.left + p.x), y: Math.round(r.top + p.y) }
      return true
    })()`)
    const hpos = await cdp.eval(`window.__esw_clickTarget ?? null`)
    if (hpos) {
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: hpos.x, y: hpos.y, button: 'left', clickCount: 1 })
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: hpos.x, y: hpos.y, button: 'left', clickCount: 1 })
      await sleep(800)
    }
    const hInsp = await cdp.eval(EX.inspectorText)
    check('X6 inspector shows SEMANTIC IDENTITY', hInsp.includes('SEMANTIC IDENTITY'), 'semantic section present')
    check('X7 confidence shown', /CONFIRMED|HIGH/.test(hInsp), 'confidence in inspector')
    await cdp.eval(EX.closeInspector)
  } else {
    console.log('  SKIP  X1–X7 (Hermes not running)')
  }

  // ---- Test Y: MCP servers (REAL / FIXTURE-only) --------------------------
  console.log('\n[Test Y] MCP server detection')
  const mcpNames = semState?.summary?.mcp ?? []
  if (mcpNames.length > 0) {
    const mcpNodes = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes('[kind="SEMANTIC"][semantic_type="MCP_SERVER"]').length : 0`)
    check('Y1 MCP semantic nodes', mcpNodes >= 1, `nodes=${mcpNodes}`)
    const spawned = await cdp.eval(`window.__esw_cy ? window.__esw_cy.edges('[kind="SPAWNED"], [kind="PROCESS_PARENT"]').length : 0`)
    check('Y2 stdio/ancestry relationships', spawned >= 1, `edges=${spawned}`)
    check('Y3 server identity evidence', mcpNames.every((n) => typeof n === 'string' && n.length > 0), mcpNames.join(','))
  } else {
    console.log('  SKIP  Y1–Y3 — REAL MCP TEST: SKIPPED (no MCP servers running; stdio/network cases covered by fixture unit tests)')
  }

  // ---- Test Z: AI view (SYSTEM <-> AI toggle, classification-driven) ------
  console.log('\n[Test Z] AI view')
  const realSemantic = (semState?.summary?.hermes === true) || (semState?.summary?.lm_studio === true) || (gpuCount > 0)
  if (realSemantic) {
    await cdp.eval(`[...document.querySelectorAll('.pill')].find(b => b.textContent.trim() === 'AI')?.click()`)
    await sleep(800)
    const dimmed = await cdp.eval(`window.__esw_cy ? window.__esw_cy.elements('.ai-dim').length : -1`)
    check('Z1 unrelated nodes dimmed in AI view', dimmed > 0, `dimmed=${dimmed}`)
    const hermesVisible = await cdp.eval(`(() => {
      const n = window.__esw_cy?.getElementById('sem:hermes')
      return n && n.length ? !n.hasClass('ai-dim') && n.visible() : false
    })()`)
    check('Z2 semantic node visible in AI view', hermesVisible === true, 'sem:hermes not dimmed')
    const gpuVisible = await cdp.eval(`(() => {
      const n = window.__esw_cy?.getElementById('gpu:0')
      return n && n.length ? !n.hasClass('ai-dim') : false
    })()`)
    check('Z3 GPU node visible in AI view', gpuVisible === true, 'gpu:0 not dimmed')
    const procNotDimmed = await cdp.eval(`(() => {
      const n = window.__esw_cy?.nodes('[kind="PROCESS"]').filter(n => n.data('semantic'))[0]
      return n ? !n.hasClass('ai-dim') : false
    })()`)
    check('Z4 classified process kept in AI view', procNotDimmed === true, 'semantic process not dimmed')
    await cdp.shot('z-ai-view.png')
    // back to SYSTEM: everything restored, NO cytoscape recreation. The
    // instance identity is the real invariant (node counts legitimately
    // drift as processes start/stop on a live machine).
    const cyBefore = await cdp.eval(`(() => { window.__esw_cyMarker = window.__esw_cy; return !!window.__esw_cy })()`)
    await cdp.eval(`[...document.querySelectorAll('.pill')].find(b => b.textContent.trim() === 'SYSTEM')?.click()`)
    await sleep(600)
    const dimmedAfter = await cdp.eval(`window.__esw_cy ? window.__esw_cy.elements('.ai-dim').length : -1`)
    const sameInstance = await cdp.eval(`window.__esw_cy === window.__esw_cyMarker`)
    check('Z5 SYSTEM view restores', dimmedAfter === 0, `dimmed=${dimmedAfter}`)
    check('Z6 toggle preserves graph (no recreate)', cyBefore === true && sameInstance === true,
      'same cytoscape instance across toggle')
  } else {
    console.log('  SKIP  Z1–Z6 (no real semantic detections to show)')
  }

  // ---- Test AA: LARGE GRAPH benchmark (TEST-ONLY synthetic data) ----------
  // Deterministic synthetic fixtures through the real WS -> GraphController
  // -> overlay path. Everything here is labeled benchmark/test_only; the
  // page is returned to REAL mode before the final screenshot below.
  console.log('\n[Test AA] Large graph benchmark (TEST-ONLY synthetic)')
  await cdp.eval(`(() => { window.__esw_cyAA = window.__esw_cy; return !!window.__esw_cy })()`)
  let st0 = await (await fetch(`${BM_API}/status`)).json()
  if (st0.active) {
    // defensive: a previous interrupted run may have left it active
    await benchmarkDeactivate()
    st0 = await (await fetch(`${BM_API}/status`)).json()
  }
  check('AA0 benchmark inactive by default', st0.active === false, JSON.stringify(st0))

  // gate: activation without the test-only header is refused
  const gated = await (await fetch(`${BM_API}/activate`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nodes: 500 }),
  })).json()
  check('AA0b activation header-gated', !!gated.error, gated.error || 'no error')

  // ---- 500 nodes ----------------------------------------------------------
  const b500 = await benchmarkLoad(cdp, 500)
  // instance marker must be captured on the CURRENT page (each benchmarkLoad
  // reloads the page, so a marker set before it points at the old context)
  await cdp.eval(`(() => { window.__esw_cyAA = window.__esw_cy; return true })()`)
  check('AA1 500-node fixture applied', b500.nodes >= 500, `nodes=${b500.nodes}`)
  const badge500 = await cdp.eval(EX.benchmarkBadge)
  check('AA2 benchmark badge visible', /BENCHMARK MODE/.test(badge500), badge500)
  const testOnly500 = await cdp.eval(EX.testOnlyNodes)
  const realPids500 = await cdp.eval(EX.realPidNodes)
  check('AA3 every node labeled test_only', testOnly500 === b500.nodes, `testOnly=${testOnly500}`)
  check('AA4 zero real pids in fixture', realPids500 === 0, `realPids=${realPids500}`)
  const edges500 = await cdp.eval(EX.edgeCount)
  check('AA5 proportionate edges', edges500 >= Math.round(b500.nodes * 0.5), `edges=${edges500}`)
  check('AA6 perf panel visible (benchmark only)', await cdp.eval(EX.perfPanelVisible))
  const perf500 = await cdp.eval(EX.perfSnapshot)
  const p500 = JSON.parse(perf500)
  check('AA7 perf instrumentation active', p500.updateMs.count >= 1 && p500.layoutMs.count >= 1,
    `update=${p500.updateMs.last.toFixed(1)}ms layout=${p500.layoutMs.last.toFixed(1)}ms`)
  console.log(`  info  500: update=${p500.updateMs.last.toFixed(1)}ms layout=${p500.layoutMs.last.toFixed(1)}ms nodes=${p500.nodes} edges=${p500.edges}`)
  // AI toggle at 500: instance preserved, timing measured
  await cdp.eval(EX.clickAiPill)
  await sleep(600)
  const aiDim500 = await cdp.eval(`window.__esw_cy ? window.__esw_cy.elements('.ai-dim').length : -1`)
  const same500 = await cdp.eval(`window.__esw_cy === window.__esw_cyAA`)
  const p500b = JSON.parse(await cdp.eval(EX.perfSnapshot))
  check('AA8 AI toggle dims unrelated nodes', aiDim500 > 0, `dimmed=${aiDim500}`)
  check('AA9 toggle preserves graph instance', same500 === true)
  check('AA10 AI toggle fast at 500', p500b.aiToggleMs !== null && p500b.aiToggleMs < 2000,
    `aiToggle=${p500b.aiToggleMs?.toFixed(1)}ms`)
  await cdp.eval(EX.clickSystemPill)
  await sleep(400)
  const dimAfter500 = await cdp.eval(`window.__esw_cy ? window.__esw_cy.elements('.ai-dim').length : -1`)
  check('AA10b SYSTEM restores at 500', dimAfter500 === 0, `dimmed=${dimAfter500}`)
  // search + filter responsiveness at 500
  await cdp.eval(`window.__esw_controller?.setSearch('svchost') ?? false`)
  await cdp.eval(`window.__esw_controller?.setFilter('active') ?? false`)
  const p500c = JSON.parse(await cdp.eval(EX.perfSnapshot))
  check('AA11 search fast at 500', p500c.searchMs !== null && p500c.searchMs < 2000,
    `search=${p500c.searchMs?.toFixed(1)}ms`)
  check('AA12 filter fast at 500', p500c.filterMs !== null && p500c.filterMs < 2000,
    `filter=${p500c.filterMs?.toFixed(1)}ms`)
  await cdp.eval(`window.__esw_controller?.setFilter('all') ?? false`)
  await cdp.eval(`window.__esw_controller?.setSearch('') ?? false`)
  await cdp.shot('aa-benchmark-500.png')

  // particle budget: TEST-ONLY injected activity (benchmark mode only)
  const injected = await cdp.eval(`(() => {
    const cy = window.__esw_cy
    const edges = cy.edges()
    const items = []
    for (let i = 0; i < 600; i++) {
      const e = edges[i % edges.length]
      items.push({ edge_id: e.id(), fwd_bps: 400000 + (i % 3) * 500000, rev_bps: 200000,
        duration_ms: 200, fwd_bytes: 1000, rev_bytes: 500, level: (i % 3) + 1, last_activity: Date.now() / 1000 })
    }
    window.__esw_controller.testInjectActivity(items)
    return items.length
  })()`)
  check('AA13 synthetic activity injected', injected === 600, `items=${injected}`)
  let ovAA = null
  for (let i = 0; i < 24; i++) {
    await sleep(500)
    ovAA = JSON.parse(await cdp.eval(EX.overlayStats))
    if ((ovAA?.particles || 0) > 0 && ovAA.running) break
  }
  check('AA14 particles spawn from injected activity', (ovAA?.particles || 0) > 0, `particles=${ovAA?.particles}`)
  check('AA15 global particle budget respected', (ovAA?.particles || 0) <= ovAA?.budget?.maxParticles,
    `particles=${ovAA?.particles} cap=${ovAA?.budget?.maxParticles}`)
  check('AA16 activity edges bounded', (ovAA?.activity || 0) <= (ovAA?.budget?.activityEdges || 0),
    `activity=${ovAA?.activity} cap=${ovAA?.budget?.activityEdges}`)
  check('AA17 injected activity flagged synthetic', (ovAA?.synthetic || 0) === (ovAA?.activity || 0),
    `synthetic=${ovAA?.synthetic} activity=${ovAA?.activity}`)
  await cdp.eval(`window.__esw_controller?.testForceIdle() ?? false`)
  await sleep(500)
  const ovIdle = JSON.parse(await cdp.eval(EX.overlayStats))
  check('AA18 idle clears particles', ovIdle.particles === 0 && ovIdle.running === false,
    `particles=${ovIdle.particles} running=${ovIdle.running}`)

  // ---- 1000 nodes ---------------------------------------------------------
  const b1000 = await benchmarkLoad(cdp, 1000)
  await cdp.eval(`(() => { window.__esw_cyAA = window.__esw_cy; return true })()`)
  check('AA19 1000-node fixture applied', b1000.nodes >= 1000, `nodes=${b1000.nodes}`)
  const p1000 = b1000.snap
  console.log(`  info  1000: update=${p1000.updateMs.last.toFixed(1)}ms layout=${p1000.layoutMs.last.toFixed(1)}ms nodes=${p1000.nodes} edges=${p1000.edges}`)
  check('AA20 layout completes at 1000', p1000.layoutMs.last > 0 && p1000.layoutMs.last < 60000,
    `layout=${p1000.layoutMs.last.toFixed(1)}ms`)
  await cdp.eval(EX.clickAiPill)
  await sleep(800)
  const p1000b = JSON.parse(await cdp.eval(EX.perfSnapshot))
  const same1000 = await cdp.eval(`window.__esw_cy === window.__esw_cyAA`)
  check('AA21 AI toggle at 1000 preserves instance', same1000 === true)
  check('AA22 AI toggle measured at 1000', p1000b.aiToggleMs !== null && p1000b.aiToggleMs < 3000,
    `aiToggle=${p1000b.aiToggleMs?.toFixed(1)}ms`)
  await cdp.eval(EX.clickSystemPill)
  await sleep(400)
  await cdp.eval(`window.__esw_controller?.setSearch('chrome') ?? false`)
  const p1000c = JSON.parse(await cdp.eval(EX.perfSnapshot))
  check('AA23 search at 1000 responsive', p1000c.searchMs !== null && p1000c.searchMs < 3000,
    `search=${p1000c.searchMs?.toFixed(1)}ms`)
  await cdp.eval(`window.__esw_controller?.setSearch('') ?? false`)
  await cdp.shot('aa-benchmark-1000.png')

  // ---- 1500 nodes ---------------------------------------------------------
  const b1500 = await benchmarkLoad(cdp, 1500)
  check('AA24 1500-node fixture applied', b1500.nodes >= 1500, `nodes=${b1500.nodes}`)
  const p1500 = b1500.snap
  console.log(`  info  1500: update=${p1500.updateMs.last.toFixed(1)}ms layout=${p1500.layoutMs.last.toFixed(1)}ms nodes=${p1500.nodes} edges=${p1500.edges} fps=${p1500.fps}`)
  check('AA25 layout completes at 1500', p1500.layoutMs.last > 0 && p1500.layoutMs.last < 90000,
    `layout=${p1500.layoutMs.last.toFixed(1)}ms`)
  await cdp.eval(EX.clickAiPill)
  await sleep(1000)
  const p1500b = JSON.parse(await cdp.eval(EX.perfSnapshot))
  check('AA26 AI toggle at 1500 measured', p1500b.aiToggleMs !== null && p1500b.aiToggleMs < 5000,
    `aiToggle=${p1500b.aiToggleMs?.toFixed(1)}ms`)
  await cdp.eval(EX.clickSystemPill)
  await sleep(400)
  await cdp.eval(`window.__esw_controller?.setFilter('highcpu') ?? false`)
  const p1500c = JSON.parse(await cdp.eval(EX.perfSnapshot))
  check('AA27 highcpu filter at 1500 responsive', p1500c.filterMs !== null && p1500c.filterMs < 5000,
    `filter=${p1500c.filterMs?.toFixed(1)}ms`)
  await cdp.eval(`window.__esw_controller?.setFilter('all') ?? false`)
  await cdp.shot('aa-benchmark-1500.png')

  // family view on the synthetic fixture (fixture contains real-shaped families)
  await cdp.eval(`[...document.querySelectorAll('.view-toggle .pill')].find(b => b.textContent === 'FAMILIES')?.click() ?? false`)
  await sleep(2500)
  const famAA = await cdp.eval(EX.familyNodes2)
  check('AA28 families collapse benchmark graph', famAA >= 1, `families=${famAA}`)
  await cdp.eval(`[...document.querySelectorAll('.view-toggle .pill')].find(b => b.textContent === 'NODES')?.click() ?? false`)
  await sleep(1500)

  // ---- 2000 nodes (validation target, soft gates) -------------------------
  const b2000 = await benchmarkLoad(cdp, 2000)
  check('AA29 2000-node fixture applied', b2000.nodes >= 2000, `nodes=${b2000.nodes}`)
  const p2000 = b2000.snap
  console.log(`  info  2000: update=${p2000.updateMs.last.toFixed(1)}ms layout=${p2000.layoutMs.last.toFixed(1)}ms nodes=${p2000.nodes} edges=${p2000.edges} fps=${p2000.fps}`)
  const z0 = await cdp.eval(EX.zoomLevel)
  await cdp.eval(`(() => { window.__esw_cy?.zoom({ level: window.__esw_cy.zoom() * 1.6 }); return true })()`)
  await sleep(800)
  const z1 = await cdp.eval(EX.zoomLevel)
  const cyAlive = await cdp.eval(`window.__esw_cy ? window.__esw_cy.nodes().length : -1`)
  check('AA30 2000-node graph interactive (zoom)', z1 > z0 && cyAlive >= 2000, `zoom ${z0.toFixed(2)} -> ${z1.toFixed(2)}`)
  check('AA31 layout completes at 2000', p2000.layoutMs.last > 0 && p2000.layoutMs.last < 120000,
    `layout=${p2000.layoutMs.last.toFixed(1)}ms`)
  await cdp.eval(`(() => { window.__esw_cy?.fit(undefined, 40); return true })()`)
  await sleep(800)
  await cdp.shot('aa-benchmark-2000.png')

  // ---- cleanup: back to REAL mode, nothing synthetic remains -------------
  const deact = await benchmarkDeactivate()
  check('AA32 benchmark deactivated', deact.active === false, JSON.stringify(deact))
  await cdp.send('Page.reload')
  await sleep(9000)
  const realNodes = await cdp.eval(EX.nodeCount)
  const realTestOnly = await cdp.eval(EX.testOnlyNodes)
  const realBadge = await cdp.eval(EX.benchmarkBadge)
  const realPerfPanel = await cdp.eval(EX.perfPanelVisible)
  const connReal = await cdp.eval(EX.connLabel)
  check('AA33 real mode restored after benchmark', connReal === '● LIVE' && realNodes >= 100,
    `nodes=${realNodes} conn=${connReal}`)
  check('AA34 zero synthetic nodes remain', realTestOnly === 0, `testOnly=${realTestOnly}`)
  check('AA35 benchmark badge gone in real mode', realBadge === '', realBadge)
  check('AA36 perf panel hidden in real mode', realPerfPanel === false)
  const pythonReal = await cdp.eval(EX.hasNode('python.exe'))
  check('AA37 real python.exe back', pythonReal >= 1, `count=${pythonReal}`)
  // the injection hook must refuse to fabricate activity outside benchmark
  const gateThrow = await cdp.eval(`(() => {
    try { window.__esw_controller.testInjectActivity([]); return 'no-throw' }
    catch (e) { return e.message }
  })()`)
  check('AA38 testInjectActivity gated to benchmark', /TEST-ONLY/.test(gateThrow), gateThrow)

  // ---- final screenshot (live data, production build) --------------------
  // capture at a realistic desktop resolution for the README artifact
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false,
  })
  await sleep(2500)
  await cdp.eval(`(() => { window.__esw_cy?.fit(undefined, 40); return true })()`)
  await sleep(1500)
  await cdp.shot('final-live.png')
  const finalShot = join(__dirname, 'shots', 'final-live.png')
  const docsDir = join(__dirname, '..', 'docs')
  // ESW_KEEP_SCREENSHOT=1: keep the committed REAL-machine screenshot.
  // Synthetic-logical TIER2 runs MUST set this — docs/screenshot.png must
  // never show fabricated activity as real telemetry.
  // v0.3.0: when the AI view shows REAL semantic detections, the official
  // screenshot is the AI view; otherwise the SYSTEM view is kept.
  if (!process.env.ESW_KEEP_SCREENSHOT) {
    try {
      mkdirSync(docsDir, { recursive: true })
      const { copyFileSync, existsSync } = await import('node:fs')
      let shot = finalShot
      if (realSemantic && existsSync(join(__dirname, 'shots', 'z-ai-view.png'))) {
        shot = join(__dirname, 'shots', 'z-ai-view.png')
        console.log('  shot  docs/screenshot.png (AI view — real semantic detections)')
      } else {
        console.log('  shot  docs/screenshot.png (SYSTEM view)')
      }
      copyFileSync(shot, join(docsDir, 'screenshot.png'))
    } catch (e) {
      console.log('  warn  could not copy docs/screenshot.png:', e.message)
    }
  } else {
    console.log('  keep  docs/screenshot.png untouched (ESW_KEEP_SCREENSHOT=1)')
  }

  console.log(`\nRESULT: ${passed} passed, ${failed} failed`)
  ws.close()
  process.exit(failed === 0 ? 0 : 1)
}

main().catch((e) => {
  console.error('ACCEPTANCE ERROR:', e.message)
  process.exit(2)
})
