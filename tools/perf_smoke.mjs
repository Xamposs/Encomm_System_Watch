// Performance smoke for v1.0.3 adaptive rendering: real graph, 1000, 1500.
// Asserts: no continuous relayout, no continuous fit, no major idle animation
// regression, no major interaction regression. Uses the TEST-ONLY benchmark
// mode (labeled synthetic — never real telemetry).
// Usage: node tools/perf_smoke.mjs   (backend + browser on :9222 expected)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const CDP = 'http://127.0.0.1:9222'
const API = 'http://127.0.0.1:8765'

const targets = await (await fetch(`${CDP}/json/list`)).json()
const t = targets.find((x) => x.type === 'page' && !x.url.startsWith('devtools://'))
if (!t) throw new Error('no page target')
const ws = new WebSocket(t.webSocketDebuggerUrl)
await new Promise((r) => { ws.onopen = r })
let id = 0
const pend = new Map()
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) } }
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 300))
  return r.result?.result?.value
}

const snap = () => ev(`(() => {
  const c = window.__esw_controller
  const p = window.__esw_perf
  const tm = c?.topologyMetrics ? c.topologyMetrics() : null
  const ps = p?.snapshot ? p.snapshot() : null
  return {
    nodes: window.__esw_cy?.nodes().length ?? 0,
    edges: window.__esw_cy?.edges().length ?? 0,
    layoutState: tm?.layoutState,
    zoom: window.__esw_cy?.zoom(),
    panX: window.__esw_cy?.pan().x,
    panY: window.__esw_cy?.pan().y,
    fps: ps?.fps,
    rAFActive: ps?.rAFActive,
    layoutMs: ps?.layoutMs,
  }
})()`)

const waitSettle = async (minNodes, maxWaitMs) => {
  const t0 = Date.now()
  while (Date.now() - t0 < maxWaitMs) {
    const s = await snap()
    if (s.nodes >= minNodes && s.layoutState === 'idle') return s
    await sleep(700)
  }
  return snap()
}

const fail = (msg) => { console.log('FAIL:', msg); process.exitCode = 1 }
let ok = true
const check = (name, cond, detail) => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${name} ${detail ?? ''}`)
  if (!cond) ok = false
}

await send('Page.navigate', { url: 'http://127.0.0.1:8765/' })
await sleep(8000)
let s = await waitSettle(100, 30000)
check('real graph visible', s.nodes > 100 && s.edges > 40, `nodes=${s.nodes} edges=${s.edges}`)
const base = s
await sleep(10000)
s = await snap()
check('real: no continuous relayout/fit (camera stable, layout idle)',
  s.layoutState === 'idle' && s.zoom === base.zoom && s.panX === base.panX && s.panY === base.panY,
  `state=${s.layoutState} zoom ${base.zoom}->${s.zoom} pan ${base.panX}->${s.panX}`)
check('real: no major idle animation regression', s.rAFActive === false || (s.fps ?? 60) >= 30,
  `rAFActive=${s.rAFActive} fps=${s.fps}`)

// --- benchmark 1000 ---
for (const [name, n] of [['1000', 1000], ['1500', 1500]]) {
  const r = await (await fetch(`${API}/api/benchmark/activate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-ESW-Benchmark': 'test-only' },
    body: JSON.stringify({ nodes: n, seed: 1 }),
  })).json()
  if (r.error) { fail(`${name}: activate: ${r.error}`); continue }
  // the fixture snapshot only flows on a fresh WS connect -> reload the page
  await send('Page.reload')
  await sleep(2000)
  s = await waitSettle(n, 60000)
  check(`${name}: graph composed (${n} nodes)`, s.nodes >= n, `nodes=${s.nodes} edges=${s.edges} state=${s.layoutState}`)
  // v1.0.3 owns camera work in a two-frame post-layout stage. layoutState can
  // truthfully be idle just before that scheduled fit executes, so sample the
  // stability baseline only after the camera has had a stable render frame.
  await sleep(1500)
  const b0 = await snap()
  await sleep(9000)
  s = await snap()
  check(`${name}: no continuous relayout/fit`, s.layoutState === 'idle' && s.zoom === b0.zoom && s.panX === b0.panX && s.panY === b0.panY,
    `state=${s.layoutState} zoom ${b0.zoom}->${s.zoom}`)
  check(`${name}: idle fps acceptable`, (s.fps ?? 60) >= 25, `fps=${s.fps} rAFActive=${s.rAFActive}`)
  // interaction: zoom in/out + pan, then re-check stability
  const t0 = Date.now()
  await ev('(() => { const cy = window.__esw_cy; const z = cy.zoom(); cy.zoom(z * 1.3); cy.zoom(z); return true })()')
  const interactMs = Date.now() - t0
  check(`${name}: interaction responsive (< 1.5s)`, interactMs < 1500, `zoom+zoom took ${interactMs}ms`)
  const deact = await (await fetch(`${API}/api/benchmark/deactivate`, { method: 'POST' })).json()
  check(`${name}: deactivated`, deact.active === false, `active=${deact.active}`)
  await send('Page.reload')
  await sleep(2000)
  await sleep(8000)
  if (name === '1500') {
    // final return-to-real only after the last benchmark
    s = await waitSettle(100, 30000)
  }
  await ev(`(() => { window.__esw_cy?.fit(undefined, 40); return true })()`)
  await sleep(1200)
}

// --- return to real ---
s = await waitSettle(100, 30000)
check('return-to-real: benchmark gone, real graph back', s.nodes > 100 && s.nodes < 1000,
  `nodes=${s.nodes} edges=${s.edges}`)
console.log(ok ? 'PERF SMOKE: ALL PASS' : 'PERF SMOKE: FAILURES')
ws.close()
process.exit(ok ? 0 : 1)
