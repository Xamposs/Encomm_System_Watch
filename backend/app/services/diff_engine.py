"""Snapshot diff engine: emits truthful lifecycle events between ticks.

Event types:
  PROCESS_STARTED / PROCESS_STOPPED
  CONNECTION_OPENED / CONNECTION_CLOSED
  PROCESS_METRICS_UPDATED (throttled: significant delta or forced interval)

No fake events are generated. When a process stops, its connections are NOT
reported as individual close events (the node itself disappears with its edges).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from ..config import Settings
from ..detectors.redact import redact_cmdline
from ..models.entities import Event, Snapshot, TopologyResult
from .topology import SYSTEM_NODE_ID

EVENT_PROCESS_STARTED = "PROCESS_STARTED"
EVENT_PROCESS_STOPPED = "PROCESS_STOPPED"
EVENT_CONNECTION_OPENED = "CONNECTION_OPENED"
EVENT_CONNECTION_CLOSED = "CONNECTION_CLOSED"
EVENT_PROCESS_METRICS_UPDATED = "PROCESS_METRICS_UPDATED"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _label_for(snap: Snapshot, topo: TopologyResult, node_id: str) -> str:
    node = topo.nodes.get(node_id)
    if node:
        return node.label
    p = snap.processes.get(node_id)
    return p.name if p else node_id


class DiffEngine:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._prev_snap: Optional[Snapshot] = None
        self._prev_topo: Optional[TopologyResult] = None
        self._last_metrics: dict[str, float] = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._seq:06d}-{int(time.time() * 1000)}"

    def diff(self, snap: Snapshot, topo: TopologyResult) -> list[Event]:
        events: list[Event] = []
        prev_snap, prev_topo = self._prev_snap, self._prev_topo
        if prev_snap is None or prev_topo is None:
            self._prev_snap, self._prev_topo = snap, topo
            return events

        cur_ids = set(snap.processes)
        prev_ids = set(prev_snap.processes)
        ts = _now_iso()
        stopped = prev_ids - cur_ids

        # ---- process lifecycle -------------------------------------------------
        for sid in sorted(cur_ids - prev_ids):
            p = snap.processes[sid]
            node = topo.nodes.get(sid)
            events.append(Event(
                event_id=self._next_id(),
                event_type=EVENT_PROCESS_STARTED,
                source=sid,
                target=None,
                timestamp=ts,
                metadata={
                    "node": node.to_dict() if node else {
                        "id": sid, "kind": "PROCESS", "label": p.name,
                        "data": {**p.to_dict(), "cmdline": redact_cmdline(p.cmdline)},
                    },
                    "pid": p.pid,
                    "name": p.name,
                },
            ))
        for sid in sorted(stopped):
            p = prev_snap.processes[sid]
            events.append(Event(
                event_id=self._next_id(),
                event_type=EVENT_PROCESS_STOPPED,
                source=sid,
                target=None,
                timestamp=ts,
                metadata={"pid": p.pid, "name": p.name},
            ))

        # ---- connection lifecycle ----------------------------------------------
        cur_c, prev_c = snap.connections, prev_snap.connections

        def _edge_port(c, kind: str) -> int:
            if kind == "LISTEN":
                return c.local_port
            if kind != "LOCALHOST":
                return c.remote_port
            return c.local_port

        def _owner_really_gone(ckey: str, c, owner: Optional[str]) -> bool:
            """True only when the owner stopped AND its socket tuples are gone.

            psutil can transiently miss a process that is mid-startup (or a
            socket's owning PID can flip from None to the real PID as the
            process finishes initializing). In those windows the connection
            tuple still exists in the current snapshot with a different key
            form — suppressing events then would silently drop real
            CONNECTION_OPENED/CLOSED announcements (the edge would never
            appear on clients). Suppress only when the tuples are truly gone.
            """
            if owner not in stopped:
                return False
            for k2, c2 in cur_c.items():
                if (c2.local_ip, c2.local_port, c2.remote_ip, c2.remote_port) == (
                    c.local_ip, c.local_port, c.remote_ip, c.remote_port
                ):
                    return False
            return True

        for ckey in sorted(set(cur_c) - set(prev_c)):
            c = cur_c[ckey]
            owner = snap.owner_map.get(ckey)
            if _owner_really_gone(ckey, c, owner):
                continue
            tgt, kind, eid = topo.conn_targets.get(ckey, (SYSTEM_NODE_ID, "EXTERNAL", "e:unknown"))
            events.append(Event(
                event_id=self._next_id(),
                event_type=EVENT_CONNECTION_OPENED,
                source=owner or SYSTEM_NODE_ID,
                target=tgt,
                timestamp=ts,
                metadata={
                    "edge_id": eid,
                    "kind": kind,
                    "proto": c.proto,
                    "edge_port": _edge_port(c, kind),
                    "local_ip": c.local_ip,
                    "local_port": c.local_port,
                    "remote_ip": c.remote_ip,
                    "remote_port": c.remote_port,
                    "src_node": (topo.nodes.get(owner or SYSTEM_NODE_ID) or topo.nodes[SYSTEM_NODE_ID]).to_dict(),
                    "tgt_node": (topo.nodes.get(tgt) or topo.nodes[SYSTEM_NODE_ID]).to_dict(),
                    "src_label": _label_for(snap, topo, owner or SYSTEM_NODE_ID),
                    "tgt_label": _label_for(snap, topo, tgt),
                },
            ))

        for ckey in sorted(set(prev_c) - set(cur_c)):
            c = prev_c[ckey]
            owner = prev_snap.owner_map.get(ckey)
            if _owner_really_gone(ckey, c, owner):
                continue
            tgt, kind, eid = prev_topo.conn_targets.get(ckey, (SYSTEM_NODE_ID, "EXTERNAL", "e:unknown"))
            remaining = 0
            ports: list[int] = []
            edge_now = topo.edges.get(eid)
            if edge_now:
                remaining = sum(
                    1 for k2 in cur_c
                    if topo.conn_targets.get(k2, (None, "", ""))[2] == eid
                )
                ports = edge_now.ports
            events.append(Event(
                event_id=self._next_id(),
                event_type=EVENT_CONNECTION_CLOSED,
                source=owner or SYSTEM_NODE_ID,
                target=tgt,
                timestamp=ts,
                metadata={
                    "edge_id": eid,
                    "kind": kind,
                    "proto": c.proto,
                    "edge_port": _edge_port(c, kind),
                    "local_ip": c.local_ip,
                    "local_port": c.local_port,
                    "remote_ip": c.remote_ip,
                    "remote_port": c.remote_port,
                    "remaining": remaining,
                    "ports": ports,
                    "src_label": _label_for(prev_snap, prev_topo, owner or SYSTEM_NODE_ID),
                    "tgt_label": _label_for(prev_snap, prev_topo, tgt),
                },
            ))

        # ---- throttled metrics --------------------------------------------------
        now = time.time()
        deltas: list[tuple[float, str]] = []
        for sid, p in snap.processes.items():
            prev_p = prev_snap.processes.get(sid)
            if prev_p is None:
                continue
            dcpu = abs(p.cpu_percent - prev_p.cpu_percent)
            dmem = abs(p.memory_mb - prev_p.memory_mb)
            last = self._last_metrics.get(sid)
            force_due = last is not None and now - last >= self.cfg.metrics_force_interval_s
            if dcpu >= self.cfg.metrics_cpu_delta or dmem >= self.cfg.metrics_mem_delta_mb or force_due:
                deltas.append((dcpu + dmem, sid))
        deltas.sort(reverse=True)
        for _, sid in deltas[: self.cfg.max_process_events_per_tick]:
            p = snap.processes[sid]
            events.append(Event(
                event_id=self._next_id(),
                event_type=EVENT_PROCESS_METRICS_UPDATED,
                source=sid,
                target=None,
                timestamp=ts,
                metadata={
                    "pid": p.pid,
                    "name": p.name,
                    "cpu_percent": p.cpu_percent,
                    "memory_mb": p.memory_mb,
                    "num_threads": p.num_threads,
                    "status": p.status,
                },
            ))
            self._last_metrics[sid] = now
        if len(self._last_metrics) > 2000:
            self._last_metrics = {k: v for k, v in self._last_metrics.items() if k in cur_ids}

        self._prev_snap, self._prev_topo = snap, topo
        return events
