"""Infrastructure semantic engine (v0.4.0) — services, WSL, Docker, VMs.

Additive layer between the infra collectors and the WebSocket, mirroring the
semantic engine pattern:

    COLLECTORS (services / wsl / docker / vm)
        -> InfraState (normalized, read-only)
        -> InfraEngine.update()  (diff against previous state)
        -> INFRA NODES / EDGES   (own ids: svc:*, wsl:*, docker:*, container:*,
                                  dockernet:*, vm:*; edges infra:*)
        -> CHANGE-ONLY EVENTS    (no event spam — first sample is baseline)
        -> WEBSOCKET snapshot merge + events
        -> INFRA VIEW (frontend, classification-driven)

Evidence rules (never invented relationships):
  - SERVICE --HOSTED_BY--> PROCESS  only when the service exposes a PID that
    maps to a live process node; N services may share one svchost.exe — each
    gets its own truthful edge, no fake process per service.
  - WINDOWS HOST --HOSTS--> WSL / DOCKER ENGINE / VM   (direct evidence).
  - CONTAINER --EXPOSES--> LISTENING_PORT  only when Docker proves a host
    port mapping AND the topology actually has that listening node.
  - CONTAINER --CONNECTED_TO--> NETWORK  only when Docker metadata names it.
  - VM --BACKED_BY--> PROCESS  only for the REAL host process (vmwp.exe /
    vmware-vmx.exe / VBox*), never a guest process.
  - VM --USES_GPU--> GPU  only when NVML PID attribution proves the VM HOST
    process uses that GPU. Guest GPU use is NEVER inferred.
  - Unproven hypervisor processes become "VIRTUALIZATION PROCESS" nodes
    (name=None) — never a made-up VM name.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from ..collectors.docker import DockerContainer, DockerState
from ..collectors.services import ServiceInfo
from ..collectors.vm import VmInfo, VmState
from ..collectors.wsl import WslDistro, WslState
from ..config import Settings
from ..detectors.redact import redact_cmdline
from ..models.entities import Event, Snapshot, TEdge, TNode, TopologyResult
from .topology import SYSTEM_NODE_ID

EVENT_SERVICE_STARTED = "SERVICE_STARTED"
EVENT_SERVICE_STOPPED = "SERVICE_STOPPED"
EVENT_SERVICE_STATUS_CHANGED = "SERVICE_STATUS_CHANGED"
EVENT_CONTAINER_STARTED = "CONTAINER_STARTED"
EVENT_CONTAINER_STOPPED = "CONTAINER_STOPPED"
EVENT_CONTAINER_CREATED = "CONTAINER_CREATED"
EVENT_CONTAINER_REMOVED = "CONTAINER_REMOVED"
EVENT_WSL_STATE_CHANGED = "WSL_STATE_CHANGED"
EVENT_VM_DETECTED = "VM_DETECTED"
EVENT_VM_LOST = "VM_LOST"
EVENT_VM_STATE_CHANGED = "VM_STATE_CHANGED"

_MAX_DOCKER_NETWORKS = 10
_MAX_SERVICES_IN_EVENT = 1  # one service per event — the drawer stays compact


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _svc_id(name: str) -> str:
    return f"svc:{name}"


def _wsl_id(name: str) -> str:
    return f"wsl:{name}"


def _container_id(cid: str) -> str:
    return f"container:{cid}"


def _net_id(name: str) -> str:
    return f"dockernet:{name}"


def _vm_id(vm: VmInfo) -> str:
    ident = vm.identity.lower().replace(" ", "_") or "unknown"
    return f"vm:{vm.provider.lower()}:{ident}"


class InfraEngine:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        # None = no sample yet (baseline); {} = sampled and currently empty.
        # The distinction matters: containers appearing after an empty poll
        # are real CONTAINER_CREATED/STARTED events, not a new baseline.
        self._services: Optional[dict[str, ServiceInfo]] = None
        self._containers: Optional[dict[str, DockerContainer]] = None
        self._wsl: Optional[dict[str, WslDistro]] = None
        self._vms: Optional[dict[str, VmInfo]] = None
        self._docker_state: DockerState = DockerState()
        self._wsl_state: WslState = WslState()
        self._vm_state: VmState = VmState()
        self._pid_sid: dict[int, str] = {}
        self._proc_infra: dict[str, dict[str, Any]] = {}  # sid -> infra role data
        self._topo: Optional[TopologyResult] = None
        self._gpu_state: list[dict[str, Any]] = []
        self._listen_index: dict[tuple[str, int], list[str]] = {}
        self._ts = 0.0
        self._seq = 0

    # ------------------------------------------------------------- state api

    def nodes(self) -> list[TNode]:
        out: list[TNode] = []
        for svc in (self._services or {}).values():
            out.append(self._service_node(svc))
        for d in (self._wsl or {}).values():
            out.append(self._wsl_node(d))
        if self._docker_state.available:
            out.append(self._engine_node(self._docker_state))
        for c in (self._containers or {}).values():
            out.append(self._container_node(c))
        nets = self._used_networks()
        for name in nets:
            out.append(self._net_node(name))
        for v in (self._vms or {}).values():
            out.append(self._vm_node(v))
        return out

    def edges(self) -> list[TEdge]:
        out: list[TEdge] = []
        # service -> process (real PID evidence, shared hosts get N edges)
        for svc in (self._services or {}).values():
            if svc.pid is None:
                continue
            sid = self._pid_sid.get(svc.pid)
            if not sid:
                continue
            out.append(TEdge(
                id=f"infra:{_svc_id(svc.name)}->{sid}:HOSTED_BY",
                source=_svc_id(svc.name), target=sid, kind="HOSTED_BY",
                proto="infra", ports=[], active=True, directed=True,
            ))
        # windows host -> wsl / docker engine / vm
        for d in (self._wsl or {}).values():
            out.append(TEdge(
                id=f"infra:{SYSTEM_NODE_ID}->{_wsl_id(d.name)}:HOSTS",
                source=SYSTEM_NODE_ID, target=_wsl_id(d.name), kind="HOSTS",
                proto="infra", ports=[], active=True, directed=True,
            ))
        if self._docker_state.available:
            for c in (self._containers or {}).values():
                out.append(TEdge(
                    id=f"infra:docker:engine->{_container_id(c.id)}:HOSTS",
                    source="docker:engine", target=_container_id(c.id), kind="HOSTS",
                    proto="infra", ports=[], active=True, directed=True,
                ))
        # container -> exposed listening port (proven host mappings only)
        for c in (self._containers or {}).values():
            for p in c.ports:
                if p.get("host_port") is None:
                    continue
                match = self._match_listen_node(p)
                if match:
                    out.append(TEdge(
                        id=f"infra:{_container_id(c.id)}->{match}:EXPOSES",
                        source=_container_id(c.id), target=match, kind="EXPOSES",
                        proto="infra", ports=[int(p["host_port"])], active=True,
                        directed=True,
                    ))
            for n in c.networks:
                out.append(TEdge(
                    id=f"infra:{_container_id(c.id)}->{_net_id(n)}:CONNECTED_TO",
                    source=_container_id(c.id), target=_net_id(n), kind="CONNECTED_TO",
                    proto="infra", ports=[], active=True, directed=True,
                ))
        # vm -> host process / gpu (NVML-attributed host PID only)
        for v in (self._vms or {}).values():
            out.append(TEdge(
                id=f"infra:{SYSTEM_NODE_ID}->{_vm_id(v)}:HOSTS",
                source=SYSTEM_NODE_ID, target=_vm_id(v), kind="HOSTS",
                proto="infra", ports=[], active=True, directed=True,
            ))
            if v.host_pid is not None:
                sid = self._pid_sid.get(v.host_pid)
                if sid:
                    out.append(TEdge(
                        id=f"infra:{_vm_id(v)}->{sid}:BACKED_BY",
                        source=_vm_id(v), target=sid, kind="BACKED_BY",
                        proto="infra", ports=[], active=True, directed=True,
                    ))
                for g in self._gpu_state:
                    for p in g.get("processes", []):
                        if int(p.get("pid") or -1) == v.host_pid:
                            out.append(TEdge(
                                id=f"infra:{_vm_id(v)}->gpu:{g.get('index', 0)}:USES_GPU",
                                source=_vm_id(v), target=f"gpu:{g.get('index', 0)}",
                                kind="USES_GPU", proto="infra", ports=[],
                                active=True, directed=True,
                            ))
        return out

    def augment_process_nodes(self, nodes: list[TNode]) -> list[TNode]:
        """Mark service-host / VM-backend processes for the INFRA view."""
        for n in nodes:
            if n.kind != "PROCESS":
                continue
            role = self._proc_infra.get(n.id)
            if role:
                n.data["infra"] = role
        return nodes

    def summary(self) -> dict[str, Any]:
        services = list((self._services or {}).values())
        running_svc = sum(1 for s in services if s.status == "running")
        return {
            "services": {
                "total": len(services),
                "running": running_svc,
                "stopped": sum(1 for s in services if s.status == "stopped"),
            },
            "wsl": {
                "distributions": len(self._wsl or {}),
                "running": len(self._wsl_state.running),
            },
            "docker": {
                "available": self._docker_state.available,
                "engine": self._docker_state.engine_status,
                "containers": len(self._containers or {}),
                "running": sum(1 for c in (self._containers or {}).values()
                               if c.state == "running"),
            },
            "vms": {
                "total": len(self._vms or {}),
                "running": sum(1 for v in (self._vms or {}).values()
                               if v.state == "RUNNING"),
                "providers": [
                    p for p, d in self._vm_state.providers.items()
                    if d.get("installed") or d.get("count", 0) > 0
                ],
            },
        }

    def state_dict(self) -> dict[str, Any]:
        """Full read-only infra state for GET /api/infra."""
        services = list((self._services or {}).values())
        by_pid: dict[int, list[ServiceInfo]] = defaultdict(list)
        pid_mappings = 0
        for s in services:
            if s.pid is not None:
                by_pid[s.pid].append(s)
                pid_mappings += 1
        shared_hosts = [
            {"pid": pid, "process": self._pid_sid.get(pid),
             "services": [s.name for s in group]}
            for pid, group in sorted(by_pid.items())
            if len(group) > 1
        ][:20]
        return {
            "services": {
                "count": len(services),
                "running": sum(1 for s in services if s.status == "running"),
                "stopped": sum(1 for s in services if s.status == "stopped"),
                "pid_mappings": pid_mappings,
                "shared_hosts": shared_hosts,
                "services": [s.to_dict() for s in services],
            },
            "wsl": self._wsl_state.to_dict(),
            "docker": self._docker_state.to_dict(),
            "vms": self._vm_state.to_dict(),
            "summary": self.summary(),
            "ts": self._ts,
        }

    # -------------------------------------------------------------- pipeline

    def update(
        self,
        services: list[ServiceInfo],
        wsl: WslState,
        docker: DockerState,
        vms: VmState,
        snap: Snapshot,
        topo: TopologyResult,
        gpu_state: list[dict[str, Any]],
    ) -> list[Event]:
        """Adopt the new state; diff against the previous one; emit events.

        First sample establishes the baseline — no startup event storm.
        """
        self._ts = time.time()
        self._pid_sid = {p.pid: sid for sid, p in snap.processes.items() if p.pid is not None}
        self._topo = topo
        # listen-node index for EXPOSES matching: (proto, port) -> [node ids]
        idx: dict[tuple[str, int], list[str]] = {}
        for nid, n in topo.nodes.items():
            if n.kind == "LISTENING_PORT":
                proto = str(n.data.get("proto", "tcp"))
                port = int(n.data.get("port", -1))
                if port > 0:
                    idx.setdefault((proto, port), []).append(nid)
        self._listen_index = idx
        self._gpu_state = gpu_state
        self._vm_state = vms

        events: list[Event] = []
        events += self._diff_services(services, snap, topo)
        events += self._diff_wsl(wsl)
        events += self._diff_docker(docker)
        events += self._diff_vms(vms)

        # process infra roles for the INFRA view
        roles: dict[str, dict[str, Any]] = defaultdict(lambda: {"services": 0, "vms": 0})
        for s in services:
            if s.pid is not None:
                sid = self._pid_sid.get(s.pid)
                if sid:
                    roles[sid]["services"] += 1
        for v in vms.vms:
            if v.host_pid is not None:
                sid = self._pid_sid.get(v.host_pid)
                if sid:
                    roles[sid]["vms"] += 1
        self._proc_infra = {}
        for sid, r in roles.items():
            if r["services"] or r["vms"]:
                role = "service_host" if r["services"] else "vm_backend"
                self._proc_infra[sid] = {
                    "role": role,
                    "services": r["services"],
                    "vms": r["vms"],
                }
        return events

    # ------------------------------------------------------------- diffs

    def _diff_services(self, services: list[ServiceInfo], snap, topo) -> list[Event]:
        cur = {s.name: s for s in services}
        prev, self._services = self._services, cur
        events: list[Event] = []
        if prev is None:
            return events  # first sample = baseline — no startup storm
        for name, s in cur.items():
            old = prev.get(name)
            if old is None:
                if s.status == "running":
                    events.append(self._svc_event(EVENT_SERVICE_STARTED, s))
                continue
            # `unknown` is NOT a transition: a transiently unreadable status
            # (AccessDenied under load) flipping back to running is not
            # evidence of a start — emitting events for it would spam the
            # drawer with fake lifecycle noise.
            if old.status != s.status and old.status != "unknown" and s.status != "unknown":
                if s.status == "running":
                    events.append(self._svc_event(EVENT_SERVICE_STARTED, s))
                elif old.status == "running":
                    events.append(self._svc_event(EVENT_SERVICE_STOPPED, s))
                else:
                    events.append(self._svc_event(EVENT_SERVICE_STATUS_CHANGED, s))
        return events

    def _diff_wsl(self, wsl: WslState) -> list[Event]:
        cur = {d.name: d for d in wsl.distributions}
        prev, self._wsl = self._wsl, cur
        self._wsl_state = wsl
        events: list[Event] = []
        if prev is None:
            return events
        for name, d in cur.items():
            old = prev.get(name)
            if old is None:
                continue  # new distro installed — no event (not in spec)
            if old.state != d.state:
                events.append(Event(
                    event_id=self._next_id(),
                    event_type=EVENT_WSL_STATE_CHANGED,
                    source=_wsl_id(name), target=None, timestamp=_now_iso(),
                    metadata={
                        "distro": name,
                        "state": d.state,
                        "previous_state": old.state,
                        "version": d.version,
                        "node": self._wsl_node(d).to_dict(),
                        "edges": [
                            TEdge(
                                id=f"infra:{SYSTEM_NODE_ID}->{_wsl_id(name)}:HOSTS",
                                source=SYSTEM_NODE_ID, target=_wsl_id(name),
                                kind="HOSTS", proto="infra", ports=[],
                                active=True, directed=True,
                            ).to_dict()
                        ],
                    },
                ))
        self._wsl = cur
        return events

    def _diff_docker(self, docker: DockerState) -> list[Event]:
        cur = {c.id: c for c in docker.containers}
        prev, self._containers = self._containers, cur
        self._docker_state = docker
        events: list[Event] = []
        if prev is None:
            return events  # first sample = baseline
        for cid, c in cur.items():
            old = prev.get(cid)
            if old is None:
                if c.state == "running":
                    events.append(self._container_event(EVENT_CONTAINER_STARTED, c))
                else:
                    events.append(self._container_event(EVENT_CONTAINER_CREATED, c))
                continue
            if old.state != c.state:
                if c.state == "running":
                    events.append(self._container_event(EVENT_CONTAINER_STARTED, c))
                elif old.state == "running":
                    events.append(self._container_event(EVENT_CONTAINER_STOPPED, c))
        for cid in set(prev) - set(cur):
            events.append(self._container_event(EVENT_CONTAINER_REMOVED, prev[cid]))
        return events

    def _diff_vms(self, vms: VmState) -> list[Event]:
        cur = {_vm_id(v): v for v in vms.vms}
        prev, self._vms = self._vms, cur
        events: list[Event] = []
        if prev is None:
            return events
        for vid, v in cur.items():
            old = prev.get(vid)
            if old is None:
                events.append(self._vm_event(EVENT_VM_DETECTED, v))
                continue
            if old.state != v.state:
                events.append(self._vm_event(EVENT_VM_STATE_CHANGED, v))
        for vid in set(prev) - set(cur):
            events.append(self._vm_event(EVENT_VM_LOST, prev[vid]))
        return events

    # --------------------------------------------------------------- nodes

    @staticmethod
    def _service_node(s: ServiceInfo) -> TNode:
        lines = [f"⚙ {s.display_name or s.name}"]
        state = s.status.upper() if s.status else "UNKNOWN"
        parts = [state]
        if s.start_type:
            parts.append(s.start_type)
        if s.pid is not None:
            parts.append(f"PID {s.pid}")
        lines.append(" · ".join(parts))
        data: dict[str, Any] = {
            "name": s.name,
            "display_name": s.display_name,
            "status": s.status,
            "start_type": s.start_type,
            "inaccessible": s.inaccessible,
        }
        if s.account:
            data["account"] = s.account
        if s.binpath:
            data["binpath"] = " ".join(redact_cmdline(s.binpath.split()))
        if s.description:
            data["description"] = s.description[:300]
        if s.pid is not None:
            data["pid"] = s.pid
        return TNode(id=_svc_id(s.name), kind="SERVICE", label="\n".join(lines), data=data)

    @staticmethod
    def _wsl_node(d: WslDistro) -> TNode:
        ver = f"WSL{d.version}" if d.version is not None else "WSL"
        return TNode(
            id=_wsl_id(d.name), kind="WSL",
            label=f"⬡ {d.name}\n{d.state.upper()}\n{ver}",
            data={
                "name": d.name,
                "state": d.state,
                "version": d.version,
                "is_default": d.is_default,
                "summary": d.summary,
            },
        )

    @staticmethod
    def _engine_node(st: DockerState) -> TNode:
        if st.engine_status == "RUNNING":
            ver = f" · v{st.version}" if st.version else ""
            label = f"◆ DOCKER ENGINE\nRUNNING{ver}"
        else:
            label = "◆ DOCKER ENGINE\nNOT RUNNING"
        return TNode(
            id="docker:engine", kind="DOCKER_ENGINE", label=label,
            data={
                "engine_status": st.engine_status,
                "version": st.version,
                "source": st.source,
                "containers": len(st.containers),
            },
        )

    @staticmethod
    def _container_node(c: DockerContainer) -> TNode:
        image = c.image if len(c.image) <= 48 else c.image[:45] + "…"
        return TNode(
            id=_container_id(c.id), kind="CONTAINER",
            label=f"◇ {c.name}\n{c.state.upper()} · {image}",
            data={
                "id": c.id,
                "name": c.name,
                "image": c.image,
                "state": c.state,
                "status": c.status,
                "created": c.created,
                "ports": c.ports,
                "networks": c.networks,
                "pid": c.pid,
            },
        )

    @staticmethod
    def _net_node(name: str) -> TNode:
        return TNode(
            id=_net_id(name), kind="DOCKER_NETWORK",
            label=f"NET\n{name}",
            data={"name": name},
        )

    @staticmethod
    def _vm_node(v: VmInfo) -> TNode:
        ident = v.identity
        label = f"▣ {ident}\n{v.state} · {v.provider}"
        data: dict[str, Any] = {
            "provider": v.provider,
            "name": v.name,
            "state": v.state,
            "confidence": v.confidence,
            "evidence": v.evidence,
            "metadata": v.metadata,
        }
        if v.vm_id:
            data["vm_id"] = v.vm_id
        if v.host_pid is not None:
            data["host_pid"] = v.host_pid
        return TNode(id=_vm_id(v), kind="VM", label=label, data=data)

    def _used_networks(self) -> list[str]:
        used: set[str] = set()
        for c in (self._containers or {}).values():
            for n in c.networks:
                used.add(n)
        return sorted(used)[:_MAX_DOCKER_NETWORKS]

    def _match_listen_node(self, port: dict[str, Any]) -> Optional[str]:
        """Find the topology LISTENING_PORT node for a proven host mapping.

        Host IP ``0.0.0.0``/``::`` matches any listener on that port (the
        mapping is still proven by Docker; only the exact interface may be
        ambiguous). No node -> no edge (nothing invented).
        """
        if self._topo is None:
            return None
        proto = port.get("proto", "tcp")
        host_port = int(port["host_port"])
        host_ip = port.get("host_ip")
        for proto_key in (proto, "tcp+udp"):
            for nid in self._listen_index.get((proto_key, host_port), []):
                n = self._topo.nodes.get(nid)
                if n is None:
                    continue
                ip = str(n.data.get("ip", ""))
                if ip in ("0.0.0.0", "::", "*"):
                    return nid
                if host_ip in (None, "0.0.0.0", "::"):
                    return nid
                if ip == host_ip:
                    return nid
        return None

    # --------------------------------------------------------------- events

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._seq:06d}-{int(time.time() * 1000)}"

    def _svc_event(self, etype: str, s: ServiceInfo) -> Event:
        return Event(
            event_id=self._next_id(),
            event_type=etype,
            source=_svc_id(s.name), target=None, timestamp=_now_iso(),
            metadata={
                "name": s.name,
                "display_name": s.display_name,
                "status": s.status,
                "pid": s.pid,
                "node": self._service_node(s).to_dict(),
                "edges": [
                    e.to_dict() for e in self.edges()
                    if e.source == _svc_id(s.name)
                ],
            },
        )

    def _container_event(self, etype: str, c: DockerContainer) -> Event:
        return Event(
            event_id=self._next_id(),
            event_type=etype,
            source=_container_id(c.id), target=None, timestamp=_now_iso(),
            metadata={
                "name": c.name,
                "image": c.image,
                "state": c.state,
                "id": c.id,
                "node": self._container_node(c).to_dict(),
                "edges": [
                    e.to_dict() for e in self.edges()
                    if e.source == _container_id(c.id)
                ],
            },
        )

    def _vm_event(self, etype: str, v: VmInfo) -> Event:
        return Event(
            event_id=self._next_id(),
            event_type=etype,
            source=_vm_id(v), target=None, timestamp=_now_iso(),
            metadata={
                "provider": v.provider,
                "name": v.name,
                "state": v.state,
                "confidence": v.confidence,
                "node": self._vm_node(v).to_dict(),
                "edges": [
                    e.to_dict() for e in self.edges()
                    if e.source == _vm_id(v)
                ],
            },
        )
