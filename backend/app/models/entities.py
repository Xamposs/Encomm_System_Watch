"""Core entity dataclasses shared across collectors and services."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProcessInfo:
    pid: int
    create_time: float
    name: str
    exe: Optional[str]
    username: Optional[str]
    status: str
    cpu_percent: float
    memory_mb: float
    num_threads: int
    ppid: Optional[int]
    cmdline: list[str] = field(default_factory=list)

    @property
    def stable_id(self) -> str:
        """Stable identity: PID + process creation time (Windows reuses PIDs)."""
        return f"proc:{self.pid}:{int(self.create_time * 1000)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "create_time": round(self.create_time, 3),
            "name": self.name,
            "exe": self.exe,
            "username": self.username,
            "status": self.status,
            "cpu_percent": self.cpu_percent,
            "memory_mb": self.memory_mb,
            "num_threads": self.num_threads,
            "ppid": self.ppid,
            "cmdline": self.cmdline,
        }


@dataclass
class ConnectionInfo:
    pid: Optional[int]
    proto: str
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    state: Optional[str]
    kind: str = "external"  # listening | localhost | external

    @property
    def key(self) -> str:
        """Lifecycle-stable key (state intentionally excluded)."""
        return f"{self.proto}|{self.pid}|{self.local_ip}|{self.local_port}|{self.remote_ip}|{self.remote_port}"


@dataclass
class Snapshot:
    ts: float
    processes: dict[str, ProcessInfo]       # stable_id -> info
    connections: dict[str, ConnectionInfo]  # key -> info
    owner_map: dict[str, Optional[str]]     # conn key -> stable_id or None (system-owned)
    system: dict[str, Any]


@dataclass
class TNode:
    id: str
    kind: str
    label: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "data": self.data}


@dataclass
class TEdge:
    id: str
    source: str
    target: str
    kind: str           # LOCALHOST | EXTERNAL | LISTEN
    proto: str
    ports: list[int]
    active: bool
    directed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "proto": self.proto,
            "ports": sorted(set(self.ports)),
            "active": self.active,
            "directed": self.directed,
        }


@dataclass
class Stats:
    processes: int = 0
    active_conns: int = 0
    listening: int = 0
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    ts: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "processes": self.processes,
            "active_conns": self.active_conns,
            "listening": self.listening,
            "cpu_percent": round(self.cpu_percent, 1),
            "mem_percent": round(self.mem_percent, 1),
            "ts": round(self.ts, 3),
        }


@dataclass
class TopologyResult:
    ts: float
    nodes: dict[str, TNode]
    edges: dict[str, TEdge]
    conn_targets: dict[str, tuple[str, str, str]]  # conn key -> (target_node_id, edge_kind, edge_id)
    stats: Stats


@dataclass
class Event:
    event_type: str
    source: str
    target: Optional[str]
    timestamp: str
    metadata: dict[str, Any]
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "target": self.target,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }
