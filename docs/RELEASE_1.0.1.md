# ENCOMM SYSTEM WATCH — Release 1.0.1

**2026-08-20 — critical UI hotfix**

## What this is

v1.0.1 is a **critical UI hotfix** released immediately after v1.0.0. v1.0.0
started cleanly and its backend collected real data, but on the user's actual
browser the central graph canvas rendered almost blank while the live top
status (processes, connections, services, ETW, GPU, Hermes) kept updating.

This was a **camera / layout / viewport regression in the frontend graph
rendering** — **not** a collector defect. No observability semantics, data
model, or backend behavior were changed.

## What was fixed

- **Blank-graph camera** — the initial camera was placed by a destructive
  `0.55` zoom clamp and, on the non-animated path, fitted before fCoSE reached
  its final node positions. On a ~700–800 node real graph the forced zoom left
  only a sparse, randomized slice on screen.
- **Initial camera now fits after real layout completion** (on Cytoscape's
  `layoutstop` event).
- **FIT ALL now truly fits the visible topology** with no zoom floor.
- **Adaptive overview** replaces the hard `0.55` floor — zooms in only while it
  keeps at least 50% of visible nodes in the viewport.
- **One-time blank-graph safety recovery** (view-only, never fights the user's
  camera).
- **Container resize audit** — ResizeObserver → `cy.resize()` on every
  container geometry change.
- **`viewportHealth()` + new AF acceptance** objectively prove the real graph
  is on screen.

## Validation

- Frontend: `npm run typecheck` PASS, `npm run build` PASS.
- Full acceptance: baseline + new AF — 0 failures.
- Real production graph confirmed visible: viewport-intersecting nodes rose
  from ~14 % (blank) to ≥50 % with an adaptive camera.
- No continuous relayout / fit / camera loops.

## Scope

- No new observability features.
- No data-model changes.
- Read-only / metadata-only guarantees unchanged.

## Release artifacts

- Tag: `v1.0.1`
- Branch: `main`
- Commit: `fix: restore real graph visibility`
