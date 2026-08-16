// Capture the final live screenshot at desktop resolution (real data).
const { writeFileSync, mkdirSync } = await import('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json()
const t = targets.find((x) => x.type === 'page' && !x.url.startsWith('devtools://'))
if (!t) throw new Error('no page target')
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
await sleep(9000)
console.log('live:', await ev('document.querySelector(".conn-label")?.textContent ?? ""'))
await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })
await sleep(2500)
await ev('(() => { window.__esw_cy?.fit(undefined, 40); return true })()')
await sleep(1800)
const shot = await send('Page.captureScreenshot', { format: 'png' })
mkdirSync('C:/Users/xampos/Desktop/Encomm SYSTEM WATCH/docs', { recursive: true })
writeFileSync('C:/Users/xampos/Desktop/Encomm SYSTEM WATCH/docs/screenshot.png', Buffer.from(shot.result.data, 'base64'))
console.log('saved docs/screenshot.png')
ws.close()
process.exit(0)
