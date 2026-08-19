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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .collectors.gpu import GpuCollector
from .detectors import SemanticDetectorRegistry
from .models.entities import Event, Snapshot, TEdge
from .services.diff_engine import DiffEngine
from .services.event_stream import EventStream
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
        "mode": _state["mode"],
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
    return base


def _snapshot_message() -> dict:
    topo = _state.get("topology")
    if topo is not None:
        nodes = [n.to_dict() for n in semantic_engine.augment_process_nodes(list(topo.nodes.values()))]
        nodes += [n.to_dict() for n in semantic_engine.semantic_nodes()]
        edges = [e.to_dict() for e in topo.edges.values()]
        edges += [e.to_dict() for e in semantic_engine.semantic_edges()]
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
            stream.publish_message({
                "type": "network_activity",
                "window_ms": cfg.telemetry_flush_ms,
                "ts": time.time(),
                "items": items,
                "nodes": node_items,
            })
        if bursts:
            stream.publish([e.to_dict() for e in bursts])
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
                if batch:
                    stream.publish(batch)
            _state["gpu"] = gpus
            prev_gpus = gpus
            _state["gpu_source"] = gpu_collector.source
            _state["gpu_error"] = gpu_collector.error
            stream.publish_message({"type": "gpu", "data": gpus, "ts": time.time()})
        except Exception:  # noqa: BLE001 — GPU failure must never kill the app
            log.warning("gpu loop tick failed", exc_info=True)
        await asyncio.sleep(cfg.gpu_metrics_interval_s)


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
                if sem_events:
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
            if events:
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
    yield
    task.cancel()
    ttask.cancel()
    gtask.cancel()
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
    if telemetry_provider is not None:
        telemetry_provider.stop()


app = FastAPI(title="ENCOMM SYSTEM WATCH", version="0.3.0", lifespan=lifespan)
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
    }


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
