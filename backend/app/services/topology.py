"""Topology engine: converts a normalized system snapshot into nodes + edges.

Node kinds today: PROCESS, SYSTEM, EXTERNAL_ENDPOINT, LISTENING_PORT,
LOCAL_ENDPOINT. The schema is generic (id/kind/label/data) so future kinds
(WINDOWS_SERVICE, DOCKER_CONTAINER, WSL_PROCESS, AI_AGENT, LLM, MCP_SERVER,
GPU, ...) plug in without reshaping the engine.

Rules (evidence-based only):
  - localhost TCP pairs with BOTH sides mapped to processes => process-process edge
  - unpaired localhost connections => LOCAL_ENDPOINT node
  - remote endpoints => EXTERNAL_ENDPOINT node (one per IP, capped, overflow aggregated)
  - listening sockets => LISTENING_PORT node (capped, overflow aggregated)
  - sockets with no mapped owner => SYSTEM node
"""
from __future__ import annotations

import time
from collections import defaultdict

from ..config import Settings
from ..detectors.redact import redact_cmdline
from ..models.entities import ConnectionInfo, Snapshot, Stats, TEdge, TNode, TopologyResult

SYSTEM_NODE_ID = "sys:windows"


def ext_node_id(ip: str) -> str:
    return f"ext:{ip}"


def listen_node_id(proto: str, ip: str, port: int) -> str:
    return f"lst:{proto}:{ip}:{port}"


def loc_node_id(proto: str, ip: str, port: int) -> str:
    return f"loc:{proto}:{ip}:{port}"


def edge_id(src: str, tgt: str, kind: str) -> str:
    return f"e:{src}->{tgt}:{kind}"


def _canon(src: str, tgt: str, kind: str) -> tuple[str, str]:
    """Undirected (LOCALHOST) edges get a canonical endpoint order so the edge
    id is stable regardless of which side was observed first."""
    if kind == "LOCALHOST" and src > tgt:
        return tgt, src
    return src, tgt


def _merge_ports(existing: list[int], new: int) -> None:
    if new not in existing:
        existing.append(new)


def _parse_listen(nid: str) -> tuple[str, str, int]:
    _, proto, rest = nid.split(":", 2)
    ip, port = rest.rsplit(":", 1)
    return proto, ip, int(port)


def _parse_loc(nid: str) -> tuple[str, str, int]:
    _, proto, rest = nid.split(":", 2)
    ip, port = rest.rsplit(":", 1)
    return proto, ip, int(port)


class TopologyEngine:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg

    def build(self, snap: Snapshot) -> TopologyResult:
        nodes: dict[str, TNode] = {}
        edges: dict[str, TEdge] = {}
        conn_targets: dict[str, tuple[str, str, str]] = {}
        sys = snap.system

        # ---- SYSTEM node ----------------------------------------------------
        nodes[SYSTEM_NODE_ID] = TNode(
            id=SYSTEM_NODE_ID,
            kind="SYSTEM",
            label="WINDOWS 11",
            data={
                "hostname": sys.get("hostname", ""),
                "platform": sys.get("platform", ""),
                "cpu_percent": sys.get("cpu_percent", 0.0),
                "mem_percent": sys.get("mem_percent", 0.0),
                "mem_used_gb": sys.get("mem_used_gb", 0.0),
                "mem_total_gb": sys.get("mem_total_gb", 0.0),
                "uptime_s": max(0, time.time() - sys.get("boot_ts", time.time())),
            },
        )

        # ---- PROCESS nodes ---------------------------------------------------
        pid_to_sid = {p.pid: sid for sid, p in snap.processes.items() if p.pid is not None}
        for sid, p in snap.processes.items():
            nodes[sid] = TNode(
                id=sid,
                kind="PROCESS",
                label=p.name,
                data={
                    "pid": p.pid,
                    "name": p.name,
                    "exe": p.exe,
                    "username": p.username,
                    "status": p.status,
                    "cpu_percent": p.cpu_percent,
                    "memory_mb": p.memory_mb,
                    "num_threads": p.num_threads,
                    "ppid": p.ppid,
                    "parent_sid": pid_to_sid.get(p.ppid) if p.ppid is not None else None,
                    "created": p.create_time,
                    "cmdline": redact_cmdline(p.cmdline),
                    "conn_count": 0,
                    "listening_ports": [],
                },
            )

        # ---- pass 1: localhost pairing ---------------------------------------
        pair_map: dict[str, str] = {}  # conn key -> paired process stable id
        by_side: dict[tuple[str, int, str, int], str] = {}
        for ckey, c in snap.connections.items():
            if c.kind == "localhost":
                by_side[(c.local_ip, c.local_port, c.remote_ip, c.remote_port)] = ckey
        for ckey, c in snap.connections.items():
            if c.kind != "localhost":
                continue
            other = by_side.get((c.remote_ip, c.remote_port, c.local_ip, c.local_port))
            if other and other != ckey:
                own_owner = snap.owner_map.get(ckey)
                other_owner = snap.owner_map.get(other)
                if own_owner and other_owner and own_owner != other_owner:
                    pair_map[ckey] = other_owner

        # ---- pass 2: node sets with caps --------------------------------------
        ext_nodes: set[str] = set()
        listen_nodes: set[str] = set()
        loc_nodes: set[str] = set()
        ext_overflow: dict[str, list[ConnectionInfo]] = defaultdict(list)
        listen_overflow: dict[str, list[ConnectionInfo]] = defaultdict(list)
        loc_overflow: dict[str, list[ConnectionInfo]] = defaultdict(list)

        for ckey, c in snap.connections.items():
            owner = snap.owner_map.get(ckey) or SYSTEM_NODE_ID
            if c.kind == "listening":
                nid = listen_node_id(c.proto, c.local_ip, c.local_port)
                if nid in listen_nodes or len(listen_nodes) < self.cfg.max_listen_nodes:
                    listen_nodes.add(nid)
                else:
                    listen_overflow[owner].append(c)
            elif c.kind == "external":
                nid = ext_node_id(c.remote_ip)
                if nid in ext_nodes or len(ext_nodes) < self.cfg.max_external_nodes:
                    ext_nodes.add(nid)
                else:
                    ext_overflow[owner].append(c)
            elif c.kind == "localhost" and ckey not in pair_map:
                nid = loc_node_id(c.proto, c.remote_ip, c.remote_port)
                if nid in loc_nodes or len(loc_nodes) < self.cfg.max_loc_nodes:
                    loc_nodes.add(nid)
                else:
                    loc_overflow[owner].append(c)

        for nid in ext_nodes:
            ip = nid.split(":", 1)[1]
            nodes[nid] = TNode(id=nid, kind="EXTERNAL_ENDPOINT", label=ip, data={"ip": ip, "ports": []})
        for nid in listen_nodes:
            proto, ip, port = _parse_listen(nid)
            nodes[nid] = TNode(id=nid, kind="LISTENING_PORT", label=f":{port}",
                               data={"ip": ip, "port": port, "proto": proto})
        for nid in loc_nodes:
            proto, ip, port = _parse_loc(nid)
            nodes[nid] = TNode(id=nid, kind="LOCAL_ENDPOINT", label=f"{ip}:{port}",
                               data={"ip": ip, "port": port, "proto": proto})
        for owner, conns in ext_overflow.items():
            nodes[f"ext-agg:{owner}"] = TNode(
                id=f"ext-agg:{owner}", kind="EXTERNAL_ENDPOINT", label=f"EXTERNAL x{len(conns)}",
                data={"ip": "*", "ports": [c.remote_port for c in conns[:8]], "aggregated": True},
            )
        for owner, conns in listen_overflow.items():
            nodes[f"lst-agg:{owner}"] = TNode(
                id=f"lst-agg:{owner}", kind="LISTENING_PORT", label=f"LISTEN x{len(conns)}",
                data={"ip": "*", "port": 0, "proto": "tcp", "aggregated": True},
            )
        for owner, conns in loc_overflow.items():
            nodes[f"loc-agg:{owner}"] = TNode(
                id=f"loc-agg:{owner}", kind="LOCAL_ENDPOINT", label=f"LOCAL x{len(conns)}",
                data={"ip": "*", "port": 0, "proto": "tcp", "aggregated": True},
            )

        def _target(ckey: str, c: ConnectionInfo, owner: str) -> tuple[str, str]:
            if c.kind == "listening":
                nid = listen_node_id(c.proto, c.local_ip, c.local_port)
                return (nid if nid in listen_nodes else f"lst-agg:{owner}", "LISTEN")
            if c.kind == "external":
                nid = ext_node_id(c.remote_ip)
                return (nid if nid in ext_nodes else f"ext-agg:{owner}", "EXTERNAL")
            if ckey in pair_map:
                return (pair_map[ckey], "LOCALHOST")
            nid = loc_node_id(c.proto, c.remote_ip, c.remote_port)
            return (nid if nid in loc_nodes else f"loc-agg:{owner}", "LOCALHOST")

        # ---- pass 3: edges ----------------------------------------------------
        active_count = 0
        listen_count = 0
        for ckey, c in snap.connections.items():
            owner = snap.owner_map.get(ckey) or SYSTEM_NODE_ID
            tgt, kind = _target(ckey, c, owner)
            src, tgt_c = _canon(owner, tgt, kind)
            eid = edge_id(src, tgt_c, kind)
            conn_targets[ckey] = (tgt, kind, eid)

            if c.kind == "listening":
                listen_count += 1
                active = True
            elif c.proto == "udp":
                active_count += 1
                active = True
            else:
                if c.state == "ESTABLISHED":
                    active_count += 1
                active = c.state == "ESTABLISHED"

            port = c.local_port if c.kind == "listening" else (c.remote_port if c.kind != "localhost" else c.local_port)

            edge = edges.get(eid)
            if edge is None:
                edges[eid] = TEdge(
                    id=eid, source=src, target=tgt_c, kind=kind,
                    proto=c.proto, ports=[port], active=active,
                    directed=kind in ("LISTEN", "EXTERNAL"),
                )
            else:
                _merge_ports(edge.ports, port)
                edge.active = edge.active or active
                if edge.proto != c.proto:
                    edge.proto = "tcp+udp"

        # ---- per-process enrichment + stats ------------------------------------
        for e in edges.values():
            for nid in (e.source, e.target):
                n = nodes.get(nid)
                if n and n.kind == "PROCESS":
                    n.data["conn_count"] += 1
                    if e.kind == "LISTEN":
                        n.data["listening_ports"].extend(e.ports)

        stats = Stats(
            processes=len(snap.processes),
            active_conns=active_count,
            listening=listen_count,
            cpu_percent=sys.get("cpu_percent", 0.0),
            mem_percent=sys.get("mem_percent", 0.0),
            ts=snap.ts,
        )
        return TopologyResult(ts=snap.ts, nodes=nodes, edges=edges, conn_targets=conn_targets, stats=stats)
