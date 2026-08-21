// Quick view capture: click a sem-view tab (SYSTEM|AI|INFRA) then screenshot.
// Usage: node tools/capture_view.mjs <AI|INFRA|SYSTEM> <out.png>
const { writeFileSync } = await import('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const view = process.argv[2] ?? 'AI'
const out = process.argv[3] ?? 'tools/shots/view.png'
const targets = await (await fetch('http://127.0.0.1:9223/json/list')).json()
const t = targets.find((x) => x.type === 'page' && !x.url.startsWith('devtools://'))
if (!t) throw new Error('no opera page target')
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
await ev(`(() => {
  const btns = [...document.querySelectorAll('.header .pill.sem-view')]
  const b = btns.find(x => x.textContent.trim() === '${view}')
  if (b) b.click()
  return true
})()`)
await sleep(2500)
await ev('(() => { window.__esw_cy?.fit(undefined, 40); return true })()')
await sleep(1500)
const shot = await send('Page.captureScreenshot', { format: 'png' })
writeFileSync(out, Buffer.from(shot.result.data, 'base64'))
console.log('saved', out)
ws.close(); process.exit(0)
