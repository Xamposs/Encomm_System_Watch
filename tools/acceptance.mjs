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
  headerText: `document.querySelector('.header')?.innerText ?? ''`,
  canvasCount: `document.querySelectorAll('canvas').length`,
  clickPos: (name) => `(() => {
    const cy = window.__esw_cy
    let n = cy.nodes('[name="${name}"]')[0] || cy.nodes('[kind="PROCESS"]')[0]
    const rp = n.renderedPosition()
    const r = cy.container().getBoundingClientRect()
    return { x: Math.round(r.left + rp.x), y: Math.round(r.top + rp.y), name: n.data('name') }
  })()`,
  closeInspector: `document.querySelector('.inspector .icon-btn')?.click() ?? false`,
  toggleDrawer: `document.querySelector('.drawer-toggle')?.click() ?? false`,
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
  const pos = await cdp.eval(EX.clickPos('python.exe'))
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 })
  await sleep(800)
  const insp = await cdp.eval(EX.inspectorText)
  check('H1 inspector opened', insp.includes('INSPECTOR'), `node=${pos.name}`)
  check('H2 PID shown', /PID/.test(insp) && /\d{2,}/.test(insp))
  check('H3 no control buttons', !/kill|terminate|restart|stop/i.test(insp))
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

  console.log(`\nRESULT: ${passed} passed, ${failed} failed`)
  ws.close()
  process.exit(failed === 0 ? 0 : 1)
}

main().catch((e) => {
  console.error('ACCEPTANCE ERROR:', e.message)
  process.exit(2)
})
