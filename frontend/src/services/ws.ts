import type { ConnectionStatus, ServerMessage } from '../types/system'

/**
 * WebSocket client with automatic reconnect + dead-link watchdog.
 * The server sends stats every ~2s, so 12s of silence means the link is dead.
 */
export class WatchSocket {
  private ws: WebSocket | null = null
  private retryDelay = 1000
  private closedByUser = false
  private lastMessage = 0
  private watchdog: number | undefined
  private reconnectTimer: number | undefined

  constructor(
    private onMessage: (msg: ServerMessage) => void,
    private onStatus: (s: ConnectionStatus) => void,
  ) {}

  connect(): void {
    this.closedByUser = false
    this.open()
  }

  private open(): void {
    this.onStatus('connecting')
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    this.ws = ws

    ws.onopen = () => {
      this.retryDelay = 1000
      this.lastMessage = Date.now()
      this.onStatus('live')
      window.clearInterval(this.watchdog)
      this.watchdog = window.setInterval(() => {
        if (Date.now() - this.lastMessage > 12000) ws.close()
      }, 4000)
    }

    ws.onmessage = (ev) => {
      this.lastMessage = Date.now()
      try {
        this.onMessage(JSON.parse(ev.data as string) as ServerMessage)
      } catch {
        /* malformed frame: ignore */
      }
    }

    ws.onclose = () => {
      window.clearInterval(this.watchdog)
      this.onStatus('disconnected')
      if (!this.closedByUser) this.scheduleReconnect()
    }

    ws.onerror = () => {
      ws.close()
    }
  }

  private scheduleReconnect(): void {
    window.clearTimeout(this.reconnectTimer)
    this.reconnectTimer = window.setTimeout(() => this.open(), this.retryDelay)
    this.retryDelay = Math.min(this.retryDelay * 2, 10000)
  }

  close(): void {
    this.closedByUser = true
    window.clearTimeout(this.reconnectTimer)
    window.clearInterval(this.watchdog)
    this.ws?.close()
    this.ws = null
  }
}
