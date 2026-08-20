# ENCOMM SYSTEM WATCH — Architecture

Local-first, read-only Windows 11 observability. Everything runs on the
machine being observed; nothing leaves it.

```
WINDOWS 11
   ▼
COLLECTORS  (1s tick)
   ├── ProcessCollector  — psutil, stable IDs, TTL-cached metadata
   ├── NetworkCollector  — psutil.net_connections (TCP+UDP), classified
   ├── SystemCollector   — CPU %, RAM, platform info
   └── GpuCollector      — NVML (nvidia-smi fallback), ~1s metrics / ~2s PID map
   ▼
NORMALIZED SYSTEM STATE (Snapshot)
   ▼
TOPOLOGY ENGINE
   ▼
SEMANTIC DETECTOR REGISTRY          (GPU + AI observability, v0.3.0)
   ├── HermesDetector     — desktop app + gateway, evidence-combination
   ├── LmStudioDetector   — process identity + localhost API probe
   └── McpDetector        — cmdline/path/ancestry (stdio + HTTP/SSE)
   ▼
INFRA COLLECTORS (v0.4.0, staggered 3-5s, READ-ONLY)
   ├── ServicesCollector  — psutil win_service_iter, TTL metadata cache
   ├── WslCollector       — wsl.exe --list/--status (never starts a distro)
   ├── DockerCollector    — local engine CLI/npipe (never TCP 2375, no ENV)
   └── VmCollector        — Hyper-V CIM / vmware-vmx / VBoxManage list
   ▼
INFRA ENGINE (v0.4.0)     (svc/wsl/docker/vm nodes + HOSTED_BY/HOSTS/EXPOSES/
   ▼                      CONNECTED_TO/BACKED_BY edges + change-only events)
SEMANTIC ENGINE           (evidence + confidence -> semantic resource nodes
   ▼                      -> relationships -> change-only events)
AI TELEMETRY (v0.5.0)     (REAL application-level metadata only:
   ▼                      Hermes gateway status API + OTEL seam +
                          optional local ingestion -> normalized events ->
                          bounded buffer -> WS ai_activity/metrics/status)
DIFF / EVENT ENGINE
   ▼
FASTAPI + WEBSOCKET  (127.0.0.1:8765)
   ▼
FRONTEND STATE  (React hook + imperative graph controller)
   ▼
CYTOSCAPE GRAPH  (SYSTEM / AI / INFRA view · canvas pulse overlay on top)
```

Network activity (v0.2) plugs into the same pipeline — the v0.2.2 runtime
chain with REAL ETW correlation is:

```
Microsoft-Windows-TCPIP                 (REAL ETW provider, elevated only)
        ↓
Connection identity events              (TcpConnectionRundown / TcpConnectTcbComplete /
        ↓                               TcpAcceptListenerComplete / TcpConnectTcbProceeding)
TCB CORRELATION CACHE                   (Tcb -> pid + local/remote 4-tuple; bounded: TTL 5 min,
        ↓                               max 8192 entries, explicit removal on close/abort/RST)
TcpDataTransferSend / TcpDataTransferReceive   (modern manifest: Tcb + byte count only)
        ↓
TCB lookup                              (hits/misses counted truthfully)
        ↓
PID + real 4-tuple + direction + bytes  -> NetworkActivityEvent
        ↓
PROVIDER BOUNDED QUEUE                  (20 000 events; overflow drops are counted)
        ↓
provider.drain()                        (the runtime loop's telemetry tick)
        ↓
ActivityAggregator.record_many()        (batch ingestion, ONE lock per window)
        ↓
ACTIVITY AGGREGATOR                     (200 ms windows, edge mapping, rates, bursts)
        ↓
WEBSOCKET ACTIVITY BATCH                (network_activity, ≤ ~5 msg/s)
        ↓
GraphController                         (per-edge activity state + decay scheduler)
        ↓
EdgePulseOverlay                        (REAL DATA particles, rAF, idle-stops)
```

The provider → aggregator wiring (drain → record_many) lives in
`_telemetry_tick()` in `backend/app/main.py` and is the fix for the
v0.2.0 missing-wiring bug: previously the ETW provider buffered events
into its internal queue but nothing ever drained it, so real byte events
never reached the aggregator or the WebSocket.

Modern Windows 11 (tcpip.sys 10.0.19041) delivers transfer events
(`TcpDataTransferSend`/`Receive`) with ONLY a Tcb handle and byte counts —
no pid, no addresses — so connection identity is learned from separate
lifecycle events into the TCB correlation cache (including the
`TcpConnectionRundown` snapshot at session start). The legacy manifest
(`SENDIPV4/RECEIPV4/...`) is still parsed for older Windows; task names
are compared case-insensitively. UDP is self-contained in the modern
manifest (`UdpEndpointSendMessages`/`Receive` carry pid + sockaddrs +
bytes) and is attributed directly. Empirically, identity events arrive in
delayed batches (up to ~35 s), so verification windows must outlast them.

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
| `gpu.py` | GPU index, name, utilization, VRAM used/total, temperature, power, driver, fan, clocks, per-PID attribution | Primary source NVML (`pynvml`/`nvidia-ml-py`, C-level, no subprocess); `nvidia-smi` CSV parsing is the fallback when NVML is unavailable. Multiple GPUs supported; unavailable fields are omitted, never fabricated; every failure degrades to an empty sample — the collector can never crash SYSTEM WATCH. |
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
- `GET /api/gpu` — current GPU state: source (`NVML` / `NVIDIA_SMI` / `NONE`),
  error (if degraded), per-GPU metrics + PID attribution.
- `GET /api/semantic` — current semantic detections (type, name, confidence,
  evidence, underlying pids), relationships, summary, per-detector errors.
- `WS /ws` — on connect: full snapshot (once). Then: event batches (≤100),
  `network_activity` batches (≤ ~5/s), `gpu` state (~1/s), stats every 2 s
  (with `net` block + telemetry info), heartbeat ping. No periodic full
  re-transmission.
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
- **SYSTEM / AI view toggle** (`setSemanticView`): the AI view is driven by
  semantic classification, never frontend string search — semantic resource
  nodes (SEMANTIC / LOCAL_LLM / GPU), backend-classified processes
  (`data.semantic`) and GPU-attributed processes stay; everything else gets
  the `ai-dim` class. The toggle only adds/removes a class: the Cytoscape
  instance, layout, positions, selection and focus are all preserved.
- Semantic nodes (compact, control-room style): `◈ HERMES ● RUNNING · PID`,
  `◉ LM STUDIO <endpoint>`, `MCP SERVER <identity>`, `LOCAL LLM <model>`,
  `GPU <index> <name> <util% · VRAM>`. Strong styling only for
  HIGH/CONFIRMED; MEDIUM/LOW stay subdued (dashed borders).
- Semantic edges are styled per kind (`USES_GPU` green dashed, `SERVES_MODEL`
  purple, `LOCAL_API`/`HOSTS` cyan dashed, `PROCESS_PARENT`/`SPAWNED`/
  `MEMBER_OF` dotted gray/violet). They never receive DATA particles — the
  pulse overlay only keys real socket edges from `network_activity` batches.
- Header AI summary (`AiSummary`): compact chips — `HERMES ●`, `LM STUDIO ●`,
  `MODEL ● <id>` (LOADED) / `MODEL <id>` (AVAILABLE), `MCP n`, `GPU <util% ·
  VRAM>` — rendered only for categories actually detected.
- Inspector semantic section: SEMANTIC IDENTITY (name, confidence badge,
  evidence list, underlying PIDs, endpoint/transport/state), GPU metrics
  (utilization, VRAM, temperature, power, driver, per-PID attribution),
  model state + metadata source.

### 8. Performance model

- Snapshot is transmitted once; updates are event deltas (a few bytes each).
- Raw per-packet telemetry never reaches the WebSocket: the aggregator emits
  one compact `network_activity` batch per 200 ms window (≤ ~5 msg/s) with
  bounded item counts.
- Event drawer is a bounded 800-event buffer; only the newest 150 rows are
  mounted (v0.3.1 — frequent events cannot explode the DOM).
- React re-renders only on stats ticks (2 s) and event batches; per-node
  metric updates mutate cytoscape data directly.
- Pulse overlay draws only active edges (capped 30 pulses + 140 particles +
  80 recents); the rAF loop stops entirely when idle.
- v0.3.1 large-graph strategy:
  - **Incremental updates**: the Cytoscape instance is created once and
    never recreated (snapshot = replace-all only on (re)connect; metric
    updates, GPU metrics, semantic refresh, AI/SYSTEM toggle and activity
    events mutate existing elements in place). A label cache skips
    redundant label writes so per-second metric ticks do not re-dirty every
    node on large graphs.
  - **Layout policy**: initial layout with size-scaled fcose iteration
    budget (2000 iters ≤800 nodes, 1300 ≤1500, 900 above) and no animation
    above 800 nodes. Incremental layouts are debounced (2 s) and gated:
    above 600 nodes they run only when a pending addition batch reaches
    max(40, 5% of nodes) — small churn keeps anchor/center positions and
    never re-layouts the whole graph. Manual RELAYOUT re-runs the initial
    layout.
  - **Semantic zoom / LOD**: far zoom (<0.45) hides process labels and
    drops edge arrowhead geometry + thins edges (`edge.lod-far`); mid zoom
    shows names; near zoom shows full compact-card details. Rendering cost
    tracks zoom, not graph size.
  - **Bounded prioritized particles**: global ceiling 140 particles /
    400 activity edges; per-edge ceiling scales with level (2/4/6); spawn
    order prioritizes strongest + most recent activity. When capacity is
    exceeded only VISUAL particles are dropped — telemetry counters and
    event truth are never touched, and nothing is ever fabricated.
  - **Perf diagnostics** (`frontend/src/graph/PerfMonitor.ts` +
    `PerfPanel`): measured update/layout/activity-batch/AI-toggle/
    search/filter timings, element + visible counts, particle state, fps,
    memory trend — exposed via `window.__esw_perf` and rendered ONLY in
    benchmark mode. The production UI stays clean.

## 9. GPU + AI semantic observability (v0.3.0)

Raw process truth is always preserved underneath the semantic layer — the
semantic pipeline is purely additive (its own node/edge ids, its own events).

```
WINDOWS RAW STATE
       ↓
PROCESS / SOCKET TOPOLOGY
       ↓
SEMANTIC DETECTOR REGISTRY            (backend/app/detectors/)
       ├── HermesDetector     — Phase 15
       ├── LmStudioDetector   — Phase 14
       └── McpDetector        — Phase 16
       ↓
EVIDENCE + CONFIDENCE        (CONFIRMED / HIGH / MEDIUM / LOW)
       ↓
SEMANTIC RESOURCE NODES      (SEMANTIC · LOCAL_LLM · GPU — ids sem:*, gpu:*)
       ↓
RELATIONSHIPS                (USES_GPU · SERVES_MODEL · LOCAL_API ·
                              PROCESS_PARENT · SPAWNED · HOSTS · MEMBER_OF)
       ↓
WEBSOCKET                    (snapshot merge + change-only semantic events)
       ↓
SYSTEM / AI VIEW             (frontend classification-driven dimming)

GPU path:
NVML (pynvml) ── nvidia-smi CSV fallback
       ↓
GPU COLLECTOR                (~1 s metrics / ~2 s PID attribution)
       ↓
GPU RESOURCE NODE            (gpu:<index> — name, util, VRAM, temp, power…)
       ↓
PID ATTRIBUTION              (NVML compute+graphics process lists)
       ↓
USES_GPU edges               (process sid → gpu:<index>, evidence-backed)
```

### Detector framework (`backend/app/detectors/`)

- `base.py` — `Detection` (WHAT / WHICH / WHY / HOW CONFIDENT / WHAT
  EVIDENCE), `DetectionEvidence` (machine-readable source + sanitized
  detail), `SemanticRelationship` (stable `se:*` ids, canonical undirected
  ordering), `DetectorContext` (snapshot + topology + GPU state + pid→sid).
  Confidence mapping: a weak guess is never a fact — hint-only matches are
  LOW, bare process names MEDIUM, known-path identity or live API response
  HIGH, and only a direct runtime observation (API answer / exact known
  path) may be CONFIRMED.
- `registry.py` — `SemanticDetectorRegistry.run_all()`: per-detector
  failure isolation (one broken detector degrades only itself; errors are
  surfaced via `/api/semantic`), hints loader for `config/detectors.json`
  (hints only — never secrets; missing/broken file falls back to built-in
  defaults).
- `redact.py` — command-line secret redaction applied at the serialization
  boundary (topology node data, diff-event metadata) and inside detectors:
  `--api-key/--token/--password/--secret/--auth*` flags, `key=value` forms,
  `Authorization: Bearer <token>`. Credential values never reach the
  frontend or logs; the raw collector snapshot stays untouched.

### Detectors (strict evidence rules)

- **Hermes** (`hermes.py`) — CONFIRMED only via known executable path
  (`…hermes-agent\…\Hermes.exe`) or gateway command line
  (`hermes_cli.main … serve` inside the hermes-agent venv); family members
  (Electron children, gateway uv-shims) join only with their own Hermes
  identity signal — never by ancestry alone (a Hermes-spawned session tree
  must not absorb bash/curl/other children). Emits `MEMBER_OF`,
  real `PROCESS_PARENT`, and `HOSTS` (gateway localhost listeners).
- **LM Studio** (`lm_studio.py`) — candidate ports come from the REAL
  socket topology (owned listeners), never a blind port-1234 assumption
  (hint ports are secondary). Loopback-only API probes (`/v1/models` +
  `/api/0/models`) with short timeouts, throttled (~8 s) and cached.
  `LOADED` is claimed ONLY when the runtime API proves it; a `/v1/models`
  listing proves `AVAILABLE` only. Model metadata (architecture,
  quantization) is exposed only when the API exposes it; filename-derived
  values would be marked `FILENAME INFERENCE` with LOW confidence.
- **MCP** (`mcp.py`) — networking alone is insufficient (stdio servers have
  no sockets). Evidence: anchored `mcp` tokens in the command line, package
  paths (`node_modules/@modelcontextprotocol`, `mcp-server`, `mcp_server`),
  parent/child ancestry (Hermes gateway launcher), owned listeners for
  HTTP/SSE. Identity: `filesystem`, `github`, … when proven; `unknown`
  when MCP is proven but the server is not — never a guess. stdio servers
  get `PROCESS_PARENT` + `SPAWNED` edges (real ancestry), never fake
  network edges and never DATA particles.

### GPU collector (`backend/app/collectors/gpu.py`)

- Primary: NVML via `pynvml` (the `nvidia-ml-py` package) — C-level calls,
  no subprocess churn. Fallback: `nvidia-smi --query-gpu=…` CSV parsing.
  Multiple GPUs from the start (`gpu:<index>` nodes).
- Only fields actually exposed are emitted; unavailable = omitted.
  Per-process VRAM only when the API really provides it (NVML graphics
  contexts often report none — that stays `n/a`, never fabricated).
- Cadence: overall metrics ~1 s, PID attribution ~2 s (main.py `_gpu_loop`,
  failure-isolated). `changed_pids()` diffs per-GPU pid sets so
  `GPU_PROCESS_ATTACHED` / `GPU_PROCESS_DETACHED` events fire on change
  only (first sample establishes the baseline — no startup storm).

### Semantic engine (`backend/app/services/semantic.py`)

- Builds semantic nodes (`SEMANTIC`/`LOCAL_LLM`/`GPU` kinds) and semantic
  edges (`se:*` ids) merged into every snapshot; augments underlying
  process nodes with `data.semantic` so the AI view is classification-
  driven. Raw topology objects are never mutated.
- Change-only events: `HERMES_DETECTED`, `LM_STUDIO_DETECTED`,
  `MCP_SERVER_DETECTED`, `SEMANTIC_LOST`, `MODEL_LOADED`, `MODEL_AVAILABLE`
  (flips only; a new LOCAL_LLM is announced once, not twice), and
  `GPU_PROCESS_ATTACHED`/`GPU_PROCESS_DETACHED` (owned by the GPU loop at
  its 2 s cadence). Repeated unchanged detections never re-emit.

### Truthfulness invariants

- `unknown` stays unknown; MEDIUM/LOW classifications get subdued styling.
- `LOADED` ≠ `AVAILABLE`; adapter totals ≠ captured bytes; NVML ≠ nvidia-smi
  (the source is always labeled).
- Command lines are redacted before they can reach the UI; detectors only
  consume sanitized metadata.
- One failed detector (NVML missing, API timeout, parser exception) degrades
  only that detector — core monitoring continues.

## Future AI-agent telemetry (Phase 17 — NOT STARTED)

The schema already accommodates the next layer. The v0.3.0 semantic layer
ships the seams: detector registry, evidence model, semantic nodes/edges,
change-only events, AI view. Deep agent telemetry (tool calls, tokens, TPS,
context usage, latency, reasoning) is intentionally NOT fabricated — it
waits for a real existing interface to expose it.

## 10. Large-graph benchmark mode + ETW health (v0.3.1)

### TEST-ONLY benchmark mode (`backend/app/services/benchmark_graph.py`)

Renderer/performance validation uses a deterministic synthetic fixture —
never the real machine, never fake real telemetry.

```
POST /api/benchmark/activate {nodes, seed?}   header X-ESW-Benchmark: test-only
GET  /api/benchmark/status
POST /api/benchmark/deactivate
```

- Inactive by default; activation is explicit and header-gated. While
  active, the WS snapshot serves the labeled fixture
  (`mode: "benchmark"`, `benchmark: {label: "TEST/BENCHMARK (synthetic)",
  …}`) and every element carries `test_only`/`benchmark` flags, synthetic
  pids (≥400000) and `SYNTHETIC\bench` identity.
- Real event / `network_activity` / GPU / semantic messages are suppressed
  for the duration (the collectors keep running underneath), so synthetic
  and real data can never mix; deactivation restores the live graph on the
  next snapshot. There are NO system control paths — the mode only switches
  which graph data is served.
- Deterministic: same `node_count` + `seed` → identical graph (default seed
  `node_count * 7919`). Realistic shapes: process families (parent + ≥2
  same-name children), endpoints (TEST-NET IPs), listening ports,
  GPU/semantic/model nodes; edges LOCALHOST / EXTERNAL / PROCESS_PARENT /
  LISTEN / USES_GPU / SERVES_MODEL / SPAWNED / HOSTS / MEMBER_OF.

### Read-only ETW attribution health (`backend/app/services/etw_health.py`)

Long-running stale `esw-telemetry` sessions can keep delivering provider
events while edge attribution freezes. The detector watches
`provider.events_received` vs `aggregator.events_mapped_to_edges`:

- `OK` — mapping is moving (or provider quiet).
- `WATCHING` — events climbing, mapping frozen < 45 s (`ESW_ETW_HEALTH_FREEZE_S`).
- `DEGRADED` — frozen ≥ threshold while tracked edges exist → surfaces
  `ETW ATTRIBUTION DEGRADED` (one WS `ETW_HEALTH` event per transition;
  also under `health` in `/api/telemetry/debug`).

Strictly READ-ONLY: it never stops logman, restarts ETW, restarts the
backend or kills processes — the operator restarts manually.

## 11. Infrastructure observability (v0.4.0)

The infrastructure layer answers "what hosts what" on top of the raw process
truth — strictly read-only, evidence-driven, change-only. Every platform
degrades independently: Docker Desktop not running → Docker unavailable only;
no WSL → WSL unavailable only; a missing hypervisor module → that provider
unavailable only. SYSTEM WATCH keeps running.

```
WINDOWS HOST
   │
   ├── SERVICES                  ⚙ svc:<name>
   │      └── HOSTED_BY ──▶ PROCESS   (real service PID evidence; N services
   │                                  may share one svchost.exe — N edges to
   │                                  the ONE process node, never one fake
   │                                  process per service)
   │
   ├── WSL                       ⬡ wsl:<distro>
   │      └── (HOSTS)            RUNNING/STOPPED · WSL1/2; bounded internal
   │                              summary ONLY for already-running distros
   │
   ├── DOCKER ENGINE             ◆ docker:engine
   │      ├── HOSTS ──▶ CONTAINER        ◇ container:<id12>
   │      │                └── EXPOSES ──▶ LISTENING_PORT (proven host mapping
   │      │                                 + existing topology listener only)
   │      └── CONTAINER ── CONNECTED_TO ──▶ DOCKER_NETWORK (metadata-proven)
   │
   └── HYPERVISOR                ▣ vm:<provider>:<identity>
          └── VM ── BACKED_BY ──▶ PROCESS   (real host process: vmwp.exe /
                                       vmware-vmx.exe / VBox*)
              └── USES_GPU ──▶ GPU         (ONLY when NVML PID attribution
                                            proves the VM HOST process uses
                                            that GPU)
```

### 11.1 Collectors (`backend/app/collectors/`)

| Module | Source (all READ-ONLY) | Degrades to |
|---|---|---|
| `services.py` | `psutil.win_service_iter()` / service APIs; TTL-cached heavy metadata (30 s), status+PID every poll | per-service `inaccessible` entry (name preserved) — never a crash |
| `wsl.py` | `wsl.exe --list --verbose` / `--list --running` / `--status` (UTF-16-resilient decode); bounded internal snapshot (`ps`/`free`/`uname`/`/proc/net/tcp`) for RUNNING distros only | WSL section unavailable; stopped distros never started/inspected |
| `docker.py` | installed `docker` CLI over Docker Desktop's local npipe (never TCP 2375, never insecure API); `version`/`ps -a`/targeted `inspect --format '{{.State.Pid}}'` only | `engine_status: NOT_RUNNING` + empty containers |
| `vm.py` | Hyper-V: `Get-VM` (CIM) + vmwp.exe GUID→PID mapping; VMware: vmware-vmx.exe + `.vmx` cmdline evidence, `vmrun list`; VirtualBox: `VBoxManage list runningvms` | per-provider `installed: false`; unproven processes → `VIRTUALIZATION PROCESS` (LOW/MEDIUM) |

Subprocesses use `CREATE_NO_WINDOW`; PowerShell runs with `-NoProfile
-NonInteractive`; every command has a hard timeout; every collector is
wrapped so a failure degrades only its own section.

### 11.2 InfraEngine (`backend/app/services/infra.py`)

Additive layer (same pattern as the semantic engine): own node ids
(`svc:*`, `wsl:*`, `docker:engine`, `container:*`, `dockernet:*`, `vm:*`),
own edge ids (`infra:*`), merged into every WS snapshot; process nodes get
`data.infra` roles (`service_host` / `vm_backend`) so the INFRA view is
classification-driven. Events are change-only (`SERVICE_STARTED/STOPPED/
STATUS_CHANGED`, `CONTAINER_CREATED/STARTED/STOPPED/REMOVED`,
`WSL_STATE_CHANGED`, `VM_DETECTED/LOST/STATE_CHANGED`) — the first sample
establishes the baseline, so startup never storms the drawer. A stopped
service KEEPS its node (status flips); a removed container / lost VM removes
it.

Polling lives in one staggered `_infra_loop` (services 4 s, WSL 5 s, Docker
3 s, VM 4 s, `ESW_INFRA_*_INTERVAL_S`) so the main 1 s collect loop and its
Phase-20 performance behavior are untouched. Benchmark mode suppresses infra
forwarding like every other real-data channel.

### 11.3 Evidence boundaries (never crossed)

- **Windows host process ≠ guest process.** Service PID mapping targets
  psutil process nodes on the host; VM `BACKED_BY` edges point at the real
  host worker (vmwp.exe / vmware-vmx.exe / VBox*). Linux processes inside
  WSL are never presented as native Windows process nodes — only a bounded
  summary on the WSL node.
- **Container port mapping ≠ arbitrary socket attribution.** `EXPOSES`
  edges exist only when Docker proves a host port mapping AND the topology
  actually has that `LISTENING_PORT` node; `0.0.0.0`/`::` mappings match any
  listener on that port (the mapping is still proven; only the interface may
  be ambiguous). No listener → no edge, no invented port node.
- **WSL host process ≠ Linux process.** Deep inspection runs only for
  distros already confirmed running, and stays a bounded read-only summary —
  no thousands of guest processes.
- **GPU host PID attribution ≠ guest GPU utilization.** `USES_GPU` from a
  VM is emitted only when NVML's per-PID attribution proves the VM HOST
  process is on the GPU. A busy GPU alone is never attributed to a guest.
- **Identity is evidence-backed.** An unproven hypervisor process becomes
  `VIRTUALIZATION PROCESS` — never a made-up VM name. VMware names come
  from the `.vmx` file (path sanitized to the file name); VirtualBox names
  from `list runningvms`; Hyper-V names/IDs from `Get-VM`.
- **No control surface.** No service start/stop, no docker
  start/stop/exec, no `wsl --shutdown/--terminate`, no
  `VBoxManage controlvm/startvm`, no `vmrun start/stop`, no
  Start/Stop/Restart-VM. `tests/test_infra_security.py` scans the new
  modules (executable code only — docstrings legitimately name the rules)
  for forbidden control tokens and for container-ENV serialization.
- **No environment leakage.** Container ENV is never collected, parsed or
  serialized; labels are not collected; service binpaths are redacted.

### 11.4 Frontend INFRA view

Third top-level view (SYSTEM / AI / INFRA) — a pure class toggle on the same
Cytoscape instance: positions, selection, focus and layout are preserved;
no graph recreation, no forced layout. Classification-driven: infra node
kinds, `data.infra`-tagged processes, GPU nodes with infra-proven
`USES_GPU` edges, `EXPOSES`-linked listening ports, and the Windows host
node stay; unrelated noise dims. Header chips (`SERVICES n`, `WSL n`,
`CONTAINERS n`, `VM n`, `DOCKER STOPPED`) render only detected categories;
the inspector gains read-only WINDOWS SERVICE / WSL DISTRIBUTION / DOCKER
ENGINE / CONTAINER / DOCKER NETWORK / VIRTUAL MACHINE sections.

## 12. Application-level AI telemetry (v0.5.0)

The hard rule: **OS/network telemetry is NEVER converted into AI claims.**
A TCP connection is not a tool call, network bytes are not tokens, socket
throughput is not TPS, a child process is not an agent message. Normalized
AI events exist only when an application-level source proves them; every
metric stays `null` when no source provides it.

```
HERMES GATEWAY (real)          LOCAL INSTRUMENTATION (optional)   OTEL (seam)
hermes_cli.main serve ─┐       POST /api/ai-telemetry/events      gen_ai.* spans
127.0.0.1:<dyn> status ┘       (localhost-only, 64 KB, schema-    (README: NO REAL
GET /api/status + /health      whitelist, private content → 422)  PRODUCER yet)
        │                              │                              │
        ▼                              ▼                              ▼
PROVIDERS (failure-isolated: hermes-gateway-status / otel-seam / fixture)
        ▼
NORMALIZED EVENTS (AITelemetryEvent — agent/model/tool/trace/status/
                   duration/tokens/TPS; absent fields stay absent)
        ▼
BOUNDED BUFFER (500 history · 20 active traces · 100 spans · 600 s TTL)
        ▼
WS ai_activity · ai_metrics · ai_provider_status   (change-only, bounded)
        ▼
FRONTEND: transient AI_RUNTIME nodes (cap 24, TTL-decayed) + AI_CALL edges
          ONLY on proven parentage (trace ids, sem:hermes identity, exact
          LOCAL_LLM model_id) + distinct fuchsia AI signal diamonds
          (24 particles / 60 edges budgets — never the DATA particle lane)
```

### 12.1 Providers

| Provider | Source | State on this machine | Availability |
|---|---|---|---|
| `hermes` | gateway `/api/status` + `/api/health` (unauthenticated, localhost, dynamic port discovered from real process+listener evidence) | **ACTIVE** (2 real gateways) | runs ✓ sessions ✓ · tokens/TPS/tool names/MCP/traces ✗ (401-protected / not exposed) |
| `otel` | OTEL-shaped span normalization (`gen_ai.*` / `agent.*` / `tool.*`) | **AVAILABLE_NO_DATA** — READY / NO REAL PRODUCER | none until wired |
| `fixture` | deterministic scripted lifecycle; `ESW_AI_TELEMETRY_FIXTURE=1` only | never active in real mode | every event `test_only` |

Run lifecycle from the Hermes provider is count-delta evidence: the gateway
exposes `active_agents` as a COUNT, so run identity is FIFO-inferred per
gateway and every event records `count_before`/`count_after` — the counts
are real, the identity inference is documented, never hidden.

### 12.2 Ingestion — metadata sink, not a control surface

`POST /api/ai-telemetry/events` accepts ONE normalized event per request
for explicit trusted local instrumentation. Bounds: 64 KB body, pydantic
schema with `extra="forbid"` (no arbitrary JSON), metadata ≤ 32 keys with
bounded values, event-type whitelist, and a recursive privacy gate that
rejects prompt/response/reasoning/content keys and credential-shaped
values. The route has no execution, tool, model, MCP, service or shell
paths — proven by `tests/test_ai_telemetry_security.py`.

### 12.3 Evidence boundaries (never crossed)

- **process relationship ≠ tool call** — MCP/TOOL events require explicit
  application-level proof; a TCP connection to an MCP server or a child MCP
  process only produces the existing process/network relationships.
- **network bytes ≠ tokens** — token counts come only from sources that
  report them; TPS is derived only from real tokens + real duration.
- **socket throughput ≠ TPS** — never estimated from byte rates.
- **no private content** — prompts, responses, reasoning, tool arguments
  with user data, file contents and credentials are never ingested;
  `GET /api/ai-telemetry` returns metadata only.
- **no control surface** — ingestion cannot execute tools, launch agents,
  call models, send MCP commands, control services or run shell commands.
- **fixture data never mixes with real mode** — the fixture provider is
  env-gated, every event is `test_only` and labeled TEST/FIXTURE/SYNTHETIC
  (frontend: dashed rose styling + `[TEST]`); the registry reports
  `fixture_mode: true` while active.
- **bounded animation** — AI signals have their own budgets (24 particles,
  60 edges, 1.5 s decay) independent of the Phase-20 DATA particle budget;
  runtime nodes are capped (24) and TTL-decayed by the shared 1 s timer.
- **benchmark isolation** — AI telemetry WS messages are suppressed while
  the TEST-ONLY benchmark fixture is active.
