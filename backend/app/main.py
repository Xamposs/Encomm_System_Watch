"""ENCOMM SYSTEM WATCH — FastAPI backend.

Local-first, read-only Windows observability. Binds to 127.0.0.1 only.
Pipeline: COLLECTORS -> NORMALIZED SNAPSHOT -> TOPOLOGY ENGINE -> DIFF ENGINE
          -> EVENT STREAM -> WEBSOCKET -> frontend.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Settings
from .models.entities import Snapshot
from .services.diff_engine import DiffEngine
from .services.event_stream import EventStream
from .services.topology import TopologyEngine

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

_state = {
    "snapshot": None,
    "topology": None,
    "mode": "demo" if cfg.demo_mode else "live",
    "loop_errors": 0,
    "loop_ok": False,
    "last_tick": 0.0,
    "skipped": 0,
}


def _stats_dict() -> dict:
    topo = _state.get("topology")
    if topo is None:
        return {"processes": 0, "active_conns": 0, "listening": 0,
                "cpu_percent": 0.0, "mem_percent": 0.0, "ts": time.time(), "mode": _state["mode"]}
    s = topo.stats
    return {
        "processes": s.processes,
        "active_conns": s.active_conns,
        "listening": s.listening,
        "cpu_percent": s.cpu_percent,
        "mem_percent": s.mem_percent,
        "ts": s.ts,
        "mode": _state["mode"],
    }


def _snapshot_message() -> dict:
    topo = _state.get("topology")
    nodes = [n.to_dict() for n in topo.nodes.values()] if topo else []
    edges = [e.to_dict() for e in topo.edges.values()] if topo else []
    return {
        "type": "snapshot",
        "mode": _state["mode"],
        "ts": _state["last_tick"],
        "stats": _stats_dict(),
        "nodes": nodes,
        "edges": edges,
    }


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
    task = asyncio.create_task(_collect_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="ENCOMM SYSTEM WATCH", version="0.1.0", lifespan=lifespan)
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
                ev = await asyncio.wait_for(queue.get(), timeout=0.5)
                batch = [ev]
                while len(batch) < cfg.max_event_batch:
                    try:
                        batch.append(queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await ws.send_json({"type": "events", "data": batch})
            except asyncio.TimeoutError:
                pass
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
