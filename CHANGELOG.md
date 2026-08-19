# Changelog

All notable changes to ENCOMM SYSTEM WATCH are recorded here.

## [0.3.0] — 2026-08-19

GPU + AI semantic observability checkpoint (Phases 14/15/16/18 COMPLETE,
Phase 17 stays NOT STARTED — seams only, never fabricated telemetry).

### Added — semantic detector framework (`backend/app/detectors/`)

- **`base.py`** — `Detection` (semantic type/name, confidence, underlying
  process ids, evidence list), `DetectionEvidence` (machine-readable source
  + sanitized detail), `SemanticRelationship` (stable `se:*` ids),
  `DetectorContext`. Confidence levels CONFIRMED/HIGH/MEDIUM/LOW — a weak
  guess is never a fact (hint-only = LOW, bare name = MEDIUM, known-path or
  live API = HIGH, direct runtime proof = CONFIRMED).
- **`registry.py`** — `SemanticDetectorRegistry`: per-detector failure
  isolation (one broken detector degrades only itself; errors surfaced via
  `/api/semantic`), hints loader for `config/detectors.json` (hints only —
  never secrets; broken file falls back to built-in defaults).
- **`redact.py`** — command-line secret redaction (`--api-key`, `--token`,
  `--password`, `--secret`, `--auth*`, `key=value` forms,
  `Authorization: Bearer …`) applied at the serialization boundary
  (topology node data, diff-event metadata) and inside detectors — credential
  values never reach the frontend or logs.

### Added — detectors

- **Phase 14 — LM Studio** (`lm_studio.py`): evidence-based detection via
  process identity + REAL socket-topology port discovery (never a blind
  port-1234 assumption; hint ports secondary). Loopback-only API probes
  (`/v1/models` + runtime `/api/0/models`), 0.5 s timeouts, ~8 s throttle,
  cached. Strict truthfulness: `LOADED` only when the runtime API proves
  it, `AVAILABLE` for `/v1/models` listings. CONFIRMED only via known path
  or live API. Emits `SERVES_MODEL` (LOCAL_LLM model nodes), `LOCAL_API`,
  `HOSTS` edges.
- **Phase 15 — Hermes** (`hermes.py`): evidence combination — exact
  executable path (`…hermes-agent\…\Hermes.exe`), Electron child
  relationships, `hermes_cli.main … serve` gateway command lines inside the
  hermes-agent venv. Family membership is identity-gated (a descendant
  joins only with its own Hermes signal — a Hermes-spawned session tree
  never absorbs unrelated children). Emits `MEMBER_OF`, real
  `PROCESS_PARENT`, `HOSTS` (gateway localhost listeners).
- **Phase 16 — MCP** (`mcp.py`): networking alone is insufficient — anchored
  `mcp` cmdline tokens, package paths
  (`node_modules/@modelcontextprotocol`, `mcp-server`, `mcp_server`),
  parent/child ancestry (Hermes gateway launcher), owned listeners for
  HTTP/SSE. Identity proven → named server; proven-but-unidentified →
  `unknown` (never a guess). stdio servers get `PROCESS_PARENT` + `SPAWNED`
  (real ancestry) — never fake network edges, never DATA particles.

### Added — GPU / VRAM (Phase 18, `backend/app/collectors/gpu.py`)

- NVML primary (`nvidia-ml-py`/pynvml — C-level, no subprocess churn);
  `nvidia-smi` CSV fallback when NVML is unavailable. Multi-GPU from the
  start (`gpu:<index>` resource nodes).
- Metrics ~1 s (utilization, VRAM used/total, temperature, power, driver,
  fan, clocks), PID attribution ~2 s (NVML compute + graphics process
  lists; per-process VRAM only when the API really provides it).
- Unavailable fields are omitted, never fabricated; the collector can never
  crash SYSTEM WATCH.
- `GPU_PROCESS_ATTACHED` / `GPU_PROCESS_DETACHED` change-only events
  (baseline established on first sample — no startup storm) + `USES_GPU`
  edges from PID attribution.

### Added — semantic engine (`backend/app/services/semantic.py`)

- Semantic resource nodes (SEMANTIC / LOCAL_LLM / GPU) and edges merged
  into every snapshot (purely additive — raw topology untouched, no
  duplicate/orphan semantic elements). Process nodes get `data.semantic`
  augmentation so the AI view is classification-driven.
- Change-only events: `HERMES_DETECTED`, `LM_STUDIO_DETECTED`,
  `MCP_SERVER_DETECTED`, `SEMANTIC_LOST`, `MODEL_LOADED`, `MODEL_AVAILABLE`,
  `GPU_PROCESS_ATTACHED`/`GPU_PROCESS_DETACHED`.
- New read-only endpoints: `GET /api/gpu`, `GET /api/semantic`. New `gpu`
  WebSocket message (~1 s live metrics). Stats now carry compact `gpu` +
  `semantic` summaries for the header.

### Added — frontend (AI view)

- **SYSTEM / AI view toggle** in the filter bar — classification-driven
  dimming (`ai-dim`), never string search; no Cytoscape recreation, graph
  state (positions, selection, focus) preserved.
- Semantic node styles (compact control-room cards: `◈ HERMES`, `◉ LM
  STUDIO`, `MCP SERVER`, `LOCAL LLM`, `GPU`) — strong styling only for
  HIGH/CONFIRMED; MEDIUM/LOW subdued. Semantic edge styles per kind;
  semantic edges never receive DATA particles.
- Inspector: SEMANTIC IDENTITY section (name, confidence badge, evidence,
  underlying PIDs, endpoint/transport/state), GPU metrics section
  (utilization, VRAM, temperature, power, driver, per-PID attribution),
  model state.
- Header AI summary chips (only detected categories), event drawer rows for
  all semantic events, legend entries for semantic nodes/edges.

### Security

- Command-line secret redaction (see `redact.py` above) — verified by
  unit tests (`tests/test_security_redaction.py`).
- Read-only preserved: no kill/stop/load/execute paths added anywhere;
  GPU/LM Studio/MCP/Hermes are observe-only.

### Tests

- Backend: 101 → **147 passed / 0 failed** (new: GPU collector — no-NVML
  degradation, single/multi GPU, metrics, PID mapping, stale PID removal,
  nvidia-smi parser; detector framework — serialization, confidence,
  failure isolation, hints; LM Studio — confirmed/API-failure/false
  positive/loaded-vs-available; Hermes — strong/ambiguous/hint-assisted/
  listener; MCP — ancestry/stdio/HTTP/false positive; semantic engine —
  change-only events, lost detections, model flips, USES_GPU edges, process
  augmentation; security redaction).
- Frontend: `npm run typecheck` + `npm run build` PASS.
- Acceptance: extended with Tests **U** (GPU), **V** (semantic framework),
  **W** (LM Studio), **X** (Hermes), **Y** (MCP), **Z** (AI view) —
  capability-aware (REAL / SKIPPED, never fixture-as-real).

### Real-machine validation (this machine)

- **GPU**: NVML source — NVIDIA GeForce GTX 1660 Ti, real utilization,
  VRAM used/total, temperature, power, driver, 20 attributed PIDs.
- **Hermes**: CONFIRMED — 9 real processes (desktop main + Electron
  children + 2 gateway profiles), evidence-backed, zero false positives.
- **LM Studio**: not running — detector unit-tested, real test SKIPPED
  (never auto-launched).
- **MCP**: no servers running — stdio/HTTP cases covered by fixture unit
  tests, real test SKIPPED.

## [0.2.3] — 2026-08-19

Final real-ETW runtime validation checkpoint (Phase 19 stays COMPLETE — every
gate re-proven live, plus one real defect fixed).

### Fixed

- **TCB stale-entry reuse race (real ETW defect found during final
  validation)** — a closed connection's Tcb correlation entry lingered until
  the 10-35 s delayed ETW removal events arrived; the kernel's TCB allocator
  can recycle the same handle value for the next connection, so its early
  byte events were attributed to the DEAD connection (measured: 18,181
  unattributed events over one back-to-back harness pair). Fix: the
  aggregator now tracks connection tuples that leave the psutil topology
  (de-bounced 2 ticks) and calls
  `EtwTcpipProvider.drop_tcb_tuples()` — matching on the 4-tuple projection
  `(pid, local_port, remote_ip, remote_port)` so wildcard-local forms are
  covered too — removing the stale entries within ~1-2 s of close. New
  diagnostic counter: `tcb_drops_from_topology`. Regression tests:
  `test_drop_tcb_tuples_removes_closed_connection`,
  `test_drop_tcb_tuples_covers_wildcard_local_form`,
  `test_drop_tcb_tuples_ignores_unrelated_entries`.
- **Acceptance harness timing (Test R/S/T could not pass under real ETW)** —
  `tools/acceptance.mjs` now implements the documented real-ETW timing
  rules: Test R harness `--watch 8` -> `35` (identity events land 10-35 s
  late, so the harness's OWN bytes now attribute mid-window), Test S harness
  `--watch 15` -> `35`, S10 (harness edge in topology) polled EARLY right
  after spawn (the edge dies ~1-2 s after close), S8/S9 use MAX-sampled
  `last_batch` per direction (single ~200 ms samples are racy, same rule as
  `verify_tier2.ps1`), T6 polls until node rates clear, and the T7 wake
  harness exit-listener is attached before the poll (it previously hung
  forever when the 3 s harness exited first).

### Verified (live, elevated, real Microsoft-Windows-TCPIP)

- `tools/verify_tier2.ps1`: **10/10 PASS**.
- Full acceptance: **76/76 PASS** (Tests A-Q, R, S, T) with REAL DATA
  particles on real edges and real directional bytes (Tests R3, S6-S10, T1-T7).
- Deterministic harness PROCESS↔PROCESS edge (port 19736) received
  **445,402,992 B fwd / 90,046,504 B rev** across 207 real ETW
  network_activity batches — correct PIDs/ports, no synthetic data.
- Backend: **101 tests passed / 0 failed**.
- Frontend: `npm run typecheck` PASS, `npm run build` PASS.
- Wildcard local-IP fallback: regression-tested (unit) but **not observed in
  this runtime window** (0 wildcard hits / 5,270 misses / 0 ambiguous across
  all windows) — the ambiguity-safe rule was never weakened to inflate hits.

## [0.2.2] — 2026-08-17

Real Windows ETW per-edge traffic correlation checkpoint (Phase 19 COMPLETE).

### Added

- **Modern Windows 11 TCPIP ETW schema support** — empirically probed and
  implemented for tcpip.sys 10.0.19041. The modern manifest delivers
  `TcpDataTransferSend` (`BytesSent`) / `TcpDataTransferReceive`
  (`NumBytes`) with ONLY a Tcb handle and byte count — no pid, no
  addresses — so per-edge attribution now works through a **TCB
  correlation cache**:
  - `TcpConnectionRundown` — bootstrap snapshot of connections that
    existed before the session started (Tcb, Pid, addresses).
  - `TcpConnectTcbComplete` / `TcpConnectTcbProceeding` — new OUTBOUND
    connection mappings (proceeding used only when it carries a real pid).
  - `TcpAcceptListenerComplete` — server-side ACCEPTED connection
    mappings (the previously open question from v0.3.0 probing).
  - `TcpDataTransferSend`/`Receive` → TCB lookup → real PID + 4-tuple +
    direction + bytes.
  - Removal/cleanup: `TcpDisconnectTcbComplete`, `TcpCloseTcbRequest`,
    `TcpAbortTcbComplete`, `TcpConnectTcbFailure`,
    `TcpConnectTcbFailedRcvRst`, `TcpConnectionTerminatedRcvRst` (by
    Tcb), `TcpRstSend` (by sockaddr tuple — its Tcb is 0x0),
    `TcpTcbStateChange` with CLOSED state, plus a bounded TTL sweep
    (5 min idle) and a hard 8192-entry cap with oldest-first eviction.
- **Modern task-name uppercase handling** — task names arrive UPPERCASE
  from pywintrace's tdh resolution; comparison is case-insensitive so
  older CamelCase manifests still work.
- **IPv4/IPv6 endpoint normalization** — `ip:port` and `[v6]:port`
  parsing, IPv4-mapped IPv6 (`::ffff:`) unwrapping, byte/int/string
  address forms.
- **UDP modern self-contained metadata** — `UdpEndpointSendMessages` /
  `UdpEndpointReceiveMessages` carry Pid, LocalSockAddr, RemoteSockAddr,
  NumBytes and are attributed directly without correlation.
- **Readiness lifecycle** (`readiness` in `/api/telemetry`) — NONE →
  INITIALIZING (session started, no real data event yet) → ACTIVE (first
  real byte event correlated) → DEGRADED (session died mid-flight). A
  started ETW session is no longer displayed as fully working per-edge
  traffic until real bytes have actually been observed.
- **`pywintrace` ctypes GUID fix (regression-guarded)** —
  `etw.ProviderInfo(...)` now receives `etw.GUID(...)` instead of a bare
  string; pywintrace passes the guid through `ctypes.byref()` which
  rejects strings. Guarded by
  `test_provider_info_receives_ctypes_guid_not_string`.
- New TCB-correlation test suite (`test_tcb_correlation.py`) + two
  full-chain modern-schema regression tests in `test_telemetry_pipeline.py`.
- **Wildcard local-IP edge attribution (final fix)** — real Windows ETW
  reports OUTBOUND client sockets with the pre-route wildcard local
  address (`0.0.0.0` / `::` / empty) while psutil topology carries the
  resolved source IP, so exact 5-tuple matching alone left
  `events_mapped_to_edges = 0` for external traffic. The aggregator now:
  - keeps the exact 5-tuple match as the always-winning first lookup;
  - falls back (ONLY for wildcard local addresses) to a
    `(pid, local_port, remote_ip, remote_port)` index built from the
    topology, attributing only when it identifies EXACTLY ONE edge;
  - never guesses between multiple candidates (ambiguity is counted, the
    event stays process-attributed);
  - exposes bounded diagnostic counters via the read-only
    `/api/telemetry/debug`: `exact_lookup_hits`,
    `wildcard_lookup_hits`, `wildcard_lookup_misses`,
    `wildcard_lookup_ambiguous`.
- Four new wildcard regression tests in `test_telemetry_pipeline.py`
  (0.0.0.0 → correct edge with directional bytes and no node fallthrough,
  IPv6 `[::]` unique fallback, ambiguous fallback never guesses,
  non-wildcard addresses never use the fallback).

### Verified

- **Real elevated ETW verification** — `tools/verify_tier2.ps1` passes
  ALL checks with REAL `Microsoft-Windows-TCPIP` ETW (elevated session):
  provider received/drained, aggregator recorded/mapped, activity
  batches, and real directional bytes in both directions.
- **98 backend tests pass / 0 failed**; frontend `typecheck` and `build`
  pass.
- **Real frontend traffic particle validation** — acceptance against the
  real elevated ETW backend: Tests R/S/T green, including REAL DATA
  particles produced by real observed byte telemetry on real edges
  (lifecycle pulses are not counted as evidence), and full
  ACTIVE → RECENT → IDLE decay (deterministic via the feed-mute test
  seam, because ambient loopback traffic on a live machine legitimately
  keeps the global maps non-empty).
- `verify_tier2.ps1` now polls directional bytes across the harness
  window (identity events arrive in delayed batches, up to ~35 s) instead
  of sampling a single ~200 ms flush window.

### Limitations

- Per-edge TCP attribution depends on TCB identity events, which arrive
  in delayed batches (empirically up to ~35 s) — short traffic bursts can
  be missed. Byte counts are per-segment accounting and approximate
  application payload sizes; retransmissions and ACK-only segments are
  excluded by construction.
- REAL Tier 2 requires an Administrator backend session; SYSTEM WATCH
  never auto-elevates. Unelevated backends truthfully report TIER0 +
  adapter totals. The synthetic provider remains opt-in
  (`ESW_TELEMETRY_PROVIDER=synthetic`), TEST-ONLY and clearly labeled —
  it is never evidence for real ETW.

## [0.2.1] — 2026-08-16

Tier-2 traffic pipeline correctness and activity-decay checkpoint.

### Fixed

- **ETW provider → aggregator wiring (Bug #1)** — the runtime telemetry
  loop called `aggregator.flush()` but never drained the provider, so real
  ETW events accumulated in the provider's bounded queue and never reached
  the WebSocket even when TIER2 was active. The loop now runs
  `_telemetry_tick()` each window: `provider.drain() → record_many() →
  flush()`. Regression-guarded by a full-chain test (fake ETW event →
  provider callback → drain → record_many → aggregator → non-zero
  directional edge bytes).
- **Frontend activity decay (Bug #2)** — `EdgePulseOverlay` only pruned
  stale activity when the next `network_activity` batch arrived; since the
  backend stops sending batches when traffic stops, stale entries (and the
  rAF loop) could persist indefinitely. The rAF loop now prunes the
  activity map by wall-clock age (`RECENT_MS`), so ACTIVE → RECENT → IDLE
  happens with no further backend packets, and the loop cancels its own
  rAF when activity/particles/pulses/recents are all empty.
- **Edge visual-state decay (Bug #2b)** — `GraphController`'s
  `actLow/actMed/actHigh` styling (edge brightness/thickness/glow) is now
  cleared by one shared 1 s activity-decay scheduler (never a timer per
  edge) purely by time, and stale per-process ↓/↑ net rates are cleared
  with it. The `applyActivity` prune also strips styling when it drops a
  map entry (previously a stale edge could stay lit forever).
- **Provider-failure resilience** — the telemetry health probe is
  exception-guarded and safe when capability state is unset; a crashing
  provider can no longer kill the monitoring loop. Tested with a
  fail-then-recover provider (loop survives, telemetry flows again).

### Added

- **Batch ingestion** — `ActivityAggregator.record_many()` ingests drained
  provider events with a single lock acquisition per ~200 ms window (no
  lock-per-event under high traffic).
- **Telemetry debug counters** — `GET /api/telemetry/debug` (localhost-only,
  counts never payloads): provider events received/drained/dropped + queue
  depth, aggregator events recorded/mapped-to-edges/mapped-to-nodes/
  unattributed/batches-emitted, last batch's directional byte totals.
- **Synthetic logical-TIER2 provider** — `backend/app/telemetry/synthetic.py`,
  activated ONLY via `ESW_TELEMETRY_PROVIDER=synthetic`. Scans the real
  socket table (2 Hz, so it never interferes with the collector's psutil
  attribution), caches matching ESTABLISHED loopback tuples for a target
  port, and emits fabricated metadata events for those real tuples through
  the full production chain. Capability is labeled `SYNTHETIC TEST PROVIDER
  (logical)` — never presented as real ETW, never the default.
- **`tools/verify_tier2.ps1`** — manual elevated verification script: never
  auto-elevates, prints the required Administrator message and exits
  cleanly when non-admin; when elevated, checks `/api/telemetry`, runs the
  traffic harness, and asserts the full counter chain (PASS/FAIL).
- **Tests** — provider→aggregator integration tests (the missing-wiring
  guard), runtime-tick wiring test, provider-failure recovery test, queue
  boundedness/drop counting, attribution counters, synthetic-chain test,
  debug-endpoint shape test. Backend suite: 61 → **69 tests**.
- **Acceptance Tests S and T** — S: provider → drain → aggregator →
  WebSocket → GraphController → particles with bidirectional bytes (runs
  for any TIER2 backend, distinguishes LOGICAL/SYNTHETIC from REAL
  ELEVATED via the capability source); T: decay — activity map empties,
  particles stop, act* styles clear, node rates clear, rAF stop mechanism
  verified deterministically (test-only `testMute`/`testForceIdle` UI
  hooks), idle state wakes on new traffic. Screenshot guard
  (`ESW_KEEP_SCREENSHOT=1`) keeps `docs/screenshot.png` a real capture.

### Verified

- Backend: **69 pytest cases green** (`python -m pytest tests -q`).
- Frontend: `npm run typecheck` and `npm run build` green.
- Acceptance (live Windows 11, production build):
  - NORMAL UNELEVATED RUN: **58/58** (A–R; TIER0, elevation required,
    zero fabricated per-edge activity, adapter totals; S/T correctly
    skipped).
  - SYNTHETIC LOGICAL TIER2 RUN (`ESW_TELEMETRY_PROVIDER=synthetic`):
    **76/76** (A–T; S: 7 704 events recorded → 7 132 mapped to edges,
    224 batches, fwd=rev=49 152 B in the last batch, particles moving;
    T: full ACTIVE → RECENT → IDLE decay, rAF stop mechanism, wake on
    new traffic).
- Counters observed (synthetic run): provider received 7 736 / drained
  7 704 / dropped 0; aggregator recorded 7 704, mapped to edges 7 132,
  unattributed 0, batches emitted 224; queue depth 0 throughout.

### Known limitations (0.2.1)

- **REAL Windows ETW TIER2 has NOT been validated end-to-end** — the
  session was not elevated, so per-edge telemetry truthfully reports
  TIER0 with `elevation_required`. The synthetic provider proves the
  production chain logically; a future elevated Administrator run
  (`tools/verify_tier2.ps1`) is required to validate real
  Microsoft-Windows-TCPIP ETW bytes.
- TCP only for per-edge byte attribution (ETW TCPIP provider); UDP sockets
  show lifecycle activity only.
- On a busy machine the pulse overlay's rAF loop may keep running on real
  lifecycle events (that is designed behavior); DATA particles, edge
  styles and activity state still fully decay, and the loop stops whenever
  the event feed goes quiet.

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
