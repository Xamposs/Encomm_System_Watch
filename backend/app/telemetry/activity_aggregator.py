"""Activity aggregator: raw telemetry events -> small WebSocket batches.

Raw per-packet events can arrive at thousands/sec. The aggregator:
  - maps each event to a topology edge via (pid, 4-tuple) evidence,
  - accumulates bytes per edge per flush window (~200 ms),
  - computes directional rates (1 s EMA) and activity levels,
  - emits one compact ``network_activity`` batch per window (never one
    message per packet),
  - detects conservative TRAFFIC_BURST events (rate-limited per edge),
  - keeps per-process totals for the node inspector / halo,
  - tracks last_activity timestamps so the frontend can decay ACTIVE ->
    RECENT -> IDLE truthfully (no activity = no animation).

Truthfulness rules:
  - events that cannot be mapped to a known edge are attributed to the
    owning process when possible (node halo), never to a random edge;
  - ETW reports outbound local addresses as wildcards (0.0.0.0 / :: /
    empty) while psutil sees the resolved source IP. A wildcard fallback
    keyed by (pid, local_port, remote_ip, remote_port) is used ONLY when
    it identifies exactly ONE topology edge; ambiguous identities are
    never guessed (counted as ``wildcard_lookup_ambiguous``);
  - events with no known owner are counted only by the adapter totals;
  - direction is only claimed when the provider exposed it.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from ..config import Settings
from ..models.entities import Event, Snapshot, TopologyResult
from .base import Capability, EdgeRateState, NetworkActivityEvent, ProcessRateState

EVENT_TRAFFIC_BURST = "TRAFFIC_BURST"
EVENT_TELEMETRY_CAPABILITY_CHANGED = "TELEMETRY_CAPABILITY_CHANGED"

ACTIVE_MS = 500    # < 500 ms since activity -> ACTIVE
RECENT_MS = 5000   # < 5 s since activity -> RECENT


def _iso_now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="milliseconds")


class ActivityAggregator:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._pending: list[NetworkActivityEvent] = []
        # (pid, lip, lport, rip, rport) -> (edge_id, owner_is_source, kind)
        self._tuple_map: dict[tuple, tuple[str, bool, str]] = {}
        # (pid, lport, rip, rport) -> [(edge_id, owner_is_source, kind), ...]
        # wildcard-local fallback candidates. Initialized empty so flush()
        # is safe before the first set_topology() (fresh startup).
        self._wildcard_map: dict[tuple, list[tuple[str, bool, str]]] = {}
        self._pid_map: dict[int, str] = {}          # pid -> stable process id
        self._labels: dict[str, tuple[str, str]] = {}  # edge_id -> (src, tgt) labels
        self._edge_kinds: dict[str, str] = {}       # edge_id -> LOCALHOST|EXTERNAL|LISTEN
        self._edges: dict[str, EdgeRateState] = {}
        self._procs: dict[str, ProcessRateState] = {}
        self._burst_last: dict[str, float] = {}
        self._capability = Capability()
        self._seq = 0
        self._last_flush = time.time()
        # read-only diagnostics (exposed via /api/telemetry/debug)
        self._counters = {
            "events_recorded": 0,
            "events_mapped_to_edges": 0,
            "events_mapped_to_nodes": 0,
            "events_unattributed": 0,
            "activity_batches_emitted": 0,
            "exact_lookup_hits": 0,
            "wildcard_lookup_hits": 0,
            "wildcard_lookup_misses": 0,
            "wildcard_lookup_ambiguous": 0,
        }
        self._last_batch: dict = {}
        # closed-connection notification: tuples absent from the topology
        # for 2+ consecutive set_topology calls are reported to the provider
        # (which drops their TCB entries promptly, closing the stale-entry
        # reuse race). De-bounced so a single flaky psutil tick never kills
        # the identity of a still-open connection.
        self._absent_tuples: dict[tuple, int] = {}
        self._on_tuples_closed: Optional[Callable[[set], None]] = None

    def set_tuples_closed_callback(self, fn: Optional[Callable[[set], None]]) -> None:
        """Register a receiver for connection tuples that left the topology.

        The provider uses this to drop stale TCB correlation entries
        immediately instead of waiting for (10-35 s delayed) ETW removal
        events — otherwise a recycled kernel Tcb handle would misattribute
        the next connection's early bytes to the dead one.
        """
        self._on_tuples_closed = fn

    # ------------------------------------------------------------ topology

    def set_topology(self, snap: Snapshot, topo: TopologyResult) -> None:
        """Rebuild the evidence map from the latest snapshot (called ~1 Hz)."""
        pid_map = {}
        for sid, p in snap.processes.items():
            if p.pid is not None:
                pid_map[p.pid] = sid
        tuple_map: dict[tuple, tuple[str, bool, str]] = {}
        # ETW reports the local address of outbound client sockets as the
        # wildcard (0.0.0.0 / ::) while psutil reports the resolved source
        # IP — index a fallback keyed by (pid, local_port, remote_ip,
        # remote_port) holding ALL candidate edges, so flush() can use it
        # only when it identifies exactly one edge (never guess).
        wildcard_map: dict[tuple, list[tuple[str, bool, str]]] = {}
        labels: dict[str, tuple[str, str]] = {}
        edge_kinds: dict[str, str] = {}

        def _lbl(nid: str) -> str:
            n = topo.nodes.get(nid)
            return n.label if n else nid

        for ckey, c in snap.connections.items():
            info = topo.conn_targets.get(ckey)
            if not info:
                continue
            _, _, eid = info
            edge = topo.edges.get(eid)
            if edge is None:
                continue
            owner = snap.owner_map.get(ckey)
            owner_is_src = owner is not None and owner == edge.source
            key = (c.pid, c.local_ip, c.local_port, c.remote_ip, c.remote_port)
            tuple_map[key] = (eid, owner_is_src, edge.kind)
            wk = (c.pid, c.local_port, c.remote_ip, c.remote_port)
            candidates = wildcard_map.setdefault(wk, [])
            cand = (eid, owner_is_src, edge.kind)
            if cand not in candidates:
                # the same edge indexed twice is NOT ambiguity
                candidates.append(cand)
            labels[eid] = (_lbl(edge.source), _lbl(edge.target))
            edge_kinds[eid] = edge.kind
        with self._lock:
            self._pid_map = pid_map
            self._tuple_map = tuple_map
            self._wildcard_map = wildcard_map
            self._labels = labels
            self._edge_kinds = edge_kinds
            now = time.time()
            present = set(labels)
            for eid, st in self._edges.items():
                if eid in present:
                    st.last_seen = now
        # closed-connection detection (de-bounced 2 ticks): tuples that were
        # in the topology and are now gone are reported so the provider can
        # drop their TCB entries before the kernel recycles the handle.
        current = set(tuple_map)
        closed: set = set()
        for key in list(self._absent_tuples):
            if key in current:
                del self._absent_tuples[key]
            else:
                self._absent_tuples[key] += 1
                if self._absent_tuples[key] >= 2:
                    closed.add(key)
        for key in current:
            self._absent_tuples.setdefault(key, 0)
        for key in closed:
            del self._absent_tuples[key]
        if closed and self._on_tuples_closed is not None:
            try:
                self._on_tuples_closed(closed)
            except Exception:  # noqa: BLE001 — closed-tuple cleanup must never break collection
                pass

    def set_capability(self, capability: Capability) -> None:
        self._capability = capability

    def capability(self) -> Capability:
        return self._capability

    # --------------------------------------------------------------- input

    def record(self, ev: NetworkActivityEvent) -> None:
        with self._lock:
            self._pending.append(ev)
            self._counters["events_recorded"] += 1

    def record_many(self, events: list[NetworkActivityEvent]) -> None:
        """Batch-ingest drained provider events with ONE lock acquisition.

        The ETW provider can deliver thousands of events per window; feeding
        them through ``record()`` one by one would acquire the lock per
        event. The runtime loop drains the provider and calls this once.
        """
        if not events:
            return
        with self._lock:
            self._pending.extend(events)
            self._counters["events_recorded"] += len(events)

    # -------------------------------------------------------------- output

    def flush(self) -> tuple[list[dict], list[Event], list[dict]]:
        """Flush one window.

        Returns (edge_items, burst_events, node_items). Bounded work: the
        pending list is drained under lock and processed outside it.
        """
        with self._lock:
            pending, self._pending = self._pending, []
            pid_map = dict(self._pid_map)
            tuple_map = dict(self._tuple_map)
            wildcard_map = dict(self._wildcard_map)
            labels = dict(self._labels)
        now = time.time()
        window_s = max(0.05, now - self._last_flush)
        self._last_flush = now

        edge_bytes: dict[str, list[int]] = {}   # edge_id -> [fwd, rev]
        proc_bytes: dict[str, list[int]] = {}   # sid -> [down, up]
        mapped_edges = 0
        mapped_nodes = 0
        unattributed = 0
        exact_hits = 0
        wildcard_hits = 0
        wildcard_misses = 0
        wildcard_ambiguous = 0
        for ev in pending:
            key = (ev.pid, ev.local_ip, ev.local_port, ev.remote_ip, ev.remote_port)
            hit = tuple_map.get(key)
            if hit is not None:
                exact_hits += 1
            elif ev.local_ip in ("0.0.0.0", "::", ""):
                # ETW reports outbound client sockets with a wildcard local
                # address while psutil reports the resolved source IP, so
                # the exact key above misses. Fall back to (pid,
                # local_port, remote_ip, remote_port) ONLY when it
                # identifies exactly one topology edge — never guess
                # between candidates.
                candidates = wildcard_map.get(
                    (ev.pid, ev.local_port, ev.remote_ip, ev.remote_port))
                if candidates is None:
                    wildcard_misses += 1
                elif len(candidates) == 1:
                    hit = candidates[0]
                    wildcard_hits += 1
                else:
                    wildcard_ambiguous += 1
            if hit is not None:
                mapped_edges += 1
                eid, owner_is_src, _kind = hit
                if ev.direction == "OUT":
                    fwd = ev.size if owner_is_src else 0
                    rev = 0 if owner_is_src else ev.size
                else:
                    fwd = 0 if owner_is_src else ev.size
                    rev = ev.size if owner_is_src else 0
                bucket = edge_bytes.setdefault(eid, [0, 0])
                bucket[0] += fwd
                bucket[1] += rev
            elif ev.pid is not None and ev.pid in pid_map:
                mapped_nodes += 1
                # real activity that cannot be pinned to a specific edge
                # (e.g. tuple changed between ticks): attribute to the process
                bucket = proc_bytes.setdefault(pid_map[ev.pid], [0, 0])
                if ev.direction == "OUT":
                    bucket[1] += ev.size
                else:
                    bucket[0] += ev.size
            else:
                unattributed += 1  # counted only by adapter totals

        items: list[dict] = []
        burst_events: list[Event] = []
        for eid, (fwd, rev) in edge_bytes.items():
            st = self._edges.get(eid)
            if st is None:
                st = self._edges[eid] = EdgeRateState(last_seen=now)
            st.last_activity = now
            st.last_seen = now
            inst_fwd = fwd / window_s
            inst_rev = rev / window_s
            alpha = min(1.0, window_s)
            st.fwd_bps = inst_fwd if st.fwd_bps == 0 else st.fwd_bps + alpha * (inst_fwd - st.fwd_bps)
            st.rev_bps = inst_rev if st.rev_bps == 0 else st.rev_bps + alpha * (inst_rev - st.rev_bps)
            intensity = inst_fwd + inst_rev
            st.level = 3 if intensity >= 500_000 else (2 if intensity >= 50_000 else (1 if intensity > 0 else 0))
            items.append({
                "edge_id": eid,
                "fwd_bytes": fwd,
                "rev_bytes": rev,
                "duration_ms": round(window_s * 1000),
                "fwd_bps": round(st.fwd_bps, 1),
                "rev_bps": round(st.rev_bps, 1),
                "level": st.level,
                "last_activity": round(st.last_activity, 3),
            })
            total = fwd + rev
            if (
                total >= self.cfg.telemetry_burst_bytes
                and now - self._burst_last.get(eid, 0.0) >= self.cfg.telemetry_burst_cooldown_s
            ):
                self._burst_last[eid] = now
                src_lbl, tgt_lbl = labels.get(eid, (eid, ""))
                burst_events.append(Event(
                    event_id=self._next_id(),
                    event_type=EVENT_TRAFFIC_BURST,
                    source=eid,
                    target=None,
                    timestamp=_iso_now(),
                    metadata={
                        "edge_id": eid,
                        "bytes": total,
                        "window_ms": round(window_s * 1000),
                        "rate_bps": round((fwd + rev) / window_s, 1),
                        "fwd_bps": round(fwd / window_s, 1),
                        "rev_bps": round(rev / window_s, 1),
                        "src_label": src_lbl,
                        "tgt_label": tgt_lbl,
                    },
                ))

        node_items: list[dict] = []
        for sid, (down, up) in proc_bytes.items():
            st = self._procs.get(sid)
            if st is None:
                st = self._procs[sid] = ProcessRateState()
            st.last_activity = now
            inst_d = down / window_s
            inst_u = up / window_s
            alpha = min(1.0, window_s)
            st.down_bps = inst_d if st.down_bps == 0 else st.down_bps + alpha * (inst_d - st.down_bps)
            st.up_bps = inst_u if st.up_bps == 0 else st.up_bps + alpha * (inst_u - st.up_bps)
            node_items.append({
                "sid": sid,
                "down_bps": round(st.down_bps, 1),
                "up_bps": round(st.up_bps, 1),
                "last_activity": round(st.last_activity, 3),
            })

        self._prune(now)

        # diagnostics: truthful counters + last non-empty batch totals
        if items or node_items:
            self._last_batch = {
                "ts": now,
                "fwd_bytes": sum(i["fwd_bytes"] for i in items),
                "rev_bytes": sum(i["rev_bytes"] for i in items),
                "node_down_bytes": sum(n["down_bps"] * window_s for n in node_items),
                "node_up_bytes": sum(n["up_bps"] * window_s for n in node_items),
            }
            with self._lock:
                self._counters["activity_batches_emitted"] += 1
                self._counters["events_mapped_to_edges"] += mapped_edges
                self._counters["events_mapped_to_nodes"] += mapped_nodes
                self._counters["events_unattributed"] += unattributed
        # lookup diagnostics are unconditional: misses/ambiguity count even
        # in windows that produced no items (e.g. unattributed-only flushes)
        with self._lock:
            self._counters["exact_lookup_hits"] += exact_hits
            self._counters["wildcard_lookup_hits"] += wildcard_hits
            self._counters["wildcard_lookup_misses"] += wildcard_misses
            self._counters["wildcard_lookup_ambiguous"] += wildcard_ambiguous
        return items, burst_events, node_items

    def _prune(self, now: float) -> None:
        """Drop stale state for edges/processes that no longer exist."""
        stale = [eid for eid, st in self._edges.items() if now - st.last_seen > 10.0]
        for eid in stale:
            self._edges.pop(eid, None)
            self._burst_last.pop(eid, None)
        if len(self._procs) > 2000:
            self._procs = {k: v for k, v in self._procs.items() if now - v.last_activity < 30.0}

    def _next_id(self) -> str:
        self._seq += 1
        return f"T{self._seq:06d}-{int(time.time() * 1000)}"

    # -------------------------------------------------------------- reads

    def edge_state(self, edge_id: str) -> Optional[EdgeRateState]:
        with self._lock:
            return self._edges.get(edge_id)

    def edge_states(self) -> dict[str, EdgeRateState]:
        with self._lock:
            return dict(self._edges)

    def process_state(self, sid: str) -> Optional[ProcessRateState]:
        with self._lock:
            return self._procs.get(sid)

    def process_states(self) -> dict[str, ProcessRateState]:
        with self._lock:
            return dict(self._procs)

    def counters(self) -> dict:
        """Read-only diagnostic counters (safe for /api/telemetry/debug)."""
        with self._lock:
            return {**self._counters, "last_batch": dict(self._last_batch)}

    def totals(self) -> dict:
        """Machine-level totals of captured telemetry (source: 'CAPTURED').

        Only edges that cross the machine boundary count: EXTERNAL and LISTEN
        edges. On EXTERNAL/LISTEN edges the process is the source, so bytes
        travelling source->target are UPLOAD (up) and target->source are
        DOWNLOAD (down). LOCALHOST edges are loopback traffic and are not
        included — they do not cross any adapter, so counting them here would
        inflate the captured totals versus the adapter totals.
        """
        down = 0.0
        up = 0.0
        with self._lock:
            for eid, st in self._edges.items():
                if self._edge_kinds.get(eid) == "LOCALHOST":
                    continue
                down += st.rev_bps
                up += st.fwd_bps
        return {"down_bps": round(down, 1), "up_bps": round(up, 1)}

    def decay_stats(self, edge_id: str) -> str:
        """ACTIVE / RECENT / IDLE for a state (used by tests + tooltips)."""
        st = self._edges.get(edge_id)
        if st is None or st.last_activity == 0:
            return "IDLE"
        age_ms = (time.time() - st.last_activity) * 1000
        if age_ms < ACTIVE_MS:
            return "ACTIVE"
        if age_ms < RECENT_MS:
            return "RECENT"
        return "IDLE"
