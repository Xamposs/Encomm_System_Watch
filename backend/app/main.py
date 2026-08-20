"""ENCOMM SYSTEM WATCH — FastAPI backend.

Local-first, read-only Windows observability. Binds to 127.0.0.1 only.
Pipeline: COLLECTORS -> NORMALIZED SNAPSHOT -> TOPOLOGY ENGINE -> DIFF ENGINE
          -> EVENT STREAM -> WEBSOCKET -> frontend.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .collectors.gpu import GpuCollector
from .detectors import SemanticDetectorRegistry
from .models.entities import Event, Snapshot, TEdge
from .services.diff_engine import DiffEngine
from .services.event_stream import EventStream
from .services.benchmark_graph import BenchmarkMode
from .services.etw_health import EtwAttributionHealth
from .services.infra import InfraEngine
from .services.semantic import (
    EVENT_GPU_PROCESS_ATTACHED,
    EVENT_GPU_PROCESS_DETACHED,
    SemanticEngine,
)
from .services.topology import TopologyEngine
from .telemetry import (
    EVENT_TELEMETRY_CAPABILITY_CHANGED,
    ActivityAggregator,
    AdapterTotalsSampler,
    Capability,
    EtwTcpipProvider,
)

log = logging.getLogger("esw")

cfg = Settings.from_env()

if cfg.demo_mode:
    from .collectors.demo import DemoCollector
    _facade = DemoCollector()  # type: ignore[assignment]
else:
    from .collectors.network import NetworkCollector
    from .collectors.processes import ProcessCollector
    from .collectors.system import SystemCollector

    class _LiveFacade:
        def __init__(self) -> None:
            self._procs = ProcessCollector()
            self._net = NetworkCollector()
            self._sys = SystemCollector()

        def collect_processes(self):
            return self._procs.collect()

        def collect_network(self, pid_map):
            return self._net.collect(pid_map)

        def collect_system(self):
            return self._sys.collect()

    _facade = _LiveFacade()

stream = EventStream()
topo_engine = TopologyEngine(cfg)
diff_engine = DiffEngine(cfg)

# ---- GPU + AI semantic observability (v0.3.0) -----------------------------
gpu_collector = GpuCollector() if (cfg.gpu_enabled and not cfg.demo_mode) else None
detector_registry = SemanticDetectorRegistry(cfg)
semantic_engine = SemanticEngine(cfg)

# ---- infrastructure observability (v0.4.0) --------------------------------
# Windows Services + WSL + Docker + VMs. READ-ONLY collectors; every
# platform degrades independently (Docker down, WSL absent, no hypervisor
# -> that section unavailable only). Skipped entirely in demo mode so the
# synthetic data path never mixes with real host discovery.
if not cfg.demo_mode:
    from .collectors.docker import DockerCollector
    from .collectors.services import ServicesCollector
    from .collectors.vm import VmCollector
    from .collectors.wsl import WslCollector

    services_collector = ServicesCollector()
    wsl_collector = WslCollector()
    docker_collector = DockerCollector()
    vm_collector = VmCollector()
else:
    services_collector = None  # type: ignore[assignment]
    wsl_collector = None  # type: ignore[assignment]
    docker_collector = None  # type: ignore[assignment]
    vm_collector = None  # type: ignore[assignment]
infra_engine = InfraEngine(cfg)

# ---- network telemetry ----------------------------------------------------
telemetry_enabled = cfg.telemetry_enabled and not cfg.demo_mode


def _make_telemetry_provider():
    """Select the network activity source.

    ``ESW_TELEMETRY_PROVIDER`` (default ``etw``):
      - ``etw``       real Microsoft-Windows-TCPIP ETW (TIER2 when elevated)
      - ``synthetic`` test-only provider for LOGICAL TIER2 validation
                      (clearly labeled SYNTHETIC; never a real observation)
      - ``off``       disable the provider entirely
    """
    kind = os.environ.get("ESW_TELEMETRY_PROVIDER", "etw").strip().lower()
    if kind == "off":
        return None
    if kind == "synthetic":
        from .telemetry.synthetic import SyntheticActivityProvider

        return SyntheticActivityProvider()
    return EtwTcpipProvider()


aggregator = ActivityAggregator(cfg)
adapter_sampler = AdapterTotalsSampler()
telemetry_provider = _make_telemetry_provider() if telemetry_enabled else None

# ---- TEST-ONLY large-graph benchmark mode (v0.3.1) -------------------------
# Inactive by default; activation is explicit (POST /api/benchmark/activate,
# header-gated). While active the WS snapshot serves the synthetic fixture
# (mode "benchmark") and real event/activity/GPU messages are suppressed so
# synthetic and real data can never mix. No system control paths.
benchmark = BenchmarkMode()

# ---- READ-ONLY ETW attribution health detector (v0.3.1) --------------------
# Observes provider/aggregator counters; reports WATCHING/DEGRADED when
# provider events keep arriving but edge attribution stays frozen. Never
# mutates ETW sessions or processes.
_etw_freeze_s = float(os.environ.get("ESW_ETW_HEALTH_FREEZE_S", "45"))
etw_health = EtwAttributionHealth(freeze_threshold_s=_etw_freeze_s)

_state = {
    "snapshot": None,
    "topology": None,
    "mode": "demo" if cfg.demo_mode else "live",
    "loop_errors": 0,
    "loop_ok": False,
    "last_tick": 0.0,
    "skipped": 0,
    "telemetry": None,   # Capability.to_dict()
    "adapter": None,     # {"down_bps": .., "up_bps": ..} adapter totals
    "gpu": [],           # list of GPU dicts (metrics + PID attribution)
    "gpu_error": None,
    "gpu_source": "NONE",
    "semantic_last_run": 0.0,
    "semantic_done": False,
    "detector_errors": {},
}


def _capability_dict() -> dict:
    cap = _state.get("telemetry")
    if cap is None:
        cap = Capability().to_dict()
    return cap


def _init_telemetry() -> None:
    """Probe the network activity source once at startup.

    Never auto-elevates: when the ETW provider needs administrator rights it
    reports exactly that and SYSTEM WATCH continues on the socket-lifecycle
    tier.
    """
    if telemetry_provider is None:
        cap = Capability(
            level="TIER0", source="DISABLED",
            detail="network telemetry disabled (demo mode or ESW_TELEMETRY_ENABLED=0)",
            enabled=False,
        )
    else:
        telemetry_provider.start()
        cap = telemetry_provider.capability()
        # closed connections must leave the provider's TCB correlation map
        # as soon as the psutil topology sees them go (ETW removal events
        # arrive 10-35 s late and the kernel can recycle the Tcb handle,
        # which would misattribute the next connection's early bytes).
        aggregator.set_tuples_closed_callback(telemetry_provider.drop_tcb_tuples)
    aggregator.set_capability(cap)
    _state["telemetry"] = cap.to_dict()
    if telemetry_provider is not None:
        stream.publish([_capability_event(cap, "TEL-INIT")])


def _capability_event(cap: Capability, event_id: str) -> dict:
    return Event(
        event_id=event_id,
        event_type=EVENT_TELEMETRY_CAPABILITY_CHANGED,
        source="telemetry", target=None,
        timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        metadata=cap.to_dict(),
    ).to_dict()


def _stats_dict() -> dict:
    cap = _capability_dict()
    base = {
        "processes": 0, "active_conns": 0, "listening": 0,
        "cpu_percent": 0.0, "mem_percent": 0.0, "ts": time.time(),
        "mode": "benchmark" if benchmark.active else _state["mode"],
        "telemetry": cap,
    }
    topo = _state.get("topology")
    if topo is not None:
        s = topo.stats
        base.update({
            "processes": s.processes,
            "active_conns": s.active_conns,
            "listening": s.listening,
            "cpu_percent": s.cpu_percent,
            "mem_percent": s.mem_percent,
            "ts": s.ts,
        })
    # NETWORK: captured telemetry when Tier 2 is active, otherwise adapter
    # totals. The two are different measurements; both are reported and the
    # UI labels the source explicitly. Never fake zeroes when unavailable.
    adapter = _state.get("adapter")
    net = None
    if cap.get("level") == "TIER2":
        totals = aggregator.totals()
        net = {
            "down_bps": totals["down_bps"],
            "up_bps": totals["up_bps"],
            "source": "CAPTURED",
            "adapter_down_bps": round(adapter["down_bps"], 1) if adapter else 0.0,
            "adapter_up_bps": round(adapter["up_bps"], 1) if adapter else 0.0,
        }
    elif adapter is not None:
        net = {
            "down_bps": adapter["down_bps"],
            "up_bps": adapter["up_bps"],
            "source": "ADAPTER_TOTALS",
            "adapter_down_bps": adapter["down_bps"],
            "adapter_up_bps": adapter["up_bps"],
        }
    base["net"] = net
    base["gpu"] = _state.get("gpu", [])
    base["semantic"] = semantic_engine.summary()
    base["infra"] = infra_engine.summary()
    return base


def _snapshot_message() -> dict:
    # TEST-ONLY benchmark mode: serve the synthetic fixture, never real
    # topology, while active (every element is flagged test_only/benchmark).
    if benchmark.active:
        bm = benchmark.snapshot()
        return {
            "type": "snapshot",
            "mode": "benchmark",
            "ts": _state["last_tick"],
            "stats": _stats_dict(),
            "telemetry": _capability_dict(),
            "nodes": bm["nodes"],
            "edges": bm["edges"],
            "gpu": [],
            "semantic": {},
            "benchmark": {
                "active": True,
                "label": bm["meta"]["label"],
                "node_count": bm["meta"]["node_count"],
                "edge_count": bm["meta"]["edge_count"],
                "seed": bm["meta"]["seed"],
            },
        }
    topo = _state.get("topology")
    if topo is not None:
        tnodes: list[TNode] = list(topo.nodes.values())
        tnodes = semantic_engine.augment_process_nodes(tnodes)
        tnodes = infra_engine.augment_process_nodes(tnodes)
        nodes = [n.to_dict() for n in tnodes]
        nodes += [n.to_dict() for n in semantic_engine.semantic_nodes()]
        nodes += [n.to_dict() for n in infra_engine.nodes()]
        edges = [e.to_dict() for e in topo.edges.values()]
        edges += [e.to_dict() for e in semantic_engine.semantic_edges()]
        edges += [e.to_dict() for e in infra_engine.edges()]
    else:
        nodes, edges = [], []
    return {
        "type": "snapshot",
        "mode": _state["mode"],
        "ts": _state["last_tick"],
        "stats": _stats_dict(),
        "telemetry": _capability_dict(),
        "nodes": nodes,
        "edges": edges,
        "gpu": _state.get("gpu", []),
        "semantic": semantic_engine.summary(),
        "infra": infra_engine.summary(),
    }


def _telemetry_tick() -> tuple[list, list, list]:
    """One telemetry window: drain provider -> batch-ingest -> flush.

    This is the v0.2.1 wiring fix extracted as a unit: the provider's
    bounded queue is drained and fed into the aggregator BEFORE the
    ~200 ms window is flushed. Returns (items, burst_events, node_items).
    Telemetry failures never propagate (the caller keeps the loop alive).
    """
    if telemetry_provider is not None:
        events = telemetry_provider.drain()
        if events:
            aggregator.record_many(events)
        # keep /api/telemetry truthful as readiness transitions
        # (INITIALIZING -> ACTIVE once real data events are observed)
        try:
            cap = telemetry_provider.capability()
            if cap.to_dict() != _state["telemetry"]:
                _state["telemetry"] = cap.to_dict()
                aggregator.set_capability(cap)
        except Exception:  # noqa: BLE001 — capability refresh must never kill
            pass
    return aggregator.flush()


async def _telemetry_loop() -> None:
    """Flush aggregated network activity in small batches (~5 msg/s max).

    The loop is the ONLY place where the provider is drained into the
    aggregator: each window it takes every buffered ETW event
    (``provider.drain()``), batch-ingests them (``aggregator.record_many``,
    a single lock acquisition), then flushes the ~200 ms window. Raw
    per-packet events never reach the WebSocket; only compact per-edge
    batches do. Also watches provider health and demotes the capability
    tier truthfully if the ETW session dies.
    """
    while True:
        try:
            items, bursts, node_items = _telemetry_tick()
        except Exception:  # noqa: BLE001 — telemetry must never kill the app
            items, bursts, node_items = [], [], []
        if items or node_items:
            if not benchmark.active:
                stream.publish_message({
                    "type": "network_activity",
                    "window_ms": cfg.telemetry_flush_ms,
                    "ts": time.time(),
                    "items": items,
                    "nodes": node_items,
                })
        if bursts and not benchmark.active:
            stream.publish([e.to_dict() for e in bursts])
        # ETW attribution health: READ-ONLY detector. While the provider
        # keeps receiving events but edge attribution stays frozen for an
        # extended period, surface a truthful ETW ATTRIBUTION DEGRADED
        # warning (one WS event per transition). No automatic action.
        try:
            prov_health = {}
            if telemetry_provider is not None:
                prov_health = telemetry_provider.counters()
                prov_health["alive"] = telemetry_provider.alive()
            agg_health = aggregator.counters()
            topo = _state.get("topology")
            etw_health.sample(
                prov_health,
                agg_health,
                edges_tracked=len(aggregator.edge_states()),
                active_conns=topo.stats.active_conns if topo is not None else 0,
                now=time.time(),
            )
            transition = etw_health.consume_transition()
            if transition is not None:
                # surface only meaningful transitions (warnings + recovery),
                # not the quiet startup OK baseline
                prev = transition.get("previous_state")
                cur = transition["state"]
                if cur in ("WATCHING", "DEGRADED", "PROVIDER_DEAD") or \
                   prev in ("WATCHING", "DEGRADED", "PROVIDER_DEAD"):
                    stream.publish([_etw_health_event(transition)])
        except Exception:  # noqa: BLE001 — health detection must never kill the app
            log.warning("etw health sample failed", exc_info=True)
        # provider health probe — must never kill the loop either
        try:
            if telemetry_provider is not None and _capability_dict().get("level") == "TIER2":
                if not telemetry_provider.alive():
                    telemetry_provider.mark_degraded()
                    cap = telemetry_provider.capability()
                    aggregator.set_capability(cap)
                    _state["telemetry"] = cap.to_dict()
                    log.warning("telemetry provider died — demoted to TIER0")
                    stream.publish([_capability_event(cap, "TEL-DEAD")])
        except Exception:  # noqa: BLE001 — health probing must never kill the app
            log.warning("telemetry health probe failed", exc_info=True)
        await asyncio.sleep(max(0.05, cfg.telemetry_flush_ms / 1000.0))


_gpu_event_seq = [0]


def _next_event_id() -> str:
    _gpu_event_seq[0] += 1
    return f"{_gpu_event_seq[0]:06d}-{int(time.time() * 1000)}"


def _gpu_pid_event(pid: int, gpus: list[dict], attached: bool) -> dict:
    """GPU_PROCESS_ATTACHED / GPU_PROCESS_DETACHED (change-only, truthful)."""
    snap = _state.get("snapshot")
    sid = None
    name = ""
    if snap is not None:
        for s, p in snap.processes.items():
            if p.pid == pid:
                sid, name = s, p.name
                break
    gpu_index = 0
    vram_mb = None
    for g in gpus:
        for proc in g.get("processes", []):
            if int(proc["pid"]) == pid:
                gpu_index = g.get("index", 0)
                vram_mb = proc.get("vram_mb")
    edge = None
    edge_id = None
    if attached and sid is not None:
        edge_id = f"se:{sid}->gpu:{gpu_index}:USES_GPU"
        edge = TEdge(
            id=edge_id, source=sid, target=f"gpu:{gpu_index}", kind="USES_GPU",
            proto="sem", ports=[], active=True, directed=True,
        ).to_dict()
    return Event(
        event_id=_next_event_id(),
        event_type=EVENT_GPU_PROCESS_ATTACHED if attached else EVENT_GPU_PROCESS_DETACHED,
        source=f"gpu:{gpu_index}",
        target=sid,
        timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        metadata={
            "pid": pid,
            "name": name,
            "sid": sid,
            "gpu_index": gpu_index,
            "vram_mb": vram_mb,
            "edge": edge,
            "edge_id": edge_id,
        },
    ).to_dict()


def _etw_health_event(state: dict) -> dict:
    """One WS event per ETW health transition (READ-ONLY warning)."""
    return Event(
        event_id=_next_event_id(),
        event_type="ETW_HEALTH",
        source="telemetry",
        target=None,
        timestamp=datetime.now().astimezone().isoformat(timespec="milliseconds"),
        metadata=state,
    ).to_dict()


async def _gpu_loop() -> None:
    """GPU metrics ~1 s, PID attribution ~2 s; never kills the app.

    Publishes a compact ``gpu`` WS message each tick so the frontend keeps
    GPU node metrics live, and change-only GPU_PROCESS_ATTACHED/DETACHED
    events when NVML's per-process list changes.
    """
    if gpu_collector is None:
        return
    last_pid_sample = 0.0
    prev_gpus: list[dict] = []
    while True:
        try:
            now = time.time()
            with_pids = now - last_pid_sample >= cfg.gpu_pid_interval_s
            gpus = await asyncio.to_thread(gpu_collector.sample, with_pids)
            if with_pids:
                last_pid_sample = now
            else:
                # metrics-only tick: carry the last PID attribution over so
                # the live stream never flickers processes in/out
                for g in gpus:
                    if "processes" in g:
                        continue
                    prev = next((x for x in prev_gpus if x.get("index") == g.get("index")), None)
                    if prev is not None:
                        g["processes"] = prev.get("processes", [])
            if gpus:
                attached, detached = gpu_collector.changed_pids(gpus)
                batch = []
                for pid in sorted(attached):
                    batch.append(_gpu_pid_event(pid, gpus, attached=True))
                for pid in sorted(detached):
                    batch.append(_gpu_pid_event(pid, gpus, attached=False))
                # benchmark mode: real GPU telemetry must not mix into the
                # synthetic graph — keep collecting, stop forwarding
                if batch and not benchmark.active:
                    stream.publish(batch)
            _state["gpu"] = gpus
            prev_gpus = gpus
            _state["gpu_source"] = gpu_collector.source
            _state["gpu_error"] = gpu_collector.error
            if not benchmark.active:
                stream.publish_message({"type": "gpu", "data": gpus, "ts": time.time()})
        except Exception:  # noqa: BLE001 — GPU failure must never kill the app
            log.warning("gpu loop tick failed", exc_info=True)
        await asyncio.sleep(cfg.gpu_metrics_interval_s)


async def _infra_loop() -> None:
    """Windows Services / WSL / Docker / VM polling (v0.4.0).

    Each collector runs on its own interval (staggered), all inside one
    task so the main 1 s collect loop is never touched. Every collector is
    failure-isolated: a broken platform degrades that section only.
    Change-only events are emitted by the InfraEngine (first sample is the
    baseline — no startup storm). Benchmark mode suppresses forwarding so
    synthetic and real data never mix.
    """
    if infra_engine is None or services_collector is None:
        return
    from .collectors.docker import DockerState
    from .collectors.vm import VmState
    from .collectors.wsl import WslState

    # last-good state holders: a transient collector failure must NEVER wipe
    # the graph to "not installed / engine UNKNOWN" — a stale-but-truthful
    # snapshot (error text set) beats a false empty one.
    last_wsl = WslState()
    last_docker = DockerState()
    last_vm = VmState()
    last_services: list = []
    last = {"services": 0.0, "wsl": 0.0, "docker": 0.0, "vm": 0.0}
    while True:
        try:
            now = time.time()
            snap = _state.get("snapshot")
            topo = _state.get("topology")
            gpu_state = _state.get("gpu", [])
            services: list = []
            wsl_state = None
            docker_state = None
            vm_state = None

            if snap is not None and topo is not None and now - last["services"] >= cfg.infra_services_interval_s:
                last["services"] = now
                try:
                    collected, _ = await asyncio.to_thread(services_collector.collect)
                    if collected:
                        services = collected
                        last_services = collected
                    else:
                        # an empty enumeration is NOT a legit services state
                        # (Windows always has services) — keep last good
                        services = last_services
                except Exception:  # noqa: BLE001 — services must never kill
                    services = last_services
            else:
                services = last_services
            if now - last["wsl"] >= cfg.infra_wsl_interval_s:
                last["wsl"] = now
                try:
                    wsl_state = await asyncio.to_thread(wsl_collector.collect)
                except Exception:  # noqa: BLE001 — WSL must never kill the app
                    wsl_state = None
            if now - last["docker"] >= cfg.infra_docker_interval_s:
                last["docker"] = now
                try:
                    docker_state = await asyncio.to_thread(docker_collector.collect)
                except Exception:  # noqa: BLE001 — Docker must never kill the app
                    docker_state = None
            if now - last["vm"] >= cfg.infra_vm_interval_s:
                last["vm"] = now
                try:
                    vm_state = await asyncio.to_thread(vm_collector.collect)
                except Exception:  # noqa: BLE001 — VM detection must never kill
                    vm_state = None

            if wsl_state is None or (wsl_state.error and not wsl_state.installed):
                # thrown OR returned a failure signature (empty enumeration +
                # error) — never let a transient WSL-service hiccup wipe the
                # graph to "not installed"; keep the last good observation
                last_wsl.error = wsl_state.error if wsl_state else (
                    "wsl collector failed — showing last good state")
                wsl_state = last_wsl
            else:
                last_wsl = wsl_state
            if docker_state is None:
                last_docker.error = "docker collector failed — showing last good state"
                docker_state = last_docker
            else:
                last_docker = docker_state
            if vm_state is None:
                last_vm.error = "vm collector failed — showing last good state"
                vm_state = last_vm
            else:
                last_vm = vm_state

            events = infra_engine.update(
                services, wsl_state, docker_state, vm_state,
                snap, topo, gpu_state,
            ) if (snap is not None and topo is not None) else []
            if events and not benchmark.active:
                stream.publish([e.to_dict() for e in events])
        except Exception:  # noqa: BLE001 — infra must never kill the app
            log.warning("infra loop tick failed", exc_info=True)
        await asyncio.sleep(1.0)


async def _collect_loop() -> None:
    consecutive_errors = 0
    while True:
        tick_start = time.time()
        try:
            procs, pid_map = await asyncio.to_thread(_facade.collect_processes)
            conns, owner_map = await asyncio.to_thread(_facade.collect_network, pid_map)
            system = await asyncio.to_thread(_facade.collect_system)
            snap = Snapshot(ts=time.time(), processes=procs, connections=conns,
                            owner_map=owner_map, system=system)
            topo = topo_engine.build(snap)
            events = diff_engine.diff(snap, topo)
            aggregator.set_topology(snap, topo)
            # ---- semantic detection (GPU + AI) -----------------------------
            # Runs on the detector cadence (or on topology change); the
            # registry is failure-isolated per detector.
            now = time.time()
            if now - _state["semantic_last_run"] >= cfg.detector_interval_s or not _state["semantic_done"]:
                gpu_state = _state.get("gpu", [])
                detections, rels = await asyncio.to_thread(
                    detector_registry.run_all, snap, topo, gpu_state
                )
                sem_events = semantic_engine.update(detections, rels, gpu_state, snap, topo)
                _state["semantic_last_run"] = now
                _state["semantic_done"] = True
                _state["detector_errors"] = detector_registry.errors
                if sem_events and not benchmark.active:
                    stream.publish([e.to_dict() for e in sem_events])
            if not cfg.demo_mode:
                adapter = adapter_sampler.sample()
                if adapter is not None:
                    _state["adapter"] = {
                        "down_bps": round(adapter[0], 1),
                        "up_bps": round(adapter[1], 1),
                    }
            _state["snapshot"] = snap
            _state["topology"] = topo
            _state["last_tick"] = snap.ts
            _state["loop_ok"] = True
            _state["skipped"] = getattr(_facade, "skipped", 0)
            consecutive_errors = 0
            # benchmark mode: real events must never mix into the synthetic
            # graph — keep building real state, stop forwarding events
            if events and not benchmark.active:
                stream.publish([e.to_dict() for e in events])
        except Exception:  # noqa: BLE001 — the monitoring loop must never die
            consecutive_errors += 1
            _state["loop_errors"] += 1
            if consecutive_errors == 1 or consecutive_errors % 10 == 0:
                log.exception("collector tick failed (%s consecutive)", consecutive_errors)
        elapsed = time.time() - tick_start
        await asyncio.sleep(max(0.05, cfg.poll_interval - elapsed))


@asynccontextmanager
async def lifespan(_: FastAPI):
    _init_telemetry()
    task = asyncio.create_task(_collect_loop())
    ttask = asyncio.create_task(_telemetry_loop())
    gtask = asyncio.create_task(_gpu_loop())
    itask = asyncio.create_task(_infra_loop())
    yield
    task.cancel()
    ttask.cancel()
    gtask.cancel()
    itask.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await ttask
    except asyncio.CancelledError:
        pass
    try:
        await gtask
    except asyncio.CancelledError:
        pass
    try:
        await itask
    except asyncio.CancelledError:
        pass
    if telemetry_provider is not None:
        telemetry_provider.stop()


app = FastAPI(title="ENCOMM SYSTEM WATCH", version="0.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173", "http://localhost:5173",
        "http://127.0.0.1:8765", "http://localhost:8765",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    status: str
    mode: str
    loop_ok: bool
    loop_errors: int
    last_tick_age_s: float
    skipped: int


class StateResponse(BaseModel):
    mode: str
    ts: float
    stats: dict
    nodes: list
    edges: list


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        mode=_state["mode"],
        loop_ok=_state["loop_ok"],
        loop_errors=_state["loop_errors"],
        last_tick_age_s=round(time.time() - _state["last_tick"], 2),
        skipped=_state["skipped"],
    )


@app.get("/api/state", response_model=StateResponse)
async def api_state() -> StateResponse:
    msg = _snapshot_message()
    return StateResponse(mode=msg["mode"], ts=msg["ts"], stats=msg["stats"],
                         nodes=msg["nodes"], edges=msg["edges"])


@app.get("/api/telemetry")
async def api_telemetry() -> dict:
    """Current network telemetry capability (honest: what actually works)."""
    return _capability_dict()


@app.get("/api/telemetry/debug")
async def api_telemetry_debug() -> dict:
    """Read-only telemetry diagnostics: counters along the whole chain.

    Localhost-only like the rest of the backend. Exposes counts, never
    payloads: how many events the provider received/drained/dropped, how
    many the aggregator recorded/mapped, queue depth, and the last
    non-empty batch's directional byte totals.
    """
    prov: dict = {}
    if telemetry_provider is not None:
        prov = telemetry_provider.counters()
        prov["queue_depth"] = telemetry_provider.queue_depth()
        prov["alive"] = telemetry_provider.alive()
    agg = aggregator.counters()
    return {
        "telemetry": _capability_dict(),
        "provider": prov,
        "aggregator": agg,
        "edges_tracked": len(aggregator.edge_states()),
        "processes_tracked": len(aggregator.process_states()),
        "health": etw_health.state_dict(time.time()),
    }


# ---- TEST-ONLY benchmark mode endpoints (v0.3.1) ----------------------------
# Explicitly activated, header-gated, read-only w.r.t. the system: they only
# switch which graph data the WS snapshot serves. No control paths.


class BenchmarkActivateRequest(BaseModel):
    nodes: int = 500
    seed: int | None = None


@app.get("/api/benchmark/status")
async def api_benchmark_status() -> dict:
    """TEST-ONLY benchmark state (always readable, inactive by default)."""
    return benchmark.status()


@app.post("/api/benchmark/activate")
async def api_benchmark_activate(
    req: BenchmarkActivateRequest,
    x_esw_benchmark: str | None = Header(default=None),
) -> dict:
    """Activate TEST-ONLY benchmark mode (header `X-ESW-Benchmark: test-only`).

    Serves the deterministic synthetic fixture as the WS snapshot until
    deactivated; real event/activity/GPU messages are suppressed for the
    duration. Synthetic data is always labeled — it can never be mistaken
    for real telemetry.
    """
    if (x_esw_benchmark or "").strip().lower() != "test-only":
        return {"error": "benchmark activation requires header X-ESW-Benchmark: test-only"}
    try:
        status = benchmark.activate(req.nodes, req.seed, now=time.time())
    except ValueError as exc:
        return {"error": str(exc)}
    return status


@app.post("/api/benchmark/deactivate")
async def api_benchmark_deactivate() -> dict:
    """Deactivate TEST-ONLY benchmark mode; the next snapshot is real again."""
    return benchmark.deactivate()


@app.get("/api/gpu")
async def api_gpu() -> dict:
    """Current GPU state (metrics + PID attribution), honest source label."""
    return {
        "source": _state.get("gpu_source", "NONE"),
        "error": _state.get("gpu_error"),
        "gpus": _state.get("gpu", []),
    }


@app.get("/api/semantic")
async def api_semantic() -> dict:
    """Current semantic detections + relationships (read-only)."""
    return {
        "detections": semantic_engine.detections_dict(),
        "relationships": semantic_engine.relationships_dict(),
        "summary": semantic_engine.summary(),
        "errors": _state.get("detector_errors", {}),
    }


@app.get("/api/infra")
async def api_infra() -> dict:
    """Current infrastructure state (v0.4.0) — services/WSL/Docker/VMs.

    READ-ONLY: observation data only, no control paths. Platforms that are
    unavailable (engine down, no WSL, no hypervisor) report exactly that —
    absent platforms are never invented, and no software is ever started to
    produce a positive result.
    """
    return infra_engine.state_dict()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue = stream.subscribe()
    try:
        # wait for the first snapshot so clients never connect to an empty map
        for _ in range(200):
            if _state["topology"] is not None:
                break
            await asyncio.sleep(0.05)
        await ws.send_json(_snapshot_message())
        last_stats = 0.0
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                item = None
            if item is not None:
                if item.get("type") in ("network_activity", "gpu"):
                    # already a complete protocol message (activity batch / gpu state)
                    await ws.send_json(item)
                else:
                    batch = [item]
                    while len(batch) < cfg.max_event_batch:
                        try:
                            nxt = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if nxt.get("type") in ("network_activity", "gpu"):
                            await ws.send_json(nxt)
                            continue
                        batch.append(nxt)
                    await ws.send_json({"type": "events", "data": batch})
            now = time.time()
            if now - last_stats >= 2.0:
                await ws.send_json({"type": "stats", "data": _stats_dict()})
                last_stats = now
            try:
                await ws.send_json({"type": "ping", "ts": now})
            except Exception:
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        pass
    finally:
        stream.unsubscribe(queue)


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
