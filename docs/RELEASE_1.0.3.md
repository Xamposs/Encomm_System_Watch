# ENCOMM SYSTEM WATCH — Release 1.0.3

Released: 2026-08-22
Type: performance and view-geometry hotfix

Version 1.0.3 stabilizes the existing live topology UI. It does not introduce
new observability features and does not change telemetry semantics.

## What changed

- Filtered NODES and FAMILIES views receive a compact deterministic layout of
  their current visible subset while the full inventory layout remains cached.
- FIT ALL waits for stable renderer dimensions, fits visible nodes only, and
  ignores hidden inventory and remote curve-control extents.
- Rack sizing solves rows and columns against the current widescreen viewport
  instead of producing an arbitrarily wide strip.
- Rendering now has automatic NEAR, MID, and FAR levels of detail. Full HTML
  cards are viewport-virtualized and capped; MID/FAR use lightweight canvas
  mini-cards so nodes never disappear into line-only wiring.
- Wire glow, connection sockets, and real LIVE FLOW signals use bounded,
  zoom-aware budgets. Pan/zoom/drag temporarily use a cheaper render path and
  restore quality once after interaction settles.
- Metric updates refresh only affected card/family metrics. They do not trigger
  full graph composition, camera movement, or family reconstruction.
- The debug performance surface reports graph/viewport aspect, coverage,
  mounted-card churn, overlay draw timings, fit/layout timings, cache hits, and
  idle render activity.

## Truthfulness

- Fake nodes: none.
- Fake edges: none.
- Fake activity: none.
- Telemetry collectors and event semantics: unchanged.

The only backend code change is release metadata (`1.0.3`) so OpenAPI and the
packaged frontend report the same version.
