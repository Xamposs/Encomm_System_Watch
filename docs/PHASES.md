# ENCOMM SYSTEM WATCH — Development Roadmap

Status legend:

| State | Meaning |
|---|---|
| `NOT STARTED` | no implementation work yet |
| `IN PROGRESS` | partial implementation, not verifiable end-to-end |
| `FUNCTIONAL` | works with real data, polish/edge cases remain |
| `COMPLETE` | implemented, tested, verified on the live machine |

---

| Phase | Scope | Status |
|---|---|---|
| **01** | Project Foundation — repo, backend/frontend scaffold, launcher, docs, CI-ready git workflow | **COMPLETE** |
| **02** | Windows Process Collector — stable IDs, defensive field collection, CPU/RAM, metadata caching | **COMPLETE** (30 backend tests; 0.6 s steady tick on 295 processes) |
| **03** | Live Process Graph — processes as nodes, compact cards, fcose layout, incremental updates | **COMPLETE** (verified in acceptance: 300+ process nodes) |
| **04** | Real Network Connections — TCP/UDP sockets, listening/localhost/external classification, proc↔proc pairing, endpoint nodes with caps | **COMPLETE** (verified: 60 localhost edges, 54+ external edges) |
| **05** | WebSocket Event Streaming — snapshot on connect, event batches, stats heartbeat, auto-reconnect | **COMPLETE** (verified: reconnect after refresh, Test J) |
| **06** | Animated Live Connections — traveling-signal canvas overlay, open/close/update pulses, recent-activity flow, graceful fades | **COMPLETE** (verified: recent edges + CONNECTION OPENED events, Test F) |
| **07** | System Metrics — machine CPU/RAM header stats, per-process CPU/RAM, throttled metric events | **COMPLETE** (verified: METRICS events flowing, Test G) |
| **08** | Windows Services — service enumeration (name, state, start type, account) as nodes | **NOT STARTED** (collector seam ready) |
| **09** | Docker + WSL observability | **NOT STARTED** (designed-for) |
| **10** | Advanced Node Inspector — read-only details panel, no controls | **COMPLETE** (added NETWORK section: ↓/↑ rates, connections, last activity — only when real telemetry exists; 0.3.0 adds SEMANTIC IDENTITY + GPU sections) |
| **11** | Search / Filtering / Grouping — search highlight, ALL/ACTIVE/LISTENING/HIGH CPU filters, family grouping | **COMPLETE** (search + 4 filters + NODES/FAMILIES toggle using real parent_sid evidence; verified Tests N, P) |
| **12** | Live Event Drawer — bounded chronological feed | **COMPLETE** (800-event buffer, verified Test I; 0.3.0 adds semantic event rows: HERMES/LM STUDIO/MCP DETECTED, MODEL LOADED/AVAILABLE, GPU PROCESS ATTACHED/DETACHED, SEMANTIC LOST) |
| **13** | Visual Polish — control-room theme, zoom-aware labels, layout tuning | **COMPLETE** (near-black micro-grid canvas, wireframe far-zoom mode, semantic label buckets, brightened palette, edge activity styling, focus dimming, live 1600×1000 screenshot verified visually) |
| **14** | LM Studio Detection — local LLM API discovery (localhost sockets + HTTP probe) | **COMPLETE** (0.3.0: evidence-based detector — real socket-topology port discovery, loopback-only `/v1/models` + `/api/0/models` probes (throttled/cached), strict LOADED (runtime-proven) vs AVAILABLE distinction, model nodes + SERVES_MODEL/LOCAL_API/HOSTS edges, CONFIRMED only via known path or live API; unit-tested incl. false-positive rejection, API-failure degradation and timeout isolation) |
| **15** | Hermes Detection — Hermes gateway/agent process identification | **COMPLETE** (0.3.0: evidence-combination detector — desktop app exact-path identity (CONFIRMED), `hermes_cli.main … serve` gateway detection, Electron/gateway family with identity-gated ancestry (no session-tree absorption), MEMBER_OF + real PROCESS_PARENT + HOSTS relationships; verified LIVE on this machine: 9 real Hermes processes, CONFIRMED, zero false positives) |
| **16** | MCP Server Detection — MCP server process/socket mapping | **COMPLETE** (0.3.0: stdio/HTTP/SSE — anchored cmdline tokens, package paths, parent/child ancestry (Hermes gateway launcher), owned listeners; identity proven → named (`filesystem`, `github`, …), proven-but-unidentified → `unknown`; SPAWNED/PROCESS_PARENT for stdio (never fake network edges); unit-tested: ancestry, stdio, HTTP, false-positive rejection; acceptance: REAL or SKIPPED with fixture tests labeled separately) |
| **17** | AI Agent Telemetry — agent nodes, tool calls, agent-to-agent messages, tokens/TPS/latency | **NOT STARTED** (0.3.0 ships the seams only: detector registry, evidence/confidence model, semantic nodes/edges, change-only events, AI view — deep telemetry is intentionally not fabricated; schema-compatible) |
| **18** | GPU / VRAM Telemetry — nvidia-smi / NVML collectors | **COMPLETE** (0.3.0: NVML primary (`nvidia-ml-py`/pynvml, no subprocess), nvidia-smi CSV fallback, multi-GPU `gpu:<index>` resource nodes, ~1 s metrics / ~2 s PID attribution, per-process VRAM only when NVML provides it, change-only GPU_PROCESS_ATTACHED/DETACHED, USES_GPU edges; verified LIVE: GTX 1660 Ti — real name/util/VRAM/temp/power/driver + 20 attributed PIDs; failure-isolated) |
| **19** | Advanced Network Activity — per-connection byte/rate telemetry with honest capability tiers | **COMPLETE** (0.2.3: verified with REAL elevated Microsoft-Windows-TCPIP ETW — not synthetic telemetry. Modern Windows 11 manifest support: Tcb correlation (rundown/connect/accept → TCB cache → TcpDataTransferSend/Receive lookup → PID + real 4-tuple + direction + bytes), UDP self-contained events, readiness INITIALIZING/ACTIVE/DEGRADED; wildcard local-IP fallback (0.0.0.0/::) for outbound ETW attribution with ambiguity-safe single-candidate lookup + diagnostic counters; 0.2.3 adds topology-driven TCB cleanup (`drop_tcb_tuples` — closed connections leave the correlation map ~1-2 s after the psutil snapshot sees them go, closing the kernel Tcb-handle-recycling misattribution race measured at 18k unattributed events); `tools/verify_tier2.ps1` 10/10 with real directional bytes; acceptance 76/76 (Tests R/S/T) with REAL DATA particles on real edges; harness PROCESS↔PROCESS edge measured 445 MB fwd / 90 MB rev of real bytes; 101 backend tests. Wildcard branch: unit-tested, not observed in the runtime window) |
| **20** | Large Graph Optimization — 1000+ node validation, clustering, virtualization | **COMPLETE** (0.3.1: TEST-ONLY deterministic benchmark mode validated 500/1000/1500/2000-node fixtures; incremental updates + label cache; size-scaled layout policy + manual RELAYOUT; far-zoom edge LOD; bounded prioritized particles 140/400/2-4-6; AI toggle + search/filter measured (5-18 ms at 500-1500 nodes); PerfMonitor diagnostics; read-only ETW attribution health detector; 30-min long-run bounded; fixed latent filter-restore bug (display:none style pass); real graph regression green) |

---

## Phase checkpoint rule

Every future phase ends with:

```
IMPLEMENT → TEST → UPDATE DOCS → UPDATE CHANGELOG → GIT STATUS → COMMIT
→ PUSH TO main → VERIFY PUSH → REPORT COMMIT SHA
```
