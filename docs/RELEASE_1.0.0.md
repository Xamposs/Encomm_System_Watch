# ENCOMM SYSTEM WATCH — Release Notes v1.0.0

**2026-08-20** — first stable release.

## WHAT IT IS

A local-first, **read-only** observability map for Windows 11: processes,
services, TCP/UDP topology, GPU, WSL/Docker/VMs, semantic AI identities
(Hermes, LM Studio, MCP) and real application-level AI telemetry, rendered
as a living control-room graph. The graph is the product — every node and
edge is driven exclusively by real evidence. No fabricated activity, ever.

## KEY CAPABILITIES

- Windows processes + services (read-only)
- TCP/UDP topology with live connection pulses
- REAL Windows ETW TIER2 per-edge byte telemetry + directional DATA particles
  (requires Administrator; non-admin runs truthfully at TIER0)
- NVIDIA GPU/NVML telemetry
- Hermes / LM Studio / MCP semantic detection (evidence + confidence)
- Application-level AI telemetry pipeline (Hermes gateway status API, OTEL
  seam, optional bounded localhost ingestion) — Phase 17 FUNCTIONAL
- WSL / Docker / Hyper-V / VMware / VirtualBox observability (optional,
  capability-aware)
- SYSTEM / AI / INFRA views, inspector, live event drawer, search/filters,
  family grouping
- Large-graph benchmark mode (TEST-ONLY) validated to 2000+ nodes
- Long-run bounded memory + ETW attribution health detection

## PLATFORM

- **Windows 11** (primary, supported). Other OSes are NOT part of the
  v1.0.0 promise.
- Python 3.11+ (backend); Node.js 18+ only to build the frontend from source.
- Administrator PowerShell only for full TIER2 ETW.

## QUICK START

```powershell
# first time (fresh clone): venv + deps + production build
.\Setup-SystemWatch.ps1

# run (elevated PowerShell for full ETW; non-admin works, TIER0)
.\Start-SystemWatch.ps1

# open http://127.0.0.1:8765
```

## KNOWN LIMITATIONS

- Full ETW needs Administrator privileges; non-admin = TIER0.
- Abnormally-terminated backends can leave a stale `esw-telemetry` ETW
  session — the launcher warns and prints the exact `logman stop
  esw-telemetry -ets` cleanup; it never stops sessions automatically.
- Tokens/TPS/tool-call deep Hermes metrics are UNAVAILABLE without a safe
  producer interface (reported truthfully, never estimated).
- Stopped WSL distros are intentionally never started for inspection.
- Docker/VM collectors report honest unavailable/skipped states.
- Windows 11 focused — no cross-platform parity claim.

## SECURITY / PRIVACY

**READ ONLY · METADATA ONLY.** No process kill, service control, Docker/VM/
WSL/GPU/model/MCP/firewall control, no packet injection, and no prompt /
response / reasoning / credential capture. Binds to 127.0.0.1 only; no LAN,
no internet; container ENV is never collected. Command lines are redacted.
Benchmark and AI-fixture modes are TEST-ONLY and off by default — normal
startup is REAL mode with no synthetic state.

## VALIDATION

- Backend: 262 passed / 0 failed
- Frontend: typecheck PASS · production build PASS
- Full acceptance: 213 passed / 0 failed (+ release AE checks)
- Real machine: processes, services, network, ETW TIER2, DATA particles,
  GPU/NVML, Hermes, AI provider, WSL, Docker, VM, SYSTEM/AI/INFRA — truthful
  states
- Launcher/setup verified from a path containing spaces
  (`C:\Users\xampos\Desktop\Encomm SYSTEM WATCH`)

## VERSION / COMMIT

- Version: **1.0.0** (git tag `v1.0.0`)
- Baseline: `2ba02489fbd800fbec8d18713ed1591cfdd9fe4a`
- Repository: https://github.com/Xamposs/Encomm_System_Watch (branch `main`)