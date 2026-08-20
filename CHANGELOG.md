# Changelog

All notable changes to ENCOMM SYSTEM WATCH are recorded here.

## [1.0.2] — 2026-08-20

VISUAL TOPOLOGY FIDELITY RELEASE — frontend-only composition/routing pass.
No observability semantics, data model, collectors, or backend behavior were
changed; no synthetic nodes/edges were added; telemetry truthfulness is
untouched (banked stopped services keep their exact truthful counts and
remain fully clickable/inspectable).

### Added
- **Connection-aware rack composition** — the initial layout is no longer a
  generic force-directed scatter. Real graph structure (union-find connected
  components over real edges) drives a deterministic "wiring rack": anchors
  and the connected core lead the left bands (component-first, degree-desc
  ordering), stopped services and unconnected populations trail as compact
  dim banks on the right. Column count is solved per viewport aspect so the
  map fills the screen at any window size.
- **Lane-aware edge routing / bundling feel** — edges sharing the same source
  and target rack band bow in the same direction with coordinated magnitude
  (deterministic per-edge jitter keeps them distinct; never a fused fake
  line), producing fan-out/fan-in corridors without inventing edges.
- **AG topology acceptance section** (AG1–AG12) — objective connectedness
  gates: viewport nodes/edges, incident-edge fraction, connected-core
  placement, orphan bank compaction, FIT ALL / RELAYOUT re-validated, view
  keep-visible, no fake nodes, real particles on real activity, truthful
  banked-service counts.

### Changed
- Edge presence is more visually dominant: base opacity 0.55→0.82, wider
  color-coded category edges, brighter active/recent traffic lanes; nodes
  recede (darker fills, thinner borders, tighter cards).
- Stopped services / orphan populations: compact `svc-bank` / `orphan-bank`
  styling in the default SYSTEM view — same nodes, same counts, far lower
  visual weight.
- AI/INFRA view dimming now lives in the Cytoscape stylesheet (canvas); the
  old DOM-CSS-only rule could never affect canvas elements (silent no-op).
- Incremental re-composition now also triggers on meaningful live batches on
  large graphs (the batch arg is actually forwarded).

### Fixed
- `composeRackLayout` hardened against dangling-edge snapshot/event races
  (edge present before its endpoint nodes land) — the rack can no longer
  silently fall back to a scatter layout on live churn.

## [1.0.1] — 2026-08-20

CRITICAL UI HOTFIX (released after v1.0.0). v1.0.0's automated acceptance was
green, but on the user's real browser the central graph canvas rendered blank
while the live top status (processes, connections, services, ETW, GPU, Hermes)
clearly kept collecting REAL data. Root cause was a camera/layout regression,
not a collector defect — no observability or backend behavior was changed.

### Fixed
- **Blank-graph camera (root cause)** — the initial camera was being placed
  based on the destructive `OVERVIEW_MIN_ZOOM = 0.55` clamp and, on the
  `animate=false` path, was fitted before fCoSE reached its FINAL node
  positions. On a ~700–800 node real graph the forced 0.55 zoom (~5× past the
  true fit of ~0.10) left only a sparse, `randomize`d slice on screen — which
  could land on near-empty space → the blank user view.
- **Camera now fits after REAL layout completion** — the initial fit runs only
  on the Cytoscape `layoutstop` event (the single source of layout
  completion), never before final positions. Removed the racy manual
  `if (!animate) onStop()` call.
- **FIT ALL truly fits the visible topology** — `fit()` now fits
  `elements(':visible')` with no zoom floor on top, so FIT ALL can never shove
  content off-screen.
- **Adaptive / no destructive hard zoom floor** — the 0.55 hard floor is
  replaced by an adaptive overview: it zooms toward readable-label zoom only
  while at least 50% of the visible NODES stay inside the viewport, binary
  searching the largest safe zoom. Large graphs keep their true fit.
- **Blank-graph safety recovery** — one-time, view-only recovery
  (resize → fit visible) if visible nodes exist but none intersect the
  viewport; it never re-arms and never fights user pan/zoom after initial
  placement.
- **Container resize audit** — a ResizeObserver now calls `cy.resize()` on
  every graph-container geometry change (not just window resize), so layout
  is never computed against a 0/stale container.
- **`viewportHealth()` diagnostic** — read-only
  `totalNodes/visibleNodes/viewportNodes/edges/zoom/pan/graphBoundingBox/
  container/dimensions/layoutState`, exposed via the existing test surface:
  "nodes exist" is now objectively distinguished from "nodes are visible".
- **Viewport visibility acceptance added (AF)** — AF1–AF14 prove the REAL
  production graph is on screen: >0 visible nodes, >0 viewport-intersecting
  nodes, meaningful coverage on large graphs, fit-after-layout, FIT ALL
  returns offscreen graph without a destructive floor, RELAYOUT ends visible,
  container sizing, filter/view toggle safety, resize, production build, no
  fixture nodes.

VALIDATION: frontend typecheck + build PASS; full 222+ acceptance
(base + new AF) 0 failures — 238 passed / 0 failed; backend suite
262 passed / 0 failed (only the version field changed in main.py — no
behavior/schema change); real-machine regression smoke PASS; no continuous
relayout/fit loops. Read-only observability unchanged.


## [1.0.0] — 2026-08-20

FIRST STABLE RELEASE (Phase 23 COMPLETE — release / packaging). The validated
v0.6.0 control-room observatory is now a clean, reproducible, documented
v1.0.0: the same real-data engine, wrapped in a production install/start
workflow and release documentation.

### Release / packaging

- **Canonical production launcher** — `Start-SystemWatch.ps1` (plus a thin
  `Start-SystemWatch.bat` wrapper): resolves the project directory (path
  spaces safe), verifies the venv and the production build, single-instance
  port check (a healthy existing instance is reported, never duplicated; a
  foreign listener fails safely without killing anything), stale
  `esw-telemetry` ETW session warning (detected and explained, never
  stopped automatically), no auto-elevation (Administrator state reported
  truthfully; non-admin gets the TIER0 explanation), backend child output
  captured to `backend\esw-backend.log` so startup failures are
  diagnosable, live capability banner (`Mode: LIVE`, `ETW: TIER0/TIER2`,
  `Read-only: YES`, UI URL), optional `-NoBrowser`, and clean shutdown of
  only the processes it started.
- **One-time setup script** — `Setup-SystemWatch.ps1`: creates
  `backend\.venv`, installs backend dependencies, `npm ci`, production
  build. It never installs system software, edits the firewall, changes
  Windows security, touches Docker/WSL/hypervisors/GPU drivers, enables
  Windows features, or auto-elevates — missing prerequisites are explained.
- **Version 1.0.0** — FastAPI app version, `frontend/package.json` and
  `frontend/package-lock.json`.
- **Release docs** — `docs/RELEASE_1.0.0.md` (what it is, capabilities,
  platform, quick start, limitations, security, validation);
  README rewritten quick start (Setup → Start → open), Windows-11-primary
  platform statement, optional-capabilities matrix, expanded known
  limitations; PHASES.md gains Phase 23; ARCHITECTURE corrected (Phase 17
  no longer listed as NOT STARTED — it is FUNCTIONAL since v0.5.0).
- **Consistency audit** — viewport claims unified on a single descriptive
  measurement (~85 % canvas in CHANGELOG/PHASES; acceptance AD uses a
  ≥72 % objective floor); benchmark/fixture modes remain explicitly
  TEST-ONLY and opt-in; release starts in REAL mode with no synthetic
  state.

### v1.0.0 carries forward (unchanged engine, all real)

- Graph-first control-room UI (fullscreen Cytoscape canvas, SYSTEM/AI/INFRA
  views, live event drawer, inspector, search/filters/family grouping)
- Windows processes + services + TCP/UDP topology
- REAL Windows ETW TIER2 per-edge bytes + directional DATA particles
- NVIDIA GPU/NVML telemetry
- Hermes / LM Studio / MCP semantic detection with evidence-backed confidence
- Real application-level AI telemetry pipeline (Hermes gateway status API,
  OTEL seam, optional local ingestion — Phase 17 FUNCTIONAL)
- WSL / Docker / Hyper-V / VMware / VirtualBox read-only observability
- 2000-node validated benchmark mode + long-run bounded memory
- Read-only / metadata-only guarantees — zero control surfaces

VALIDATION: backend 262 passed / 0 failed; frontend typecheck + build PASS;
full acceptance 213 passed / 0 failed (+ release AE checks); real-machine
REAL validation green; launcher + setup scripts verified from a
spaces-containing path.

## [0.6.0] — 2026-08-20

FINAL UI FIDELITY / CONTROL-ROOM VISUAL PASS (Phase 22 COMPLETE). The visual
shell was rebuilt to match the supplied SYSTEM WATCH reference in character
and composition — NOT pixel-perfect and never copying its branding: ENCOMM
branding and this application's own functionality remain original. The
observability engine is untouched: no new collectors, no ETW changes, no
fabricated telemetry. Every particle still requires real evidence.

### UI / visual (frontend only)

- **Fullscreen graph-first shell** — the Cytoscape canvas now owns ~85% of
  the viewport; no persistent side panels; the inspector is a temporary
  right overlay, closed by default.
- **Compact header** (~34 px) — ENCOMM SYSTEM WATCH + `LIVE · READ ONLY`,
  SYSTEM/AI/INFRA tabs moved into the header as small technical buttons
  (same single Cytoscape instance; toggling only adds dim classes), compact
  status chips (PROC/CONN/LISTEN, CPU/RAM/FEED, NET source-labeled,
  TRAFFIC tier chip).
- **Dense node cards** — all node kinds scaled down ~30% (PROCESS 108×38,
  SYSTEM 150×40, SERVICE 112×36, GPU 118×38, …), mono micro-typography
  (11–13 px model), strict information hierarchy per zoom bucket:
  FAR = label-free wireframe blocks · MID = `NAME` + `PID` · NEAR = PID,
  CPU%, MEM. Tiny endpoint/port labels sit above their shapes.
- **Fine curved wiring** — edges are hairline (1 px) `unbundled-bezier`
  with a deterministic per-edge control point (`edgeCurveDist`), low idle
  opacity (0.55), brighter/thicker only under real observed activity,
  small arrowheads; far zoom drops arrow geometry and thins edges further.
- **Organized-chaos density** — fcose retuned (nodeRepulsion 14000,
  idealEdgeLength 125, gravity 0.10, padding 20) so hundreds of real
  entities fit on screen at once with zero card overlap (measured 0 pairs
  < 30 px) and visible edge routes between clusters.
- **Initial camera** — fit now clamps to a 0.55 zoom floor: the opening
  view is a broad, dense, readable overview (labels visible), never a
  zoomed-out unlabeled haze.
- **Label readability over wiring** — subtle dark text backing
  (`text-background-opacity 0.55`) keeps dense lines from slicing through
  card text.
- **Reference shell details** — flat near-black canvas (#06090f) with a
  faint 48 px micro-grid + 8 px dot texture (no vignette); legend collapsed
  to a `▸ LEGEND` chip (on-demand); new 18 px bottom hint bar
  (`READ ONLY · CLICK NODE INSPECT · SCROLL ZOOM · …`) above the thin
  `LIVE EVENT DRAWER` strip (28 px collapsed / 210 px expanded).
- **Live signals unchanged in truthfulness** — REAL DATA particles
  (cyan/amber, actual bytes) and AI application signals (fuchsia diamonds,
  proven telemetry) keep their evidence distinction; no decorative fake
  motion was added.
- **Performance preserved** — same graph instance, label cache, size-scaled
  layout policy, bounded particles, bounded event DOM, idle rAF stop, edge
  LOD; no box-shadows/blur/backdrop-filter on graph elements.

### Fixed

- **cytoscape 3.34 label default** — the bundle does not apply the
  `data(label)` default mapping; labels are now explicitly mapped
  (`label: 'data(label)'`), which is what makes every card render its
  name/PID (previously cards could render blank at overview zoom).

### Tests / acceptance

- Acceptance: drawer overlay math updated for the 28 px collapsed strip;
  new **AD — UI FIDELITY / SHELL** section (13 objective checks: graph
  canvas ≥ 72 % viewport height, header ≤ 56 px, filter bar ≤ 34 px,
  drawer collapsed by default, hint bar present, inspector closed by
  default, no sidebar, compact node dimensions, particle-overlay alignment
  after resize, legend collapsed, single Cytoscape instance across
  SYSTEM/AI/INFRA, AI dimming, 1600×900 no overflow).
- 3+ screenshot comparison iterations against the supplied reference
  (1600×1000 CDP captures on the REAL live graph), final real-data
  screenshot updated in `docs/screenshot.png`.
- Backend tests: NOT REQUIRED (no backend/shared schema changes).
- Phase 17 remains **FUNCTIONAL** (deep real producer telemetry pending).

## [0.5.0] — 2026-08-20

REAL application-level AI telemetry checkpoint (Phase 17 FUNCTIONAL — real
producer integration pending). SYSTEM WATCH now distinguishes OS/network
telemetry from APPLICATION AI telemetry, with a normalized metadata
pipeline, a real read-only Hermes gateway adapter, a bounded local
ingestion endpoint, an OTEL seam and a TEST/FIXTURE provider that can
never mix with real mode.

### Evidence boundary (documented in README + ARCHITECTURE)

- OS network telemetry != application AI telemetry
- process relationship != tool call
- network bytes != tokens
- socket throughput != TPS

### Added — normalized AI telemetry (`backend/app/ai_telemetry/`)

- `models.py` — `AITelemetryEvent` normalized schema (event_id, source,
  event_type, agent/model/tool ids, trace/span ids, status, duration,
  token counts, TPS — every metric optional, `None` when the source does
  not provide it; TPS is derived ONLY from real token + duration
  evidence). Privacy gate `contains_private_content`: prompt/response/
  reasoning/content/credential-shaped keys and values are rejected.
- `base.py` — failure-isolated `TelemetryProvider` with the four
  truthful states: ACTIVE / AVAILABLE_NO_DATA / UNAVAILABLE / DEGRADED
  and a per-metric availability matrix.
- `buffer.py` — bounded: 500 event history, 20 active traces, 100 recent
  spans, 600 s run TTL; trace correlation via real `trace_id` /
  `parent_span_id` (no invented parentage); tokens/s only from real
  non-fixture token events.
- `hermes_provider.py` — REAL read-only adapter. Discovers
  `hermes_cli.main … serve` gateway processes + their localhost
  listeners (real socket evidence) and polls the unauthenticated
  `GET /api/health` + `GET /api/status` endpoints. Emits
  `AGENT_RUN_STARTED/FINISHED` from real `active_agents` count deltas
  (run identity FIFO-inferred — the gateway exposes counts only; the
  metadata always records count_before/count_after), `AI_ERROR` on
  platform/component error states (change-only). Verified LIVE on this
  machine: 2 real gateways discovered (default + encomm-system-watch),
  provider ACTIVE, 1 real session observed.
- `otel_provider.py` — lightweight OpenTelemetry-compatible seam
  (gen_ai.* / agent.* / tool.* attribute subset → normalized events).
  Status: **READY / NO REAL PRODUCER** (unit-tested; no producer wired).
- `fixture_provider.py` — TEST-ONLY deterministic scripted lifecycle
  (run → model request → tool call → MCP call → run finish), env-gated
  (`ESW_AI_TELEMETRY_FIXTURE=1`), every event `test_only`, registry
  reports `fixture_mode: true`; never active in real mode.
- `registry.py` — async poll loop (5 s), per-provider failure isolation,
  WS publishing on change only.

### Added — localhost ingestion + API + WebSocket (metadata only)

- `POST /api/ai-telemetry/events` — OPTIONAL local ingestion for
  explicit trusted instrumentation. Localhost-only, 64 KB bound,
  schema-whitelisted (unknown fields rejected), metadata ≤ 32 bounded
  keys, private content rejected. **OBSERVE ONLY**: no tool/agent/model/
  MCP/shell/execution control paths (security-tested). All security
  tests in `backend/tests/test_ai_telemetry_security.py`.
- `GET /api/ai-telemetry` — provider states, active runs, metrics,
  bounded caps, fixture mode.
- WS message types `ai_activity` / `ai_metrics` / `ai_provider_status`
  — change/activity only, never full histories; suppressed during
  TEST-ONLY benchmark mode so synthetic and real data can never mix.

### Added — frontend

- Bounded transient AI runtime nodes (AGENT RUN / MODEL REQUEST /
  TOOL CALL / MCP CALL, hexagon, role-colored; TEST/FIXTURE dashed +
  `[TEST]` label; hard cap 24 nodes, TTL decay via the shared 1 s
  timer). Edges (`AI_CALL`) only on proven evidence: backend trace
  parentage, `sem:hermes` agent identity, exact LOCAL_LLM `model_id`
  match. Runtime nodes survive snapshot refreshes.
- Distinct AI signal lane in the canvas overlay: fuchsia diamonds
  (rose for TEST) — never the DATA particle lane; own budgets
  (24 particles / 60 signal edges), 1.5 s decay, idle-stops.
- Inspector: AI TELEMETRY section (role/status/model/tokens/TPS/
  latency/tools/trace — only real fields).
- Event drawer rows: AI RUN START/END, MODEL REQUEST, TOOL CALL,
  MCP CALL, AI ERROR, AI RETRY (TEST-labeled).
- Header: compact AI telemetry chips only when real data exists
  (provider state, RUNS, TOKENS/s, TOOLS, MODEL).

### Validation

- Backend: 262 tests passed / 0 failed (225 baseline + 37 new).
- Frontend: typecheck + production build PASS.
- Acceptance: 170 baseline + 30 new AC checks (AC0–AC29) covering
  provider state, bounded ingestion, invalid/private/oversized payload
  rejection, trace correlation, runtime node lifecycle + TEST labeling,
  drawer rows, snapshot survival, deterministic cleanup, AI signal
  budgets, env-gated fixture backend with return to real mode.
- REAL: Hermes semantic YES; deep telemetry interface found (gateway
  status API — status/counts only); tokens/TPS/tool names
  **UNAVAILABLE** (401-protected / not exposed without auth) — reported
  truthfully, never estimated.

## [0.4.0] — 2026-08-20

Infrastructure observability checkpoint (Phases 08 + 09 COMPLETE, Phase 21
COMPLETE). SYSTEM WATCH now understands the machine's infrastructure layer:
Windows Services, WSL distributions, Docker Engine/containers and local
virtual machines — all strictly read-only, evidence-driven, change-only.

### Added — Windows Services (Phase 08, `backend/app/collectors/services.py`)

- Full service enumeration via psutil (`win_service_iter` — no control APIs
  are ever called): name, display name, status, start type (compact
  Auto/AutoDelay/Manual/Disabled), account, binary path (redacted),
  description, PID. Unavailable fields are omitted, never fabricated.
- Heavy metadata is TTL-cached (30 s); status + PID refresh every poll
  (default 4 s, `ESW_INFRA_SERVICES_INTERVAL_S`).
- Per-service failure isolation: a service whose state cannot be read is
  marked `inaccessible` with its name preserved — never a crash, never
  invented data.
- **Shared-host truthfulness**: N services in one svchost.exe produce N
  `HOSTED_BY` edges to the ONE real process node — never one fake process
  per service (verified live: BFE + Windows Defender Firewall share
  pid 3624; 119 running PID mappings on this machine).
- Compact `SERVICE` nodes (`⚙ <display name> / RUNNING · Auto · PID n`) and
  change-only events: `SERVICE_STARTED` / `SERVICE_STOPPED` /
  `SERVICE_STATUS_CHANGED` (first sample is the baseline — no startup storm).

### Added — WSL (Phase 09, `backend/app/collectors/wsl.py`)

- Read-only discovery only: `wsl --list --verbose` / `--list --running` /
  `--status` (UTF-16 resilient decoding). Distribution name, state,
  version, default marker.
- **A STOPPED distribution is never started and never inspected
  internally.** Bounded internal summaries (process count, kernel, top
  process names, memory, listening TCP ports) run ONLY for distributions
  already confirmed RUNNING, via one minimal read-only command with a hard
  timeout; any failure degrades to `summary: null`.
- `WSL` nodes (`⬡ Ubuntu / STOPPED / WSL2`) + `HOSTS` edges from the
  Windows host; `WSL_STATE_CHANGED` events on real flips only.

### Added — Docker Engine + containers (Phase 09, `backend/app/collectors/docker.py`)

- Engine detection through the installed `docker` CLI (Docker Desktop's
  local npipe — never TCP 2375, never insecure API access). Engine down →
  truthful `NOT_RUNNING` with empty containers — the app keeps running.
- Read-only `docker ps -a` JSON stream: short id, name, image, state,
  status, created, host port mappings (parsed, including no-host-mapping
  forms), networks. Host PIDs via **targeted** `docker inspect --format
  '{{.State.Pid}}'` only (bounded).
- **No environment leakage**: container ENV is never collected, parsed or
  serialized (full inspect JSON is never requested); labels are not
  collected. Verified by unit tests.
- `DOCKER_ENGINE` / `CONTAINER` / `DOCKER_NETWORK` nodes; `HOSTS` (engine →
  container), `EXPOSES` (container → real topology LISTENING_PORT node,
  only when the host mapping is proven AND the listener exists — nothing
  invented), `CONNECTED_TO` (container → network, proven by metadata).
- Change-only events: `CONTAINER_CREATED` / `CONTAINER_STARTED` /
  `CONTAINER_STOPPED` / `CONTAINER_REMOVED`.

### Added — Virtual machine observability (Phase 21, `backend/app/collectors/vm.py`)

- Generic VM detector framework: Hyper-V (read-only CIM `Get-VM` + vmwp.exe
  GUID→PID host-process mapping), VMware (`vmware-vmx.exe` + .vmx evidence,
  read-only `vmrun list` when installed), VirtualBox (`VBoxManage list
  runningvms` only), plus generic hypervisor-process detection
  (vmwp/vmx/VBoxHeadless/VirtualBox/qemu).
- **Identity is evidence-backed**: VM name only when provable; an
  unproven hypervisor process becomes `VIRTUALIZATION PROCESS` (LOW/MEDIUM
  confidence) — never a made-up name. VM paths are sanitized (file name
  only). Provider/name/state/confidence/evidence schema per spec.
- `VM` nodes + `HOSTS` (host → VM), `BACKED_BY` (VM → real host process),
  and `USES_GPU` **only when NVML PID attribution proves the VM host
  process uses that GPU** — guest GPU use is never inferred.
- Change-only events: `VM_DETECTED` / `VM_LOST` / `VM_STATE_CHANGED`.

### Added — InfraEngine + INFRA view

- `backend/app/services/infra.py`: additive semantic layer (own ids
  `svc:*`/`wsl:*`/`docker:*`/`container:*`/`dockernet:*`/`vm:*`, edges
  `infra:*`) merged into every snapshot; process nodes get `data.infra`
  roles (service_host / vm_backend) so the INFRA view is
  classification-driven. Change-only events; first sample = baseline.
- Staggered `_infra_loop` (services 4 s, WSL 5 s, Docker 3 s, VM 4 s) —
  the main 1 s collect loop is untouched; every collector degrades
  independently; benchmark mode suppresses forwarding so synthetic and
  real data never mix.
- `GET /api/infra` — full read-only infra state (counts, shared hosts,
  distributions, engine, VMs, summary). Stats carry a compact `infra`
  block for header chips.
- **Frontend**: third top-level view **SYSTEM / AI / INFRA** (same
  Cytoscape instance, positions/selection preserved, no layout forced —
  only a class toggle). Infra node/edge styles (⚙ amber, ⬡ cyan, ◆ blue/
  teal, ▣ magenta; HOSTED_BY/EXPOSES/CONNECTED_TO/BACKED_BY), inspector
  sections per kind, header chips (`SERVICES n`, `WSL n`, `CONTAINERS n`,
  `VM n` — only detected categories; `DOCKER STOPPED` when the engine is
  down), event-drawer rows, legend entries.

### Security

- Strictly read-only: no service start/stop/restart, no docker
  start/stop/exec, no `wsl --shutdown/--terminate`, no VM control commands
  (`VBoxManage controlvm/startvm`, `vmrun start/stop`, Start/Stop-VM). A
  new security test suite (`tests/test_infra_security.py`) scans the new
  modules for forbidden control tokens and ENV serialization so a
  regression can never silently reintroduce a control surface.
- VM paths sanitized; service binpaths redacted; container ENV/labels never
  serialized; no software is ever started to force a positive result.

### Tests

- Backend: 171 → **221 passed / 0 failed** (new: services collector —
  enumeration/PID mapping/shared host/inaccessible/state change; WSL —
  no-WSL/UTF-16 distro list/stopped-never-inspected/running bounded
  summary/parser resilience; Docker — no engine/engine available/ports/
  no-ENV-leakage; VM — no hypervisor/Hyper-V/VMware/VirtualBox fixtures/
  ambiguous process/no-control-commands; InfraEngine — classifications/
  no duplicates/no orphan edges/change-only events/EXPOSES matching/GPU
  boundary; security scans).
- Frontend: `npm run typecheck` + `npm run build` PASS.
- Acceptance: new **Test AB — INFRASTRUCTURE** section (REAL services
  count/status/PID mappings/shared host; REAL WSL discovery with
  stopped-never-inspected; truthful Docker SKIPPED when engine down;
  truthful VM SKIPPED with providers detected; INFRA view dimming +
  SYSTEM→INFRA→AI→SYSTEM same-instance cycle; service inspector; header
  chips; no benchmark leftovers).

### Real-machine validation (this machine)

- **Services**: REAL — 293 enumerated (119 running / 174 stopped), 119 PID
  mappings, shared-host proven (BFE + mpssvc → pid 3624).
- **WSL**: REAL — Ubuntu (default, WSL2) + docker-desktop, both STOPPED;
  internal summary correctly SKIPPED (never auto-started).
- **Docker**: client installed, engine NOT running → truthful
  `REAL DOCKER: SKIPPED — ENGINE NOT RUNNING`; unit-tested with fixtures.
- **VMs**: Hyper-V (enabled, vmms running) + VMware Workstation +
  VirtualBox 7.2 installed; zero VMs running → truthful
  `REAL VM VALIDATION: SKIPPED — NO RUNNING VM`; all three providers
  fixture-tested.

### Robustness fixes found during live validation (all regression-tested)

- **WSL empty-enumeration truthfulness**: the WSL service (LxssManager) can
  return EMPTY `wsl --list --verbose` output while `--status`/`--running`
  still answer; the collector previously treated that as "no WSL installed"
  and silently wiped the WSL nodes from the graph. Now: plain `wsl --list`
  fallback probe recovers the distributions; when both probes fail the
  failure is surfaced truthfully as an error (`wsl.py` + 3 tests).
- **Last-good infra state**: a transient collector failure no longer wipes
  the graph to "not installed / engine UNKNOWN" — the loop keeps the last
  good observation (error text set) for services/WSL/Docker/VM.
- **First-tick baseline**: `infra_engine.update()` never runs before the
  first real snapshot exists (an empty-services baseline made every
  running service look "new" → a 120-event startup storm).
- **`unknown` is not a transition**: a transiently unreadable service
  status (AccessDenied under load) flipping to/from `running` no longer
  emits fake SERVICE_STARTED/STOPPED events.
- **ETW diagnostic counters**: `/api/telemetry/debug` now exposes
  `tuple_map_size` / `wildcard_map_size` (aggregator evidence-map size) so
  attribution freezes are distinguishable from empty topology.
- **Real-ETW validation note**: this machine's acceptance runs DO reach
  real TIER2 (elevated terminal); a stale `esw-telemetry` ETW session left
  over from a killed backend freezes edge attribution (provider receives,
  TCB hits climb, `events_mapped_to_edges` stays 0) — the documented
  clean-session procedure (kill backend → `logman stop esw-telemetry -ets`
  → restart) is mandatory before acceptance. Verified: with a clean
  session table, real per-edge mapping and DATA particles flow again
  (`events_mapped_to_edges > 0` on ambient traffic within seconds).

## [0.3.1] — 2026-08-20

Large graph performance + long-run stability checkpoint (Phase 20
IN PROGRESS→COMPLETE). Validates 500/1000/1500/2000-node graphs with a
TEST-ONLY deterministic benchmark mode, makes ordinary updates incremental,
adds a layout policy, semantic zoom LOD, bounded prioritized particles,
development perf diagnostics, a read-only ETW attribution health detector,
and a bounded event-drawer DOM.

### Added — TEST-ONLY benchmark mode (`backend/app/services/benchmark_graph.py`)

- Deterministic synthetic large-graph fixture generator: same node count +
  seed → identical graph (default seed = `node_count * 7919`). Realistic
  shapes — process nodes (incl. families: parent + ≥2 same-name children),
  service/system nodes, external endpoints (TEST-NET documentation IP
  ranges), listening ports, local endpoints, GPU + semantic/model nodes —
  and edges: LOCALHOST, EXTERNAL, PROCESS_PARENT, LISTEN, USES_GPU,
  SERVES_MODEL, SPAWNED, HOSTS, MEMBER_OF. Proportionate density
  (~0.5–3 edges/node).
- **Every element is explicitly labeled TEST/BENCHMARK**: `test_only` /
  `benchmark` flags, synthetic pids (≥400000), `SYNTHETIC\bench` identity —
  benchmark data can never be mistaken for, or mixed with, real telemetry.
- `BenchmarkMode` state holder: inactive by default; `POST
  /api/benchmark/activate` is header-gated (`X-ESW-Benchmark: test-only`);
  `POST /api/benchmark/deactivate`; `GET /api/benchmark/status`. While
  active the WS snapshot serves only the labeled fixture (mode
  `benchmark`) and real event/activity/GPU/semantic messages are suppressed
  — the real collectors keep running underneath, so deactivation restores
  the live graph instantly. No system control paths.

### Added — read-only ETW attribution health detector (`backend/app/services/etw_health.py`)

- Watches provider counters (`events_received`) vs aggregator
  `events_mapped_to_edges`. When provider events keep arriving but edge
  attribution stays frozen for an extended period (default 45 s,
  `ESW_ETW_HEALTH_FREEZE_S`) while tracked edges exist → state machine
  OK → WATCHING → **DEGRADED** with a truthful `ETW ATTRIBUTION DEGRADED`
  warning (one WS `ETW_HEALTH` event per transition; also exposed under
  `health` in `/api/telemetry/debug`).
- **Strictly read-only**: never stops logman, never restarts ETW sessions
  or the backend, never kills processes — the operator restarts manually.

### Added — development performance diagnostics (frontend)

- `PerfMonitor` (`frontend/src/graph/PerfMonitor.ts`): measured timings
  along the update path — WebSocket message processing per type, cytoscape
  snapshot/update duration, layout duration, activity batch duration, AI
  toggle, search, filter; element counts + visible counts; overlay particle
  state; animation-frame sampling (fps); memory trend (Chromium
  `performance.memory`, 5 s samples). Exposed as `window.__esw_perf`
  (`snapshot()`, `heapSeriesMB()`).
- `PerfPanel` debug overlay rendered ONLY in benchmark mode (or forced via
  `window.__esw_perf.show()`) — the normal production UI stays clean.

### Changed — incremental update path (`GraphController`)

- **Label cache**: process labels are only rewritten when the rendered text
  actually changes — metric ticks no longer re-dirty every node label every
  second on large graphs.
- **Layout policy**: initial layouts get a size-scaled iteration budget
  (2000 → 1300 → 900 iters above 800/1500 nodes) and no animation above
  800 nodes; incremental fcose runs are debounced AND gated — above 600
  nodes a layout only runs when the pending addition batch reaches
  `max(40, 5% of nodes)`; small additions keep their anchor/center
  positions. New manual **RELAYOUT** button re-runs the initial layout.
- **Edge LOD**: at far zoom arrowhead geometry is dropped and edges thin
  out (`edge.lod-far`) — dense graphs render cheaply where details are
  invisible; real activity styling is untouched.
- Family view grouping is now one-pass (no O(F×M) re-filtering).

### Changed — bounded prioritized particles (`EdgePulseOverlay`)

- Per-edge particle ceiling now scales with activity level (2/4/6 for
  low/med/high; global ceiling stays 140); spawn order prioritizes
  strongest level + most recent activity first; activity-edge map bounded
  at 400. Real telemetry truth is untouched — only visual particles are
  capped/dropped.
- `testInjectActivity` TEST-ONLY hook (benchmark mode only; throws
  otherwise) drives the real applyActivity path with `synthetic`-flagged
  items so the budget is acceptance-testable without fabricating real
  telemetry.

### Changed — event drawer DOM bound

- Buffer stays bounded (800) in the hook; only the newest 150 rows mount —
  frequent traffic/GPU/process events can no longer explode the DOM.

### Added — ETW health rows + benchmark badge (UI)

- `ETW_HEALTH` event rows in the drawer (amber warning styling); header
  shows `BENCHMARK MODE · TEST DATA` badge only in benchmark mode; benchmark
  fixture nodes render with a dashed border.

### Backend

- New tests: `tests/test_benchmark_graph.py` (determinism, labeling,
  structure, API gate + activate/deactivate flow), `tests/test_etw_health.py`
  (state machine timeline, one-shot transitions, recovery, no-mutation).

### Measured (benchmark fixtures, headless Chromium, production build)

Initial render (snapshot → cytoscape add + fcose initial layout, measured
via `__esw_perf`):

| Nodes | edges | update (incl. layout) | layout | AI toggle | search / filter | fps |
|---|---|---|---|---|---|---|
| 500 | 710 | 1.20 s | 1.18 s | 6.3 ms | 2.8 / 4.4 ms | — |
| 1000 | 1398 | 4.23 s | 4.20 s | 11.2 ms | 5.4 ms | — |
| 1500 | 2112 | 8.87 s | 8.83 s | 20.4 ms | 15.2 ms | 60 |
| 2000 | 2804 | 16.2 s | 16.2 s | (measured, interactive) | — | 60 |

Particle budget: injected synthetic activity (600 items) → particles capped
at 140/140, activity edges capped 400/400, all flagged synthetic; idle
clears to 0 and the rAF loop stops. Families collapse 52 groups at 1500
nodes. 2000-node graph stays interactive (zoom changes render instantly).
These are renderer/benchmark measurements on synthetic fixtures — never
real-machine telemetry.

### Long-run (25 min × 2, real monitoring + local traffic phase)

`tools/longrun_probe.mjs` — headless browser on the production build, 10 s
samples, real loopback traffic harness during minutes 5–15 (port 19735 =
synthetic-provider chain, 94,108 events mapped through the full pipeline):

| metric | run 1 | run 2 |
|---|---|---|
| duration / samples | 25 min / 148 | 25 min / 148 |
| browser heap (JS) | 38 → 28 MB (max 84, **Δ −9.8 MB**) | 38 → 24 MB (max 66, **Δ −13.8 MB**) |
| backend RSS | 80 → 80 MB (max 81, **Δ +0.2 MB**) | 80 → 80 MB (max 81, **Δ +0.1 MB**) |
| particles max | 0 (no per-edge source that run) | **4** (live DATA particles during traffic) |
| activity edges max | 0 | 1 |
| event rows max | 150 (render cap) | 150 (render cap) |
| queue depth max | 0 | 48 (cap 500) |
| verdict | BOUNDED | BOUNDED |

No monotonic unbounded growth: heap oscillates with GC, RSS is flat, all
queues/buffers hold their caps. **No leak detected.**

### Acceptance

- New **Test AA — LARGE GRAPH** section: benchmark activation gate, 500 /
  1000 / 1500 / 2000-node fixtures, test_only labeling, zero real pids,
  perf instrumentation, AI toggle (instance preserved + measured), search /
  filter responsiveness, particle budget (injection → caps → idle), family
  view on fixture, zoom interactivity at 2000, deactivation → return-to-real
  (no synthetic nodes remain, injection hook gated).

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
