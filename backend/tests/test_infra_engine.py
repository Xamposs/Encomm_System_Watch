"""InfraEngine tests (v0.4.0): nodes, edges, change-only events, truthfulness.

Covers service->process mapping, shared process hosting, EXPOSES matching,
network relationships, GPU attribution boundaries, no duplicates and no
orphan edges.
"""
import pytest

from app.collectors.docker import DockerContainer, DockerState
from app.collectors.services import ServiceInfo
from app.collectors.vm import VmInfo, VmState
from app.collectors.wsl import WslDistro, WslState
from app.services.infra import (
    EVENT_CONTAINER_CREATED,
    EVENT_CONTAINER_REMOVED,
    EVENT_CONTAINER_STARTED,
    EVENT_CONTAINER_STOPPED,
    EVENT_SERVICE_STARTED,
    EVENT_SERVICE_STATUS_CHANGED,
    EVENT_SERVICE_STOPPED,
    EVENT_VM_DETECTED,
    EVENT_VM_LOST,
    EVENT_VM_STATE_CHANGED,
    EVENT_WSL_STATE_CHANGED,
    InfraEngine,
)
from app.services.topology import TopologyEngine
from conftest import make_conn, make_proc, make_snap


def _svc(name, status="running", pid=1001, display=None):
    return ServiceInfo(name=name, display_name=display or name, status=status,
                       start_type="Auto", account="LocalSystem",
                       binpath=f"C:\\svc\\{name}.exe -k x", description="d",
                       pid=pid)


def _wsl_state(*distros):
    st = WslState(installed=True, distributions=list(distros))
    st.running = [d.name for d in distros if d.state == "Running"]
    return st


def _docker(*containers, engine="RUNNING", version="29.5.3", available=True):
    return DockerState(available=available, engine_status=engine,
                       version=version, containers=list(containers),
                       source="CLI (docker)")


def _vm(provider="HYPER_V", name="Lab1", state="RUNNING", host_pid=None,
        confidence="CONFIRMED"):
    return VmInfo(provider=provider, name=name, state=state,
                  confidence=confidence, evidence="fixture", host_pid=host_pid,
                  metadata={})


def _snapshot_topology(cfg, procs, conns):
    snap = make_snap(procs=procs, conns=conns)
    topo = TopologyEngine(cfg).build(snap)
    return snap, topo


def _engine(cfg):
    return InfraEngine(cfg)


def test_service_process_mapping(cfg):
    """A running service with a real PID maps HOSTED_BY to the process node."""
    proc = make_proc(1001, name="svchost.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    eng.update([_svc("BFE", pid=1001)], _wsl_state(), _docker(),
               VmState(), snap, topo, [])
    nodes = {n.id: n for n in eng.nodes()}
    assert "svc:BFE" in nodes
    assert nodes["svc:BFE"].kind == "SERVICE"
    edges = eng.edges()
    hosted = [e for e in edges if e.kind == "HOSTED_BY"]
    assert len(hosted) == 1
    assert hosted[0].source == "svc:BFE"
    assert hosted[0].target == proc.stable_id
    # process node carries the infra role for the INFRA view
    from app.models.entities import TNode
    augmented = eng.augment_process_nodes([TNode(**topo.nodes[proc.stable_id].to_dict())])
    assert augmented[0].data.get("infra", {}).get("role") == "service_host"


def test_shared_process_hosting_truthful(cfg):
    """Two services in one svchost.exe -> two edges to ONE process node."""
    proc = make_proc(3624, name="svchost.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    eng.update([_svc("BFE", pid=3624), _svc("mpssvc", pid=3624)],
               _wsl_state(), _docker(available=False), VmState(), snap, topo, [])
    hosted = [e for e in eng.edges() if e.kind == "HOSTED_BY"]
    assert len(hosted) == 2
    assert {e.source for e in hosted} == {"svc:BFE", "svc:mpssvc"}
    assert all(e.target == proc.stable_id for e in hosted)
    # exactly one process node — never one fake process per service
    assert len(eng.nodes()) == 2  # 2 service nodes


def test_service_without_pid_no_edge(cfg):
    proc = make_proc(1001)
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    eng.update([_svc("wuauserv", status="stopped", pid=None)],
               _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert all(e.kind != "HOSTED_BY" for e in eng.edges())


def test_wsl_hosts_edge(cfg):
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    eng.update([], _wsl_state(WslDistro(name="Ubuntu", state="Stopped", version=2)),
               _docker(), VmState(), snap, topo, [])
    nodes = {n.id: n for n in eng.nodes()}
    assert "wsl:Ubuntu" in nodes
    assert nodes["wsl:Ubuntu"].kind == "WSL"
    assert nodes["wsl:Ubuntu"].label == "⬡ Ubuntu\nSTOPPED\nWSL2"
    edges = eng.edges()
    hosts = [e for e in edges if e.kind == "HOSTS" and e.target == "wsl:Ubuntu"]
    assert len(hosts) == 1
    assert hosts[0].source == "sys:windows"


def test_docker_engine_and_exposes(cfg):
    """EXPOSES edges appear ONLY when the topology has the listening node."""
    proc = make_proc(2001, name="com.docker.backend.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [
        make_conn(2001, lip="0.0.0.0", lport=5433, kind="listening"),
    ])
    eng = _engine(cfg)
    c = DockerContainer(id="abc123def456", name="postgres", image="postgres:16",
                        state="running", status="Up",
                        ports=[{"host_ip": "0.0.0.0", "host_port": 5433,
                                "container_port": 5432, "proto": "tcp"}],
                        networks=["bridge"])
    eng.update([], _wsl_state(), _docker(c), VmState(), snap, topo, [])
    nodes = {n.id: n for n in eng.nodes()}
    assert "docker:engine" in nodes
    assert nodes["docker:engine"].kind == "DOCKER_ENGINE"
    assert "container:abc123def456" in nodes
    assert nodes["container:abc123def456"].kind == "CONTAINER"
    edges = eng.edges()
    exposes = [e for e in edges if e.kind == "EXPOSES"]
    assert len(exposes) == 1
    assert exposes[0].target == "lst:tcp:0.0.0.0:5433"
    assert exposes[0].ports == [5433]
    connected = [e for e in edges if e.kind == "CONNECTED_TO"]
    assert len(connected) == 1
    assert connected[0].target == "dockernet:bridge"
    # engine hosts container
    assert any(e.kind == "HOSTS" and e.source == "docker:engine"
               and e.target == "container:abc123def456" for e in edges)


def test_exposes_no_listen_node_no_edge(cfg):
    """A Docker port mapping without a matching topology listener must NOT
    invent a port node or edge."""
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    c = DockerContainer(id="abc123def456", name="nginx", image="nginx",
                        state="running",
                        ports=[{"host_ip": "0.0.0.0", "host_port": 8080,
                                "container_port": 80, "proto": "tcp"}])
    eng.update([], _wsl_state(), _docker(c), VmState(), snap, topo, [])
    assert all(e.kind != "EXPOSES" for e in eng.edges())
    assert not any(n.kind == "LISTENING_PORT" for n in eng.nodes())


def test_engine_not_running_no_containers(cfg):
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    eng.update([], _wsl_state(), _docker(engine="NOT_RUNNING"), VmState(),
               snap, topo, [])
    nodes = {n.id: n for n in eng.nodes()}
    assert "docker:engine" in nodes
    assert nodes["docker:engine"].data["engine_status"] == "NOT_RUNNING"
    assert not any(n.kind == "CONTAINER" for n in eng.nodes())


def test_vm_backed_by_and_gpu_boundary(cfg):
    """BACKED_BY uses the real host process; USES_GPU ONLY when NVML proves
    the host PID is on the GPU — a busy GPU alone is never attributed."""
    proc = make_proc(9000, name="vmwp.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    vm = _vm(host_pid=9000)
    # GPU state does NOT contain pid 9000 -> no USES_GPU
    eng.update([], _wsl_state(), _docker(), VmState(vms=[vm]), snap, topo,
               [{"index": 0, "processes": [{"pid": 555}]}])
    edges = eng.edges()
    backed = [e for e in edges if e.kind == "BACKED_BY"]
    assert len(backed) == 1
    assert backed[0].source.startswith("vm:hyper_v")
    assert backed[0].target == proc.stable_id
    assert all(e.kind != "USES_GPU" for e in edges)
    # GPU state PROVES pid 9000 -> USES_GPU appears
    eng.update([], _wsl_state(), _docker(), VmState(vms=[vm]), snap, topo,
               [{"index": 0, "processes": [{"pid": 9000}]}])
    uses = [e for e in eng.edges() if e.kind == "USES_GPU"]
    assert len(uses) == 1
    assert uses[0].target == "gpu:0"


def test_generic_virtualization_process_node(cfg):
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    eng.update([], _wsl_state(), _docker(),
               VmState(vms=[_vm(provider="OTHER", name=None, state="RUNNING",
                                host_pid=777, confidence="LOW")]),
               snap, topo, [])
    nodes = {n.id: n for n in eng.nodes()}
    vnode = next(n for n in eng.nodes() if n.kind == "VM")
    assert vnode.label.startswith("▣ VIRTUALIZATION PROCESS")
    assert vnode.data["name"] is None
    assert vnode.data["confidence"] == "LOW"
    assert "vm:other:virtualization_process" in nodes


def test_no_duplicates_no_orphans(cfg):
    proc = make_proc(1001, name="svchost.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [
        make_conn(1001, lip="0.0.0.0", lport=5433, kind="listening"),
    ])
    eng = _engine(cfg)
    services = [_svc("A", pid=1001), _svc("B", pid=1001)]
    containers = [
        DockerContainer(id="aabbccddeeff", name="web", image="nginx",
                        state="running",
                        ports=[{"host_ip": "0.0.0.0", "host_port": 5433,
                                "container_port": 5432, "proto": "tcp"}],
                        networks=["bridge", "esw-net"]),
        DockerContainer(id="112233445566", name="db", image="postgres",
                        state="exited",
                        ports=[{"host_ip": None, "host_port": None,
                                "container_port": 5432, "proto": "tcp"}]),
    ]
    vms = VmState(vms=[_vm(host_pid=1001)])
    eng.update(services, _wsl_state(WslDistro(name="Ubuntu", state="Stopped")),
               _docker(*containers), vms, snap, topo,
               [{"index": 0, "processes": [{"pid": 1001}]}])
    nodes = eng.nodes()
    edges = eng.edges()
    ids = [n.id for n in nodes]
    assert len(ids) == len(set(ids)), "duplicate node ids"
    eids = [e.id for e in edges]
    assert len(eids) == len(set(eids)), "duplicate edge ids"
    # infra edges may target raw topology nodes (processes, listening ports)
    # or semantic nodes (gpu:*): the union of infra + topology + semantic
    # node ids must cover every endpoint (mirrors the snapshot merge)
    node_ids = set(ids) | set(topo.nodes) | {"gpu:0"}
    for e in edges:
        assert e.source in node_ids, f"orphan edge source {e.source}"
        assert e.target in node_ids, f"orphan edge target {e.target}"
    # classification correctness
    kinds = {n.kind for n in nodes}
    assert kinds == {"SERVICE", "WSL", "DOCKER_ENGINE", "CONTAINER",
                     "DOCKER_NETWORK", "VM"}


def test_events_change_only(cfg):
    proc = make_proc(1001)
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    # baseline: silent
    assert eng.update([_svc("Svc", pid=1001)], _wsl_state(), _docker(),
                      VmState(), snap, topo, []) == []
    # unchanged: silent
    assert eng.update([_svc("Svc", pid=1001)], _wsl_state(), _docker(),
                      VmState(), snap, topo, []) == []
    # started
    ev = eng.update([_svc("NewSvc", status="running", pid=1002)],
                    _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_SERVICE_STARTED]
    assert ev[0].metadata["node"]["kind"] == "SERVICE"
    # stopped
    ev = eng.update([_svc("NewSvc", status="stopped", pid=None)],
                    _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_SERVICE_STOPPED]
    # status change paused
    ev = eng.update([_svc("NewSvc", status="paused", pid=1002)],
                    _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_SERVICE_STATUS_CHANGED]


def test_unknown_status_is_not_a_transition(cfg):
    """A transiently unreadable status ('unknown') flipping to/from running
    must NOT emit lifecycle events — unobservable is not stopped/started."""
    proc = make_proc(1001)
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    assert eng.update([_svc("Svc", status="unknown", pid=None)],
                      _wsl_state(), _docker(), VmState(), snap, topo, []) == []
    # unknown -> running: no STARTED (the previous state was unobservable)
    assert eng.update([_svc("Svc", status="running", pid=1001)],
                      _wsl_state(), _docker(), VmState(), snap, topo, []) == []
    # running -> unknown: no STOPPED either
    assert eng.update([_svc("Svc", status="unknown", pid=None)],
                      _wsl_state(), _docker(), VmState(), snap, topo, []) == []
    # unknown -> running again: still no event
    assert eng.update([_svc("Svc", status="running", pid=1001)],
                      _wsl_state(), _docker(), VmState(), snap, topo, []) == []
    # a REAL transition from a known state still fires
    ev = eng.update([_svc("Svc", status="stopped", pid=None)],
                    _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_SERVICE_STOPPED]


def test_container_events(cfg):
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    # NOTE: each update() receives FRESH objects (like a real collector poll)
    c1 = DockerContainer(id="aaaa1111", name="web", image="nginx", state="created")
    assert eng.update([], _wsl_state(), _docker(c1), VmState(), snap, topo, []) == []
    # same state -> silent
    assert eng.update([], _wsl_state(),
                      _docker(DockerContainer(id="aaaa1111", name="web",
                                              image="nginx", state="created")),
                      VmState(), snap, topo, []) == []
    # created -> running
    ev = eng.update([], _wsl_state(),
                    _docker(DockerContainer(id="aaaa1111", name="web",
                                            image="nginx", state="running")),
                    VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_CONTAINER_STARTED]
    # running -> exited
    ev = eng.update([], _wsl_state(),
                    _docker(DockerContainer(id="aaaa1111", name="web",
                                            image="nginx", state="exited")),
                    VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_CONTAINER_STOPPED]
    # removed
    ev = eng.update([], _wsl_state(), _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_CONTAINER_REMOVED]
    # brand new running container -> STARTED, brand new created -> CREATED
    ev = eng.update([], _wsl_state(),
                    _docker(DockerContainer(id="bbbb2222", name="x", image="i",
                                            state="running"),
                            DockerContainer(id="cccc3333", name="y", image="i",
                                            state="created")),
                    VmState(), snap, topo, [])
    assert sorted(e.event_type for e in ev) == [EVENT_CONTAINER_CREATED,
                                                EVENT_CONTAINER_STARTED]


def test_wsl_and_vm_events(cfg):
    snap, topo = _snapshot_topology(cfg, [], [])
    eng = _engine(cfg)
    wsl = _wsl_state(WslDistro(name="Ubuntu", state="Stopped", version=2))
    assert eng.update([], wsl, _docker(), VmState(), snap, topo, []) == []
    ev = eng.update([], _wsl_state(WslDistro(name="Ubuntu", state="Running", version=2)),
                    _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_WSL_STATE_CHANGED]
    assert ev[0].metadata["state"] == "Running"

    vm = _vm(state="RUNNING")
    wsl_running = _wsl_state(WslDistro(name="Ubuntu", state="Running", version=2))
    ev = eng.update([], wsl_running, _docker(), VmState(vms=[vm]),
                    snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_VM_DETECTED]
    ev = eng.update([], wsl_running, _docker(), VmState(vms=[_vm(state="OFF")]),
                    snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_VM_STATE_CHANGED]
    ev = eng.update([], wsl_running, _docker(), VmState(), snap, topo, [])
    assert [e.event_type for e in ev] == [EVENT_VM_LOST]


def test_state_dict_shared_hosts(cfg):
    proc = make_proc(3624, name="svchost.exe")
    snap, topo = _snapshot_topology(cfg, [proc], [])
    eng = _engine(cfg)
    eng.update([_svc("BFE", pid=3624), _svc("mpssvc", pid=3624),
                _svc("StoppedSvc", status="stopped", pid=None)],
               _wsl_state(), _docker(), VmState(), snap, topo, [])
    d = eng.state_dict()
    assert d["services"]["count"] == 3
    assert d["services"]["running"] == 2
    assert d["services"]["stopped"] == 1
    assert d["services"]["pid_mappings"] == 2
    shared = d["services"]["shared_hosts"]
    assert len(shared) == 1
    assert shared[0]["pid"] == 3624
    assert set(shared[0]["services"]) == {"BFE", "mpssvc"}
    assert d["summary"]["services"]["total"] == 3
