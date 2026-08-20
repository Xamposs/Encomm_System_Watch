"""TEST-ONLY deterministic large-graph benchmark fixture (v0.3.1).

Generates realistic-shaped synthetic graph topology — process nodes, service
nodes, endpoints, GPU/resource nodes, semantic nodes and their edges — for
renderer/performance validation ONLY. Every node and edge carries
``test_only: true`` / ``benchmark: true`` flags and synthetic identity data
(pid range 400000+, TEST-NET IP ranges) so benchmark data can never be
mistaken for — or mixed with — real machine telemetry.

Guarantees
----------
* Deterministic: same ``node_count`` + ``seed`` -> identical graph.
* Self-contained: all edge endpoints reference existing nodes.
* Explicitly labeled: ``meta.label == "TEST/BENCHMARK (synthetic)"`` and every
  element is flagged ``test_only``.
* Never enters the normal pipeline: the WS snapshot only serves benchmark
  data while ``BenchmarkMode`` is explicitly activated, and real event /
  activity / GPU messages are suppressed for the duration (see main.py).
"""
from __future__ import annotations

import random
from typing import Any

# Synthetic identity ranges — clearly outside anything this machine could
# report for real (Windows PIDs are far below 400000, TEST-NET IPs are
# documentation-only ranges that are never routed on the public internet).
SYNTH_PID_BASE = 400_000
TESTNET_A = "203.0.113"
TESTNET_B = "198.51.100"

# Node kind distribution (proportions of the total node budget)
_PROC = 0.72      # process nodes (incl. service-like processes)
_EXT = 0.08       # external endpoint nodes
_LISTEN = 0.05    # listening-port nodes
_LOCAL = 0.02     # local endpoint nodes
_SYS = 0.01       # system container nodes
_FIXED_SEM = 6    # fixed semantic/GPU/model nodes (hermes, lmstudio, mcp x2, gpu, model)

_PROC_NAMES = [
    "svchost.exe", "chrome.exe", "msedge.exe", "node.exe", "python.exe",
    "explorer.exe", "dwm.exe", "spoolsv.exe", "winlogon.exe", "SearchHost.exe",
    "brave.exe", "firefox.exe", "powershell.exe", "cmd.exe", "WmiPrvSE.exe",
    "RuntimeBroker.exe", "StartMenuExperienceHost.exe", "ctfmon.exe",
    "backgroundTaskHost.exe", "Widgets.exe", "conhost.exe", "Taskmgr.exe",
    "VSSVC.exe", "audiodg.exe", "lsass.exe", "csrss.exe", "wininit.exe",
    "services.exe", "registry.exe", "smss.exe", "fontdrvhost.exe",
    "sihost.exe", "ShellExperienceHost.exe", "TextInputHost.exe",
    "SecurityHealthSystray.exe", "MsMpEng.exe", "dllhost.exe", "unsecapp.exe",
    "OpenConsole.exe", "mscorsvw.exe", "TiWorker.exe", "SearchIndexer.exe",
    "MoUsoCoreWorker.exe", "usocoreworker.exe", "OneDrive.exe",
    "Teams.exe", "Spotify.exe", "Discord.exe", "Slack.exe", "Code.exe",
]
_FAMILY_NAMES = {"chrome.exe", "msedge.exe", "brave.exe", "node.exe", "python.exe", "firefox.exe"}

_SEM_NAME_POOL = ["Bench Hermes", "Bench LM Studio", "Bench MCP filesystem", "Bench MCP github"]
_MODEL_POOL = ["bench-llama-8b", "bench-qwen-7b", "bench-embed-384"]


def _node(node_id: str, kind: str, label: str, **data: Any) -> dict[str, Any]:
    base = {
        "test_only": True,
        "benchmark": True,
        "synthetic": True,
    }
    base.update(data)
    return {"id": node_id, "kind": kind, "label": label, "data": base}


def _edge(edge_id: str, source: str, target: str, kind: str,
          active: bool = False, directed: bool = True,
          ports: list[int] | None = None) -> dict[str, Any]:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "kind": kind,
        "proto": "tcp",
        "ports": ports or [],
        "active": active,
        "directed": directed,
        "test_only": True,
        "benchmark": True,
    }


def generate_benchmark_graph(
    node_count: int,
    seed: int | None = None,
) -> dict[str, Any]:
    """Generate a deterministic synthetic graph with ~``node_count`` nodes.

    Returns ``{"nodes": [...], "edges": [...], "meta": {...}}`` where every
    element is flagged ``test_only`` / ``benchmark``. The fixture is
    renderer-validation data, never telemetry.
    """
    if node_count < 10:
        raise ValueError("benchmark node_count must be >= 10")
    if node_count > 5000:
        raise ValueError("benchmark node_count must be <= 5000")
    rng = random.Random(seed if seed is not None else node_count * 7919)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seq = [0]

    def nid(prefix: str) -> str:
        seq[0] += 1
        return f"bm-{prefix}-{seq[0]:05d}"

    n_proc = max(8, round(node_count * _PROC))
    n_ext = max(2, round(node_count * _EXT))
    n_listen = max(2, round(node_count * _LISTEN))
    n_local = max(1, round(node_count * _LOCAL))
    n_sys = max(1, round(node_count * _SYS))

    # ------------------------------------------------------------ processes
    proc_ids: list[str] = []
    parent_by_id: dict[str, str | None] = {}
    for i in range(n_proc):
        pid = SYNTH_PID_BASE + i
        name = rng.choice(_PROC_NAMES)
        node_id = nid("proc")
        proc_ids.append(node_id)
        # tree-ish parent assignment: most processes hang off an earlier one
        parent: str | None = None
        if i > 0 and rng.random() < 0.62:
            parent = rng.choice(proc_ids[:-1]) if len(proc_ids) > 1 else None
        parent_by_id[node_id] = parent
        cpu = 0.0
        if rng.random() < 0.05:
            cpu = rng.uniform(25, 96)   # high-CPU minority (HIGH CPU filter)
        elif rng.random() < 0.3:
            cpu = rng.uniform(3, 22)
        mem = rng.uniform(4, 900)
        if name in ("chrome.exe", "msedge.exe", "brave.exe") and rng.random() < 0.4:
            mem = rng.uniform(200, 1600)
        nodes.append(_node(
            node_id, "PROCESS", name,
            name=name,
            pid=pid,
            exe=f"C:\\Bench\\{name}",
            username="SYNTHETIC\\bench",
            status="running",
            cpu_percent=round(cpu, 1),
            memory_mb=round(mem, 1),
            num_threads=rng.randint(1, 48),
            parent_sid=parent,
            cmdline=[name, "--benchmark-test-only"],
            conn_count=rng.randint(0, 40),
            highCpu=cpu >= 25 or None,
            semantic=(name in ("python.exe", "node.exe") and rng.random() < 0.2) or None,
        ))
    # family realism: give some root processes >= 2 same-name children (the
    # frontend family view keys off parent_sid + equal names)
    fam_roots_used: set[str] = set()
    for root in list(proc_ids):
        if root in fam_roots_used or parent_by_id.get(root) is not None:
            continue
        if rng.random() >= 0.12:
            continue
        fam_roots_used.add(root)
        # family contract (frontend familyOf): children share the PARENT's
        # name — a root named chrome.exe gets 2-4 chrome.exe children
        root_name = next((n["data"].get("name") for n in nodes if n["id"] == root),
                         rng.choice(sorted(_FAMILY_NAMES)))
        kids = rng.randint(2, 4)
        for _ in range(kids):
            pid = SYNTH_PID_BASE + len(proc_ids)
            node_id = nid("proc")
            proc_ids.append(node_id)
            parent_by_id[node_id] = root
            nodes.append(_node(
                node_id, "PROCESS", root_name,
                name=root_name,
                pid=pid,
                exe=f"C:\\Bench\\{root_name}",
                username="SYNTHETIC\\bench",
                status="running",
                cpu_percent=round(rng.uniform(0.5, 9), 1),
                memory_mb=round(rng.uniform(20, 320), 1),
                num_threads=rng.randint(1, 24),
                parent_sid=root,
                cmdline=[root_name, "--benchmark-test-only"],
                conn_count=rng.randint(0, 12),
            ))

    # ------------------------------------------------------------ system
    sys_ids: list[str] = []
    for i in range(n_sys):
        node_id = nid("sys")
        sys_ids.append(node_id)
        nodes.append(_node(
            node_id, "SYSTEM", f"BENCH SYSTEM {i}",
            name=f"bench-system-{i}", synthetic_group="system",
        ))

    # ------------------------------------------------- external endpoints
    ext_ids: list[str] = []
    for i in range(n_ext):
        node_id = nid("ext")
        ext_ids.append(node_id)
        ip = f"{TESTNET_A}.{rng.randint(1, 254)}" if i % 2 == 0 else f"{TESTNET_B}.{rng.randint(1, 254)}"
        nodes.append(_node(
            node_id, "EXTERNAL_ENDPOINT", ip,
            ip=ip, port=rng.randint(1, 65535),
            country="SYNTH", synthetic_group="endpoint",
        ))

    # ------------------------------------------------- listening ports
    listen_ids: list[str] = []
    for i in range(n_listen):
        node_id = nid("listen")
        listen_ids.append(node_id)
        port = rng.randint(1024, 65000)
        owner = rng.choice(proc_ids)
        nodes.append(_node(
            node_id, "LISTENING_PORT", f":{port}",
            port=port, proc_sid=owner, synthetic_group="endpoint",
        ))

    # --------------------------------------------------- local endpoints
    local_ids: list[str] = []
    for i in range(n_local):
        node_id = nid("local")
        local_ids.append(node_id)
        port = rng.randint(1024, 65000)
        nodes.append(_node(
            node_id, "LOCAL_ENDPOINT", f"127.0.0.1:{port}",
            ip="127.0.0.1", port=port, synthetic_group="endpoint",
        ))

    # --------------------------------------------------- semantic + GPU
    hermes_id = "bm-sem:hermes"
    lmstudio_id = "bm-sem:lmstudio"
    mcp_ids = ["bm-sem:mcp-filesystem", "bm-sem:mcp-github"]
    gpu_id = "bm-gpu:0"
    model_id = "bm-llm:bench-llama"
    nodes.append(_node(hermes_id, "SEMANTIC", "Bench Hermes",
                       semantic_type="HERMES", confidence="CONFIRMED",
                       semantic_name="Bench Hermes", process_ids=[],
                       synthetic_group="semantic"))
    nodes.append(_node(lmstudio_id, "SEMANTIC", "Bench LM Studio",
                       semantic_type="LM_STUDIO", confidence="HIGH",
                       semantic_name="Bench LM Studio", process_ids=[],
                       synthetic_group="semantic"))
    for mid, mname in zip(mcp_ids, ["Bench MCP filesystem", "Bench MCP github"]):
        nodes.append(_node(mid, "SEMANTIC", mname,
                           semantic_type="MCP_SERVER", confidence="MEDIUM",
                           semantic_name=mname, process_ids=[],
                           synthetic_group="semantic"))
    nodes.append(_node(gpu_id, "GPU", "Benchmark GPU 0",
                       name="Benchmark GPU", gpu_index=0,
                       utilization_percent=0, vram_used_mb=0,
                       vram_total_mb=6144, temperature_c=None,
                       synthetic_group="semantic"))
    nodes.append(_node(model_id, "LOCAL_LLM", f"Bench {_MODEL_POOL[0]}",
                       model_id=_MODEL_POOL[0], state="LOADED",
                       semantic_type="MODEL", confidence="HIGH",
                       synthetic_group="semantic"))

    # guarantee the requested size: top up with plain processes until the
    # fixture has at least ``node_count`` nodes (all still TEST-labeled)
    while len(nodes) < node_count:
        pid = SYNTH_PID_BASE + len(proc_ids)
        node_id = nid("proc")
        proc_ids.append(node_id)
        parent = rng.choice(proc_ids[:-1]) if len(proc_ids) > 1 else None
        parent_by_id[node_id] = parent
        nodes.append(_node(
            node_id, "PROCESS", "svchost.exe",
            name="svchost.exe",
            pid=pid,
            exe="C:\\Bench\\svchost.exe",
            username="SYNTHETIC\\bench",
            status="running",
            cpu_percent=round(rng.uniform(0.1, 4), 1),
            memory_mb=round(rng.uniform(3, 60), 1),
            num_threads=rng.randint(2, 16),
            parent_sid=parent,
            cmdline=["svchost.exe", "--benchmark-test-only"],
            conn_count=rng.randint(0, 12),
        ))

    # first process per name (for semantic relationship anchors)
    first_by_name: dict[str, str] = {}
    for n in nodes:
        if n["kind"] == "PROCESS":
            first_by_name.setdefault(n["data"]["name"], n["id"])

    # --------------------------------------------------------------- edges
    e = [0]

    def eid() -> str:
        e[0] += 1
        return f"bm-e-{e[0]:06d}"

    n_proc_final = len(proc_ids)

    # PROCESS_PARENT tree edges (one per non-root process)
    for child, parent in parent_by_id.items():
        if parent is None:
            continue
        edges.append(_edge(eid(), parent, child, "PROCESS_PARENT", active=False, directed=True))
    # LOCALHOST pairs between processes / local endpoints
    for _ in range(round(n_proc_final * 0.5)):
        a, b = rng.sample(proc_ids + local_ids, 2)
        edges.append(_edge(
            eid(), a, b, "LOCALHOST",
            active=rng.random() < 0.18, directed=False,
            ports=[rng.randint(1024, 65000)],
        ))
    # EXTERNAL: process -> external endpoint
    for _ in range(round(n_proc_final * 0.4)):
        src = rng.choice(proc_ids)
        tgt = rng.choice(ext_ids)
        edges.append(_edge(
            eid(), src, tgt, "EXTERNAL",
            active=rng.random() < 0.12, directed=True,
            ports=[rng.randint(1, 65535)],
        ))
    # LISTEN: listening port -> owning process
    for lid in listen_ids:
        owner = next((n["data"].get("proc_sid") for n in nodes if n["id"] == lid), None)
        if owner:
            edges.append(_edge(eid(), lid, owner, "LISTEN", active=False, directed=False))
    # USES_GPU: some processes use the benchmark GPU
    gpu_procs = rng.sample(proc_ids, min(len(proc_ids), max(4, round(n_proc_final * 0.04))))
    for p in gpu_procs:
        edges.append(_edge(eid(), p, gpu_id, "USES_GPU", active=True, directed=True))
    # semantic relationships
    hermes_host = first_by_name.get("python.exe") or first_by_name.get("node.exe") or proc_ids[0]
    for m in mcp_ids:
        edges.append(_edge(eid(), m, hermes_host, "SPAWNED", active=False, directed=True))
    edges.append(_edge(eid(), hermes_id, hermes_host, "HOSTS", active=False, directed=True))
    edges.append(_edge(eid(), lmstudio_id, model_id, "SERVES_MODEL", active=False, directed=True))
    edges.append(_edge(eid(), model_id, hermes_id, "SERVES_MODEL", active=False, directed=True))
    # MEMBER_OF: a semantic-flagged process belongs to the Hermes semantic node
    member = next((p for p in proc_ids if any(
        n["id"] == p and n["data"].get("semantic") for n in nodes
    )), None)
    if member:
        edges.append(_edge(eid(), member, hermes_id, "MEMBER_OF", active=False, directed=True))

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "label": "TEST/BENCHMARK (synthetic)",
            "node_count": len(nodes),
            "edge_count": len(edges),
            "requested": node_count,
            "seed": seed if seed is not None else node_count * 7919,
            "test_only": True,
        },
    }


def validate_graph(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    """Structural validation: unique ids, endpoints exist, labels present."""
    problems: list[str] = []
    ids: set[str] = set()
    for n in nodes:
        if n["id"] in ids:
            problems.append(f"duplicate node id {n['id']}")
        ids.add(n["id"])
        if not n.get("data", {}).get("test_only"):
            problems.append(f"node {n['id']} missing test_only flag")
    eids: set[str] = set()
    for ed in edges:
        if ed["id"] in eids:
            problems.append(f"duplicate edge id {ed['id']}")
        eids.add(ed["id"])
        if ed["source"] not in ids or ed["target"] not in ids:
            problems.append(f"edge {ed['id']} references missing endpoint "
                            f"{ed['source']}->{ed['target']}")
        if not ed.get("test_only"):
            problems.append(f"edge {ed['id']} missing test_only flag")
    return problems


class BenchmarkMode:
    """Explicitly-activated TEST-ONLY benchmark state.

    Defaults to inactive; activation requires ``activate()`` and is always
    OFF on startup. While active, main.py serves the synthetic fixture as the
    WS snapshot (mode ``benchmark``) and suppresses real event/activity/GPU
    messages so synthetic and real data can never mix. This object holds no
    system controls — it only switches which graph data is served.
    """

    def __init__(self) -> None:
        self._active = False
        self._node_count = 0
        self._seed: int | None = None
        self._nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []
        self._activated_at: float | None = None

    @property
    def active(self) -> bool:
        return self._active

    def activate(self, node_count: int, seed: int | None = None, now: float | None = None) -> dict[str, Any]:
        fixture = generate_benchmark_graph(node_count, seed)
        self._active = True
        self._node_count = node_count
        self._seed = fixture["meta"]["seed"]
        self._nodes = fixture["nodes"]
        self._edges = fixture["edges"]
        self._activated_at = now
        return self.status()

    def deactivate(self) -> dict[str, Any]:
        self._active = False
        self._node_count = 0
        self._seed = None
        self._nodes = []
        self._edges = []
        self._activated_at = None
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "node_count": self._node_count,
            "edge_count": len(self._edges),
            "seed": self._seed,
            "label": "TEST/BENCHMARK (synthetic)" if self._active else None,
            "activated_at": self._activated_at,
        }

    def snapshot(self) -> dict[str, Any]:
        """The snapshot payload while active (nodes/edges/meta), else None."""
        if not self._active:
            return None  # type: ignore[return-value]
        return {
            "nodes": self._nodes,
            "edges": self._edges,
            "meta": {
                "label": "TEST/BENCHMARK (synthetic)",
                "node_count": len(self._nodes),
                "edge_count": len(self._edges),
                "seed": self._seed,
            },
        }
