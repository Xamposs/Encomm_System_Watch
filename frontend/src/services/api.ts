/**
 * Backend API / WebSocket origin resolution.
 *
 * The existing browser workflows stay same-origin, exactly as before:
 *   - Browser dev:  http://localhost:5173  → Vite proxies /api + /ws → 127.0.0.1:8765
 *   - Browser prod: http://127.0.0.1:8765  → the FastAPI backend serves the UI, /api and /ws
 *
 * Inside the Tauri desktop window the page runs on the tauri.localhost asset
 * origin, where no proxy exists — so API/WebSocket traffic is addressed
 * directly to the localhost backend. The backend binds 127.0.0.1 only and
 * remains the single source of truth (strictly read-only).
 */

const TAURI_BACKEND_ORIGIN = 'http://127.0.0.1:8765'

/**
 * True when the page runs inside the Tauri desktop window.
 * Primary signal is the injected window.__TAURI__ global (requires
 * app.withGlobalTauri). Fallback: the deterministic Tauri v2 asset-origin
 * hostname, which needs no config. Browser dev/prod stay on localhost, so
 * they never match.
 */
export function isTauri(): boolean {
  if (typeof window === 'undefined') return false
  if ('__TAURI__' in window) return true
  return window.location.hostname === 'tauri.localhost'
}

/** Backend HTTP origin for the current runtime ('' = same-origin browser mode). */
export function apiOrigin(): string {
  return isTauri() ? TAURI_BACKEND_ORIGIN : ''
}

/** Absolute URL for a backend HTTP endpoint. */
export function httpUrl(path: string): string {
  return `${apiOrigin()}${path}`
}

/** WebSocket URL for the live telemetry feed. */
export function wsUrl(): string {
  if (isTauri()) return `${TAURI_BACKEND_ORIGIN.replace(/^http/, 'ws')}/ws`
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}/ws`
}