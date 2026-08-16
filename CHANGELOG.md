# Changelog

All notable changes to ENCOMM SYSTEM WATCH are recorded here.

## [0.2.0] — 2026-08-16

Live network traffic and topology visual polish.

### Added

- **Network telemetry** (`backend/app/telemetry/`)
  - `NetworkActivityProvider` abstraction with honest capability tiers:
    TIER2 (per-edge byte activity) vs TIER0 (socket lifecycle). The tier,
    source, and any elevation requirement are reported in the UI
    (`TRAFFIC: PER-EDGE` / `TRAFFIC: SOCKET EVENTS · ELEVATION REQUIRED`)
    and via `GET /api/telemetry`. SYSTEM WATCH never auto-elevates.
  - ETW `Microsoft-Windows-TCPIP` provider (pywintrace): per-packet metadata
    — pid, addresses, ports, direction (SEND/RECEIVE task names), size.
    Payload bytes are never touched. Empirically verified on this machine:
    enabling it unelevated returns ERROR_ACCESS_DENIED, which is detected,
    documented, and gracefully falls back to TIER0.
  - Activity aggregator: 200 ms windows, tuple→edge mapping rebuilt each
    tick, directional rates (1 s EMA), ACTIVE/RECENT/IDLE decay
    (500 ms / 5 s), per-process totals (node halo — never faked onto random
    edges), conservative TRAFFIC_BURST detection (≥200 KB/window, 10 s
    cooldown, no classification).
  - Adapter totals (psutil per-interface counters) reported separately and
    labeled `ADAPTER` vs `CAPTURED` in the header.
  - `ESW_TELEMETRY_*` settings (enabled, flush window, burst threshold).
- **WebSocket protocol**: `network_activity` batches (≤ ~5 msg/s, bounded),
  `TELEMETRY_CAPABILITY_CHANGED` events, `net` block + telemetry in stats,
  `/api/telemetry` REST endpoint.
- **Frontend live signals** (`EdgePulseOverlay`)
  - Directional data particles: forward (source→target) cyan, reverse amber;
    spawn rate scales with observed rate (LOW/MEDIUM/HIGH); decays to a slow
    dot then nothing. A connection that merely stays open never animates.
  - Lifecycle pulses (open/close/update) kept as a separate signal class.
- **Frontend topology UX**
  - Semantic zoom: far = wireframe (translucent fills, bright borders, no
    labels); medium = names; close = name + PID + CPU + RAM.
  - NODES/FAMILIES toggle: real parent/child process trees collapse into
    `chrome.exe ×N` nodes (parent_sid evidence); raw topology untouched.
  - FOCUS mode: double-click a node → 1/2-hop neighborhood, rest dimmed.
  - Multi-select: shift+click toggle + shift+drag rubber-band box
    (inspection only; left-drag panning preserved).
  - Edge hover tooltip: endpoints, ports, directional rates and last-activity
    age **only when real telemetry exists**, plus the telemetry source.
  - Header: NET ↓/↑ block with explicit source label, telemetry capability
    chip, selection summary bar.
  - Inspector NETWORK section (↓/↑ rates, connections, last activity).
  - Event drawer: TRAFFIC BURST + TELEMETRY rows.
  - Near-black technical canvas: faint micro-grid + dot texture; edge
    activity subtly brightens/thickens edges and decays back to thin dim
    lines.
- **Test harness** (`tools/network_activity_test/`)
  - `server.py` / `client.py` / `run.py`: deterministic loopback-only traffic
    (client→server 25 MB, server→client 10 MB by default; `--watch N` for
    continuous streaming) with exact byte-count verification.
- **Tests**: 28 new backend tests (aggregator mapping/direction/batching/
  rates/decay/bursts/attribution/totals, ETW parsing + elevation fallback,
  adapter sampler, stats net-block honesty, diff-engine startup races).
  Acceptance extended to 58 checks (A–R: telemetry honesty, bandwidth
  source, family view, focus mode, multi-select, tooltip, live harness
  traffic with truthful TIER0 assertions).

### Fixed

- Diff engine could silently swallow CONNECTION_OPENED/CLOSED for sockets
  whose owning PID attribution flipped (None→real) or whose owner was
  transiently missing from the process scan; suppression now applies only
  when the socket tuples are truly gone.
- Frontend upsertEdge **replaced** a shared edge's port list on every open
  event (randomly dropping ports depending on event order) — now merges.
- Backend venv was silently resolving fastapi/uvicorn/pydantic through a
  leaked PYTHONPATH; dependencies now installed in the venv itself.

### Verified

- Backend: 61 pytest cases green (`python -m pytest tests -q`).
- Frontend: `npm run typecheck` and `npm run build` green.
- Acceptance on the live Windows 11 machine: 58/58 checks (A–K preserved
  from 0.1.0; L telemetry honesty incl. zero fabricated activity at TIER0,
  M header bandwidth source, N families, O focus, P multi-select, Q tooltip,
  R real localhost traffic: pair edge appears, CONNECTION OPENED/CLOSED
  logged, edge removed after close, harness byte-verified).
- `docs/screenshot.png` replaced with a real live capture (1600×1000,
  production build, 290+ processes).

### Known limitations (0.2.0)

- TIER2 per-edge traffic requires the backend to run with administrator
  rights (ETW kernel provider); unelevated runs truthfully show
  `TRAFFIC: SOCKET EVENTS` with adapter totals. Verified live unelevated;
  the TIER2 path is unit-tested with mocked ETW and activates automatically
  when permitted.
- TCP only for per-edge byte attribution (ETW TCPIP provider); UDP sockets
  show lifecycle activity only.
- TIME_WAIT sockets briefly linger as LOCAL_ENDPOINT edges after a process
  exits (truthful socket-table observation; Windows default ~2 min).
- Sockets whose owning process is mid-startup can briefly appear as SYSTEM-
  owned; they re-attribute on the next tick.

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
