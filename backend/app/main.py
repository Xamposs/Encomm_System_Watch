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
from .models.entities import Event, Snapshot
from .services.diff_engine import DiffEngine
from .services.event_stream import EventStream
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
    return base


def _snapshot_message() -> dict:
    topo = _state.get("topology")
    nodes = [n.to_dict() for n in topo.nodes.values()] if topo else []
    edges = [e.to_dict() for e in topo.edges.values()] if topo else []
    return {
        "type": "snapshot",
        "mode": _state["mode"],
        "ts": _state["last_tick"],
        "stats": _stats_dict(),
        "telemetry": _capability_dict(),
        "nodes": nodes,
        "edges": edges,
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
                    cap = Capability(
                        level="TIER0", source="NONE",
                        detail="ETW session ended; falling back to socket lifecycle",
                        elevation_required=True,
                    )
                    aggregator.set_capability(cap)
                    _state["telemetry"] = cap.to_dict()
                    log.warning("telemetry provider died — demoted to TIER0")
                    stream.publish([_capability_event(cap, "TEL-DEAD")])
        except Exception:  # noqa: BLE001 — health probing must never kill the app
            log.warning("telemetry health probe failed", exc_info=True)
        await asyncio.sleep(max(0.05, cfg.telemetry_flush_ms / 1000.0))


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
    yield
    task.cancel()
    ttask.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    try:
        await ttask
    except asyncio.CancelledError:
        pass
    if telemetry_provider is not None:
        telemetry_provider.stop()


app = FastAPI(title="ENCOMM SYSTEM WATCH", version="0.2.1", lifespan=lifespan)
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
                if item.get("type") == "network_activity":
                    # already a complete protocol message (activity batch)
                    await ws.send_json(item)
                else:
                    batch = [item]
                    while len(batch) < cfg.max_event_batch:
                        try:
                            nxt = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        if nxt.get("type") == "network_activity":
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
