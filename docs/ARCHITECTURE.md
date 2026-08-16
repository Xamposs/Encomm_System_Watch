# ENCOMM SYSTEM WATCH — Architecture

Local-first, read-only Windows 11 observability. Everything runs on the
machine being observed; nothing leaves it.

```
WINDOWS 11
   ▼
COLLECTORS  (1s tick)
   ├── ProcessCollector  — psutil, stable IDs, TTL-cached metadata
   ├── NetworkCollector  — psutil.net_connections (TCP+UDP), classified
   └── SystemCollector   — CPU %, RAM, platform info
   ▼
NORMALIZED SYSTEM STATE (Snapshot)
   ▼
TOPOLOGY ENGINE
   ▼
DIFF / EVENT ENGINE
   ▼
FASTAPI + WEBSOCKET  (127.0.0.1:8765)
   ▼
FRONTEND STATE  (React hook + imperative graph controller)
   ▼
CYTOSCAPE GRAPH  (canvas pulse overlay on top)
```

Network activity (v0.2) plugs into the same pipeline — the v0.2.1 runtime
chain is:

```
WINDOWS NETWORK ACTIVITY SOURCE   (ETW Microsoft-Windows-TCPIP, if permitted)
        ↓
EtwTcpipProvider ETW callback     (parse + normalize + append — lightweight only)
        ↓
PROVIDER BOUNDED QUEUE            (20 000 events; overflow drops are counted)
        ↓
provider.drain()                  (the runtime loop's telemetry tick)
        ↓
ActivityAggregator.record_many()  (batch ingestion, ONE lock per window)
        ↓
ACTIVITY AGGREGATOR               (200 ms windows, edge mapping, rates, bursts)
        ↓
TOPOLOGY EDGE MAPPING             (tuple → edge evidence, rebuilt every tick)
        ↓
WEBSOCKET ACTIVITY BATCH          (network_activity, ≤ ~5 msg/s)
        ↓
GraphController                   (per-edge activity state + decay scheduler)
        ↓
EdgePulseOverlay                  (directional particles, rAF, idle-stops)
```

The provider → aggregator wiring (drain → record_many) lives in
`_telemetry_tick()` in `backend/app/main.py` and is the fix for the
v0.2.0 missing-wiring bug: previously the ETW provider buffered events
into its internal queue but nothing ever drained it, so real byte events
never reached the aggregator or the WebSocket.

Separate LOGICAL-TIER2 validation path (test-only, opt-in, never default):

```
SyntheticActivityProvider          (ESW_TELEMETRY_PROVIDER=synthetic ONLY)
        ↓                          scans the REAL socket table at 2 Hz,
        ↓                          caches matching loopback tuples (target
        ↓                          port), emits fabricated metadata events
        ↓                          (~100 Hz) for those REAL tuples only
PROVIDER BOUNDED QUEUE → drain → record_many → aggregator → WS → graph
```

The synthetic provider is labeled `SYNTHETIC TEST PROVIDER (logical)` in
the capability and is used exclusively to prove the production data path
end-to-end without administrator privileges. It is NOT real ETW
observation and must never be presented as one.

## Layer by layer

### 1. Collectors (`backend/app/collectors/`)

| Module | What it collects | Notes |
|---|---|---|
| `processes.py` | pid, name, exe, username, status, CPU %, RAM, threads, ppid, start time, cmdline | Stable id `proc:<pid>:<create_time_ms>` guards against PID reuse. Per-field try/except — a single inaccessible/vanishing process never crashes the tick. psutil caches `name/create_time/exe/username/ppid` per Process object; `status/num_threads/cmdline` are TTL-cached (60 s) here; `memory_info + cpu_times` are fetched every tick in one batched `as_dict()` call (~0.6 s for ~300 processes — measured; psutil does not parallelize across threads on Windows, so no thread pool). |
| `network.py` | TCP/UDP sockets: pid, local/remote addr, state | Classified `listening` (no remote end), `localhost` (both ends loopback), `external`. `pid=None` sockets (elevated/system) map to the SYSTEM node. AccessDenied falls back to per-family enumeration. |
| `system.py` | machine CPU %, RAM used/total, hostname, platform, boot time | One cheap call set per tick. |
| `demo.py` | synthetic data | **Only** active when `ESW_DEMO_MODE=1`. Every snapshot built from it is flagged `mode=demo`; the UI shows a DEMO badge. |

### 2. Normalized state (`models/entities.py`)

- `Snapshot` — `processes` (by stable id), `connections` (by lifecycle-stable
  key), `owner_map` (conn key → owning process stable id), `system` metrics.
- Connection keys deliberately exclude TCP state, so state churn doesn't look
  like open/close events.

### 3. Topology engine (`services/topology.py`)

Converts a snapshot into `nodes`, `edges`, `conn_targets` and `stats`.

- Node kinds today: `PROCESS`, `SYSTEM`, `EXTERNAL_ENDPOINT`,
  `LISTENING_PORT`, `LOCAL_ENDPOINT`. The schema is generic
  (`id`/`kind`/`label`/`data`) so future kinds plug in without reshaping the
  engine.
- **Evidence rules** (no invented relationships):
  - localhost TCP pair with *both* sides mapped to processes → process↔process
    edge (canonical endpoint ordering, undirected);
  - unpaired loopback → `LOCAL_ENDPOINT` node;
  - remote IP → one `EXTERNAL_ENDPOINT` node per IP (cap 120, overflow
    aggregated per process as `EXTERNAL xN`);
  - listening socket → `LISTENING_PORT` node (cap 40, overflow aggregated);
  - unmapped sockets (pid None) → SYSTEM node.
- Edges are aggregated per `(source, target, kind)` with port lists, so N
  sockets to the same remote host produce one edge with N ports — this keeps
  the graph at topology scale, not socket scale.
- Process nodes carry `parent_sid` (real PPID evidence) so the frontend can
  build FAMILY (parent/child) grouping without guessing.

### 4. Diff / event engine (`services/diff_engine.py`)

Compares consecutive snapshots and emits only truthful events:

| Event | Trigger |
|---|---|
| `PROCESS_STARTED` | new stable id |
| `PROCESS_STOPPED` | stable id gone |
| `CONNECTION_OPENED` | new connection key (carries edge id + node dicts) |
| `CONNECTION_CLOSED` | key gone (carries `remaining` count on the edge) |
| `PROCESS_METRICS_UPDATED` | \|ΔCPU\| ≥ 2 % or \|ΔRAM\| ≥ 16 MB or 10 s force; capped at 60/tick |
| `TRAFFIC_BURST` | edge bytes ≥ 200 KB in one 200 ms window (rate-limited 10 s/edge, conservative, no classification) |
| `TELEMETRY_CAPABILITY_CHANGED` | telemetry provider started / died / demoted |

When a process stops, its connections are *not* re-reported as individual
close events — the node dies with its edges. Events are ordered
(started → opened → closed → metrics) so the frontend can apply them
incrementally.

Startup/exit race handling: psutil can transiently miss a mid-startup process
or attribute a fresh socket to `pid=None` (SYSTEM). The engine suppresses a
stopped owner's connection events **only when the socket tuples are also gone**
from the current snapshot — otherwise real opens/closes would be silently
swallowed and edges would never appear on clients.

### 5. Network telemetry (`backend/app/telemetry/`)

Abstraction over Windows network activity sources with honest capability
detection. **Tiers:**

| Tier | What | Source |
|---|---|---|
| TIER2 | per-connection/per-edge byte activity | ETW `Microsoft-Windows-TCPIP` |
| TIER0 | socket lifecycle only (open/close) | psutil topology (always works) |
| — | system adapter totals (header bandwidth) | psutil per-interface counters (always works) |

- `base.py` — `NetworkActivityEvent` (pid, 4-tuple, direction, size — metadata
  only), `Capability`, provider interface, per-edge/per-process rate state.
- `windows_network.py` — `EtwTcpipProvider`: realtime ETW session on
  Microsoft-Windows-TCPIP via pywintrace; SEND/RECEIVE task names give
  direction; payload bytes are never touched (only the size field). The
  ETW callback only parses, normalizes and appends to a bounded 20 000-event
  queue (overflow drops are counted) — no topology or WebSocket work on the
  consumer thread. An unelevated session fails with ERROR_ACCESS_DENIED —
  the provider detects this, reports `elevation_required`, and the app stays
  on TIER0. SYSTEM WATCH never auto-elevates. `AdapterTotalsSampler` —
  system-wide down/up bps from per-interface counters (loopback excluded),
  labeled separately from captured telemetry.
- `synthetic.py` — `SyntheticActivityProvider`: TEST-ONLY logical-TIER2
  source, activated exclusively via `ESW_TELEMETRY_PROVIDER=synthetic`.
  Scans the real socket table at 2 Hz (never hammering psutil, which would
  corrupt the collector's own attribution), caches matching ESTABLISHED
  loopback tuples for a target port, and emits fabricated metadata events
  (~100 Hz) for those REAL tuples. Capability is labeled `SYNTHETIC TEST
  PROVIDER (logical)` so it can never be mistaken for real ETW. Used by the
  acceptance suite (Tests S/T) and manual verification only.
- `activity_aggregator.py` — 200 ms aggregation windows; raw events are
  ingested in batches (`record_many` — one lock acquisition per window) and
  mapped to topology edges via (pid, 4-tuple) evidence rebuilt each tick;
  per-edge directional rates (1 s EMA) and activity levels; ACTIVE
  (< 500 ms) / RECENT (< 5 s) / IDLE decay timestamps; per-process totals
  for the node inspector/halo; conservative TRAFFIC_BURST detection. Events
  that cannot be mapped to a specific edge are attributed to the owning
  process (node halo) or dropped — never faked onto a random edge.
  Diagnostic counters track every stage: events recorded / mapped to edges
  / mapped to nodes / unattributed / batches emitted, plus the last
  non-empty batch's directional byte totals.

**Fallback chain** (documented + tested): ETW TIER2 → TIER0. If the ETW
session dies at runtime, a `TELEMETRY_CAPABILITY_CHANGED` event is emitted
and the header chip downgrades honestly. Adapter totals remain available at
every tier.

Why ETW and not alternatives (all measured on this machine, unelevated):
`GetPerTcpConnectionEStats` (per-connection byte counters) returns
ERROR_ACCESS_DENIED; the NDU analytic provider is also access-denied. Both
would need elevation like ETW, and ETW additionally covers UDP and gives
explicit direction. Documented, not used.

### 6. FastAPI + WebSocket (`services/event_stream.py`, `main.py`)

- `GET /api/health` — loop health, error counters, last-tick age.
- `GET /api/state` — current full topology (debugging).
- `GET /api/telemetry` — current capability tier + reason (honest indicator).
- `GET /api/telemetry/debug` — read-only pipeline counters: provider
  received/drained/dropped + queue depth, aggregator recorded/mapped/
  unattributed/batches, last batch's directional bytes. Counts only, never
  payloads; localhost-only like everything else.
- `WS /ws` — on connect: full snapshot (once). Then: event batches (≤100),
  `network_activity` batches (≤ ~5/s), stats every 2 s (with `net` block +
  telemetry info), heartbeat ping. No periodic full re-transmission.
- The telemetry runtime loop (`_telemetry_loop`) calls `_telemetry_tick()`
  every ~200 ms: `provider.drain() → aggregator.record_many() →
  aggregator.flush()`. Provider and health-probe failures are caught — a
  broken telemetry source never kills SYSTEM WATCH (tested).
- `EventStream` is an async pub/sub bus; slow clients drop oldest events
  instead of blocking the collector.
- The collector loop runs `collect → build topology → diff → publish` every
  ~1 s (measured tick ≈ 0.6–0.7 s steady state). Any tick failure is counted
  and logged without killing the loop.
- If `frontend/dist` exists, the backend also serves the built UI at `/`.

### 7. Frontend (`frontend/src/`)

- `services/ws.ts` — WebSocket client: relative URL (works in dev via Vite
  proxy and in prod via the backend), exponential backoff reconnect, 12 s
  dead-link watchdog (the server sends stats every 2 s, so silence means
  dead).
- `hooks/useSystemWatch.ts` — React state only for *derived UI* (stats,
  events, selection, connection status, telemetry chip). Graph mutations
  bypass React.
- `graph/GraphController.ts` — one Cytoscape instance for the app lifetime;
  `cy.batch` upserts; snapshot → full replace + fcose layout; events →
  incremental add/update/fade-remove. Per-edge activity state feeds edge
  styling (`actLow/actMed/actHigh`) and the pulse overlay. A single shared
  activity-decay scheduler (1 s interval, started on first activity batch,
  stopped when nothing is tracked — never one timer per edge) clears stale
  edge styling and per-node net rates purely by wall-clock age, so visual
  state decays even when no further `network_activity` batch arrives.
  Port lists on shared edges are **merged** on every open event (never
  replaced), so multi-connection edges stay stable regardless of event
  order.
- `graph/EdgePulseOverlay.ts` — a transparent canvas above the graph draws
  traveling particles only for edges with real activity. Two strictly
  separate signal classes:
  1. lifecycle pulses (`open`/`close`/`update` — socket events);
  2. directional **data** particles from real observed bytes (forward =
     source→target, reverse = target→source; spawn rate scales with the
     observed rate).
  Decay is time-based inside the rAF loop itself: the activity map is
  pruned by age (RECENT > 5 s ⇒ removed), so a traffic stop with no
  further backend batch still fully idles — no particles, no glow, no
  thick edge — and when activity/particles/pulses/recents are all empty
  the loop cancels its own rAF and stops entirely. (Test-only `testMute`/
  `testForceIdle` hooks let the acceptance suite verify the stop mechanism
  deterministically on a busy machine; they touch UI state only.)
- Semantic zoom: far = wireframe (translucent fills, bright borders) with no
  labels; medium = process names; close = name + PID + CPU + RAM.
- FAMILY view (NODES/FAMILIES toggle): real parent/child process trees
  (parent_sid evidence) collapse into `chrome.exe ×N` nodes; the underlying
  topology is untouched and restored on toggle-back.
- FOCUS mode: double-click a node → 1-hop or 2-hop neighborhood emphasized,
  everything else dimmed (visualization only).
- Multi-select: shift+click toggles, shift+drag draws a rubber-band box
  (inspection only; left-drag panning is never hijacked).
- Edge hover tooltip shows endpoints, ports, and — only when real telemetry
  exists — directional rates and last-activity age, plus the telemetry
  source.
- Layout: fcose, `randomize` once on connect; incremental passes with
  `fit: false` afterwards so the user's viewport never jumps. New nodes are
  placed near a known neighbor when the event carries one.

### 8. Performance model

- Snapshot is transmitted once; updates are event deltas (a few bytes each).
- Raw per-packet telemetry never reaches the WebSocket: the aggregator emits
  one compact `network_activity` batch per 200 ms window (≤ ~5 msg/s) with
  bounded item counts.
- Event drawer is a bounded 800-event buffer.
- React re-renders only on stats ticks (2 s) and event batches; per-node
  metric updates mutate cytoscape data directly.
- Pulse overlay draws only active edges (capped 30 pulses + 140 particles +
  80 recents); the rAF loop stops entirely when idle.

## Future AI-agent telemetry

The topology schema already accommodates new node kinds
(`AI_AGENT`, `LLM`, `MCP_SERVER`, `DATABASE`, `WATCHER`, `QUEUE`, `API`,
`GPU`, `WINDOWS_SERVICE`, `DOCKER_CONTAINER`, `WSL_PROCESS`). Planned
integration points:

1. **Collectors** — new collectors (e.g. `ai_telemetry.py`) publish
   `AgentInfo`/`LLMRequest` records into the same `Snapshot`-shaped state.
2. **Topology engine** — kind-agnostic already; agent/LLM nodes get edges
   when evidence exists (e.g. Hermes → DeepSeek via its HTTP client socket,
   Hermes → LM Studio via the mapped localhost pair, MCP server sockets).
3. **Diff engine** — new event types (e.g. `TOOL_CALL`, `AGENT_MESSAGE`,
   `LLM_REQUEST`) follow the same lifecycle pattern.
4. **Frontend** — node-kind styles are selector-driven; adding a kind is a
   stylesheet entry, not a code change.

No AI-layer code ships in this phase; the design above is the seam where it
will attach.
