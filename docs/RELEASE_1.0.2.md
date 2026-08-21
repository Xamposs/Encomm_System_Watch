# ENCOMM SYSTEM WATCH — Release 1.0.2

**2026-08-20 — visual topology fidelity release**

## What this is

v1.0.2 is a **frontend-only visual topology pass**: composition, edge
routing, connectedness, and visual hierarchy. It makes the live real-data
graph read as a dense interconnected machine wiring map — the connected core
leading the canvas, real edge lanes fanning out/in between rack bands, and
the weakly-connected populations compacted into quiet banks — instead of a
scatter of isolated cards.

## What is NOT in this release

- **No new observability features.** No collectors, no ETW/AI/GPU/agent
  changes, no backend behavior changes.
- **No synthetic anything.** No fake nodes, no fixture edges, no invented
  relationships, no fabricated traffic. Every edge is a real socket,
  parentage, semantic, or infrastructure relationship observed on this
  machine.
- **Truthfulness preserved.** Stopped services keep their exact counts and
  remain fully clickable/inspectable; they are only rendered as compact dim
  banks in the default view.

## What changed

- **Connection-aware rack composition** — the initial layout is now a
  deterministic "wiring rack" driven by the real connected structure
  (union-find over real edges): connected core leads the left bands,
  stopped services / unconnected nodes trail as compact banks, and the
  column count adapts to the window aspect so the map fills the screen.
- **Lane-aware edge routing** — edges sharing a source/target band travel
  shared corridors (coordinated curvature, per-edge jitter) →
  fan-out/fan-in feel without fake edges.
- **Edge-dominant styling** — brighter, wider, color-coded real edges;
  cards recede (darker fills, thinner borders, tighter labels).
- **AG topology acceptance section (AG1–AG12)** — objective connectedness
  gates in `tools/acceptance.mjs`.

## Final reference-style visual pass (frontend-only, same release)

A second frontend-only iteration driven by a reference control-room
screenshot: the map now reads as **four labeled semantic zones** with
visible gaps (`HOSTS · SEMANTIC · INFRA` / `CONNECTED CORE` /
`SERVICES · UNLINKED` / `PROCESSES · UNLINKED`), with per-column stagger,
jitter and lean for organized chaos instead of a perfect grid. Two new
canvas layers: a **wire underlay** (soft colored glow following every real
rendered edge curve behind the cards + faint uppercase zone headers — zero
synthetic edges) and a **connection-socket overlay** (small ports on each
connected node's left/right border, colored by the dominant incident edge
kind). The edge palette was re-mapped to the reference multicolor language
(cyan/blue normal, teal/green active, amber services/infra, purple/magenta
AI, red only for genuine error/close), with longer lane-aware arcs for
cross-screen drama. Cards got a premium refresh (brighter fills, crisp
borders, larger PROCESS/SYSTEM cards; banked populations stay legible yet
visually subordinate). Truthfulness is untouched — every edge is still a
real relationship, all counts remain exact.

## Validation

- Frontend: `npm run typecheck` PASS, `npm run build` PASS
- Full acceptance suite (A–AF + AG1–AG12) against the production build:
  all passed, 0 failures
- Real regression: ETW TIER2, DATA particles, GPU, Hermes, AI telemetry,
  Services, WSL, Docker, VM, SYSTEM / AI / INFRA views — all green
- Performance smoke: real graph, 1000-node and 1500-node fixtures PASS with
  no continuous relayout / fit / camera fighting
- Security: read-only surfaces only; backend still binds 127.0.0.1 only

No tag was created for this release — tagging is decided after review.