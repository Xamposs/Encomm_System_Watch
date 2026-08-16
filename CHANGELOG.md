# Changelog

All notable changes to ENCOMM SYSTEM WATCH are recorded here.

## [0.1.0] — 2026-08-16

Initial implementation — a live, read-only system topology map for Windows 11.

### Added

- **Backend** (FastAPI + psutil, binds 127.0.0.1:8765)
  - Process collector: stable IDs (`proc:<pid>:<create_time_ms>`), defensive
    per-field handling of AccessDenied / NoSuchProcess / zombie / PID reuse,
    TTL-cached stable metadata, batched `memory_info + cpu_times` fast path
    (steady tick ≈ 0.6 s for ~300 processes).
  - Network collector: TCP + UDP sockets classified as listening / localhost /
    external; pid-less sockets mapped to the SYSTEM node; AccessDenied
    per-family fallback.
  - Topology engine: PROCESS / SYSTEM / EXTERNAL_ENDPOINT / LISTENING_PORT /
    LOCAL_ENDPOINT nodes; evidence-only edges; localhost process-to-process
    pairing; endpoint caps with per-process overflow aggregation; edge
    aggregation by (source, target, kind) with port lists.
  - Diff engine: PROCESS_STARTED / PROCESS_STOPPED / CONNECTION_OPENED /
    CONNECTION_CLOSED / PROCESS_METRICS_UPDATED (throttled); close events
    suppressed for processes that stopped in the same tick.
  - Event stream: async pub/sub, slow-client drop-oldest, bounded batches.
  - WebSocket `/ws`: full snapshot on connect, event batches, 2 s stats
    heartbeat, ping; REST `/api/health` and `/api/state`.
  - Optional static serving of `frontend/dist`.
  - Demo collector behind `ESW_DEMO_MODE=1` (default off; snapshots flagged).

- **Frontend** (React + TypeScript + Vite + Cytoscape)
  - Fullscreen dark control-room topology canvas (fcose layout, zoom/pan/fit).
  - Traveling-signal pulse overlay (canvas) for real connection events;
    subtle flow on recently-active edges; graceful fades on removal.
  - Live header: ● LIVE / CONNECTING / DISCONNECTED, process/connection/
    listening counts, CPU/RAM, feed clock.
  - Read-only node inspector; no control buttons anywhere.
  - LIVE EVENT DRAWER with a bounded 800-event buffer.
  - Filters (ALL / ACTIVE CONNECTIONS / LISTENING / HIGH CPU) and process
    search with highlight-and-dim (positions preserved).
  - Auto-reconnecting WebSocket with dead-link watchdog; zoom-aware label
    visibility.

- **Tooling & docs**
  - `start-dev.ps1` development launcher (localhost only, tree-kill cleanup,
    PYTHONPATH isolation).
  - `tools/acceptance.mjs` — headless CDP acceptance driver (Tests A–K).
  - README, docs/ARCHITECTURE.md, docs/PHASES.md, CHANGELOG.

### Verified

- Backend: 30 pytest cases green (`python -m pytest tests -q`).
- Frontend: `npm run build` (tsc strict + vite) green.
- Acceptance on the live Windows 11 machine: 29/29 checks (A startup,
  B real processes, C process start, D process stop, E network, F connection
  activity, G metrics, H inspector, I event drawer, J websocket recovery,
  K resilience with rapid app open/close, zero collector errors).

### Known limitations (phase 1)

- Connection pulses reflect socket-table lifecycle events
  (CONNECTION_OPENED/CLOSED), **not** packet-level traffic; packet telemetry
  is a later phase.
- Unpaired loopback sockets appear as LOCAL_ENDPOINT nodes.
- Full-metadata first pass ≈ 8 s at startup; steady ticks ≈ 0.6–1 s.
- Listening sockets show raw bound addresses (e.g. `0.0.0.0:5355`).
