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
| **10** | Advanced Node Inspector — read-only details panel, no controls | **COMPLETE** (added NETWORK section: ↓/↑ rates, connections, last activity — only when real telemetry exists) |
| **11** | Search / Filtering / Grouping — search highlight, ALL/ACTIVE/LISTENING/HIGH CPU filters, family grouping | **COMPLETE** (search + 4 filters + NODES/FAMILIES toggle using real parent_sid evidence; verified Tests N, P) |
| **12** | Live Event Drawer — bounded chronological feed | **COMPLETE** (800-event buffer, verified Test I; now also TRAFFIC BURST + TELEMETRY rows) |
| **13** | Visual Polish — control-room theme, zoom-aware labels, layout tuning | **COMPLETE** (near-black micro-grid canvas, wireframe far-zoom mode, semantic label buckets, brightened palette, edge activity styling, focus dimming, live 1600×1000 screenshot verified visually) |
| **14** | LM Studio Detection — local LLM API discovery (localhost sockets + HTTP probe) | **NOT STARTED** |
| **15** | Hermes Detection — Hermes gateway/agent process identification | **NOT STARTED** |
| **16** | MCP Server Detection — MCP server process/socket mapping | **NOT STARTED** |
| **17** | AI Agent Telemetry — agent nodes, tool calls, agent-to-agent messages, tokens/TPS/latency | **NOT STARTED** (schema designed; see ARCHITECTURE.md) |
| **18** | GPU / VRAM Telemetry — nvidia-smi / NVML collectors | **NOT STARTED** |
| **19** | Advanced Network Activity — per-connection byte/rate telemetry with honest capability tiers | **COMPLETE** (0.2.3: verified with REAL elevated Microsoft-Windows-TCPIP ETW — not synthetic telemetry. Modern Windows 11 manifest support: Tcb correlation (rundown/connect/accept → TCB cache → TcpDataTransferSend/Receive lookup → PID + real 4-tuple + direction + bytes), UDP self-contained events, readiness INITIALIZING/ACTIVE/DEGRADED; wildcard local-IP fallback (0.0.0.0/::) for outbound ETW attribution with ambiguity-safe single-candidate lookup + diagnostic counters; 0.2.3 adds topology-driven TCB cleanup (`drop_tcb_tuples` — closed connections leave the correlation map ~1-2 s after the psutil snapshot sees them go, closing the kernel Tcb-handle-recycling misattribution race measured at 18k unattributed events); `tools/verify_tier2.ps1` 10/10 with real directional bytes; acceptance 76/76 (Tests R/S/T) with REAL DATA particles on real edges; harness PROCESS↔PROCESS edge measured 445 MB fwd / 90 MB rev of real bytes; 101 backend tests. Wildcard branch: unit-tested, not observed in the runtime window) |
| **20** | Large Graph Optimization — 1000+ node validation, clustering, virtualization | **IN PROGRESS** (idle rAF stops, activity batches ≤5 msg/s, bounded queues/particles; focus mode + family view reduce visual clutter; full 1000+ validation still pending) |

---

## Phase checkpoint rule

Every future phase ends with:

```
IMPLEMENT → TEST → UPDATE DOCS → UPDATE CHANGELOG → GIT STATUS → COMMIT
→ PUSH TO main → VERIFY PUSH → REPORT COMMIT SHA
```
