// Capture the production UI in OPERA (headless) via CDP — real machine data.
// Usage: node tools/capture_opera.mjs [out.png] [width] [height]
// Expects an Opera instance on 127.0.0.1:9223 (started headless by the driver).
const { writeFileSync } = await import('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const out = process.argv[2] ?? 'tools/shots/v102-iteration.png'
const W = Number(process.argv[3] ?? 1600)
const H = Number(process.argv[4] ?? 1000)

const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json()
const t = targets.find((x) => x.type === 'page' && !x.url.startsWith('devtools://'))
if (!t) throw new Error('no opera page target')
const ws = new WebSocket(t.webSocketDebuggerUrl)
await new Promise((r) => { ws.onopen = r })
let id = 0
const pend = new Map()
ws.onmessage = (e) => {
  const m = JSON.parse(e.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
}
const send = (method, params = {}) =>
  new Promise((res) => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
  if (r.result?.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.result.exceptionDetails).slice(0, 300))
  return r.result?.result?.value
}

await send('Page.navigate', { url: 'http://127.0.0.1:8765/' })
// wait for the graph to settle (nodes composed, layout idle)
for (let i = 0; i < 40; i++) {
  await sleep(500)
  const st = await ev(`(() => {
    const c = window.__esw_controller
    if (!c) return null
    const tm = c.topologyMetrics ? c.topologyMetrics() : null
    return { nodes: window.__esw_cy?.nodes().length ?? 0, state: tm?.layoutState ?? '?' }
  })()`)
  if (st && st.nodes > 100 && st.state === 'idle') break
}
await sleep(3000)
console.log('live:', await ev('document.querySelector(".conn-label")?.textContent ?? ""'))
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile: false })
await sleep(2500)
await ev('(() => { window.__esw_cy?.fit(undefined, 40); return true })()')
await sleep(1800)
const shot = await send('Page.captureScreenshot', { format: 'png' })
writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
console.log('saved', out)
ws.close()
process.exit(0)
