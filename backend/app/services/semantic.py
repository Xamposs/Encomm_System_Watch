"""Semantic engine: detections + GPU state -> semantic nodes, edges, events.

Pipeline stage between the detector registry and the WebSocket:

    WINDOWS RAW STATE -> TOPOLOGY -> DETECTOR REGISTRY
        -> EVIDENCE + CONFIDENCE -> SEMANTIC RESOURCE NODES
        -> RELATIONSHIPS -> WEBSOCKET -> SYSTEM / AI VIEW

The engine is purely additive: it never mutates the raw topology. Semantic
nodes carry their own ids (``sem:*``, ``gpu:*``) and semantic edges their
own ids (``se:*``), so SYSTEM WATCH's raw process truth stays intact and
duplicates are impossible (the snapshot is a full replace).

Events are emitted ONLY on state changes (detected/lost, model state
flip, GPU process attach/detach) — repeated unchanged detections never
spam the event drawer.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Optional

from ..config import Settings
from ..detectors.base import Detection, SemanticRelationship
from ..models.entities import Event, Snapshot, TEdge, TNode, TopologyResult

EVENT_HERMES_DETECTED = "HERMES_DETECTED"
EVENT_LM_STUDIO_DETECTED = "LM_STUDIO_DETECTED"
EVENT_MCP_SERVER_DETECTED = "MCP_SERVER_DETECTED"
EVENT_MODEL_LOADED = "MODEL_LOADED"
EVENT_MODEL_AVAILABLE = "MODEL_AVAILABLE"
EVENT_SEMANTIC_LOST = "SEMANTIC_LOST"
EVENT_GPU_PROCESS_ATTACHED = "GPU_PROCESS_ATTACHED"
EVENT_GPU_PROCESS_DETACHED = "GPU_PROCESS_DETACHED"

_EDGE_KINDS = {"USES_GPU", "SERVES_MODEL", "LOCAL_API", "PROCESS_PARENT",
               "SPAWNED", "HOSTS", "MEMBER_OF"}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _gpu_label(g: dict[str, Any]) -> str:
    idx = g.get("index", 0)
    name = str(g.get("name", "GPU"))
    parts = [f"GPU {idx}", name]
    util = g.get("utilization_percent")
    used = g.get("vram_used_mb")
    total = g.get("vram_total_mb")
    if util is not None:
        if used is not None and total is not None:
            parts.append(f"{int(util)}% · {round(used / 1024, 1)}/{round(total / 1024, 1)} GB")
        else:
            parts.append(f"{int(util)}%")
    return "\n".join(parts)


class SemanticEngine:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._detections: dict[str, Detection] = {}
        self._relationships: list[SemanticRelationship] = []
        self._gpu: list[dict[str, Any]] = []
        self._prev_gpu_pids: dict[int, set[int]] = {}
        self._prev_model_states: dict[str, str] = {}
        self._pid_sid: dict[int, str] = {}
        self._procs: dict[str, Any] = {}
        self._seq = 0

    # ------------------------------------------------------------- state api

    def semantic_nodes(self) -> list[TNode]:
        nodes: list[TNode] = []
        for det in self._detections.values():
            nodes.append(self._det_node(det))
        for g in self._gpu:
            nodes.append(self._gpu_node(g))
        return nodes

    def semantic_edges(self) -> list[TEdge]:
        edges: list[TEdge] = []
        for rel in self._relationships:
            if rel.kind not in _EDGE_KINDS:
                continue
            src, tgt = rel.source, rel.target
            if not rel.directed and src > tgt:
                src, tgt = tgt, src
            edges.append(TEdge(
                id=rel.id, source=src, target=tgt, kind=rel.kind,
                proto="sem", ports=[], active=True, directed=rel.directed,
            ))
        # USES_GPU edges from live GPU PID attribution (resolved via the
        # pid->sid map captured during update(); unknown pids get no edge)
        for g in self._gpu:
            for p in g.get("processes", []):
                sid = self._pid_sid.get(int(p["pid"]))
                if sid:
                    edges.append(TEdge(
                        id=f"se:{sid}->gpu:{g['index']}:USES_GPU",
                        source=sid, target=f"gpu:{g['index']}", kind="USES_GPU",
                        proto="sem", ports=[], active=True, directed=True,
                    ))
        return edges

    def augment_process_nodes(self, nodes: list[TNode]) -> list[TNode]:
        """Mark underlying process nodes with their semantic identity so the
        AI view is driven by classification, not frontend string search."""
        for n in nodes:
            if n.kind != "PROCESS":
                continue
            for det in self._detections.values():
                if n.id in det.process_ids:
                    n.data["semantic"] = {
                        "semantic_type": det.semantic_type,
                        "semantic_name": det.semantic_name,
                        "confidence": det.confidence,
                    }
                    break
        return nodes

    def summary(self) -> dict[str, Any]:
        hermes = lm = False
        models: list[dict[str, Any]] = []
        mcp: list[str] = []
        for det in self._detections.values():
            if det.semantic_type == "HERMES":
                hermes = True
            elif det.semantic_type == "LM_STUDIO":
                lm = True
            elif det.semantic_type == "MCP_SERVER":
                mcp.append(det.semantic_name)
            elif det.semantic_type == "LOCAL_LLM":
                models.append({
                    "id": det.semantic_name,
                    "state": det.metadata.get("state", "AVAILABLE"),
                })
        gpu_summary = [
            {
                "index": g.get("index", 0),
                "name": g.get("name"),
                "utilization_percent": g.get("utilization_percent"),
                "vram_used_mb": g.get("vram_used_mb"),
                "vram_total_mb": g.get("vram_total_mb"),
                "temperature_c": g.get("temperature_c"),
            }
            for g in self._gpu
        ]
        return {
            "hermes": hermes,
            "lm_studio": lm,
            "models": models,
            "mcp": mcp,
            "gpu": gpu_summary,
        }

    def detections_dict(self) -> list[dict[str, Any]]:
        return [d.to_dict() for d in self._detections.values()]

    def relationships_dict(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._relationships]

    # -------------------------------------------------------------- pipeline

    def update(
        self,
        detections: list[Detection],
        relationships: list[SemanticRelationship],
        gpu: list[dict[str, Any]],
        snap: Snapshot,
        topo: TopologyResult,
    ) -> list[Event]:
        """Adopt the new state, diff against the previous one, emit events.

        Called on the detector cadence (a few seconds) and after every GPU
        refresh. Events are change-only.
        """
        self._pid_sid = {p.pid: sid for sid, p in snap.processes.items() if p.pid is not None}
        self._procs = snap.processes
        cur: dict[str, Detection] = {d.key(): d for d in detections}
        events: list[Event] = []

        # --- detection lifecycle -------------------------------------------
        for key, det in cur.items():
            if key not in self._detections and det.semantic_type != "LOCAL_LLM":
                # LOCAL_LLM appearances are announced by the model event
                # (MODEL_AVAILABLE / MODEL_LOADED) — never double-announced
                events.append(self._detection_event(det, relationships))
        for key, det in self._detections.items():
            if key not in cur:
                events.append(Event(
                    event_id=self._next_id(),
                    event_type=EVENT_SEMANTIC_LOST,
                    source=det.node_id,
                    target=None,
                    timestamp=_now_iso(),
                    metadata={
                        "semantic_type": det.semantic_type,
                        "semantic_name": det.semantic_name,
                        "node_id": det.node_id,
                    },
                ))

        # --- model state flips (LOADED <-> AVAILABLE only) ------------------
        cur_models: dict[str, str] = {}
        for det in cur.values():
            if det.semantic_type != "LOCAL_LLM":
                continue
            cur_models[det.node_id] = det.metadata.get("state", "AVAILABLE")
        for node_id, state in cur_models.items():
            prev = self._prev_model_states.get(node_id)
            if state == "LOADED" and prev != "LOADED":
                events.append(self._model_event(node_id, state, detections, relationships))
            elif state == "AVAILABLE" and prev == "LOADED":
                events.append(self._model_event(node_id, state, detections, relationships))
            elif prev is None and state == "AVAILABLE":
                events.append(self._model_event(node_id, state, detections, relationships))
        self._prev_model_states = cur_models

        # --- GPU attach/detach events are owned by the GPU loop ---------------
        # (main.py publishes them at the GPU PID cadence; the engine would
        # only re-emit the same change on its slower detector cadence)

        self._detections = cur
        self._relationships = relationships
        self._gpu = gpu
        return events

    # ------------------------------------------------------------ node/edges

    @staticmethod
    def _det_node(det: Detection) -> TNode:
        kind = "SEMANTIC" if det.semantic_type != "LOCAL_LLM" else "LOCAL_LLM"
        data: dict[str, Any] = {
            "semantic_type": det.semantic_type,
            "semantic_name": det.semantic_name,
            "confidence": det.confidence,
            "evidence": [e.to_dict() for e in det.evidence],
            "pids": det.metadata.get("pids", []),
            "process_ids": det.process_ids,
            "state": det.metadata.get("state", "RUNNING"),
        }
        for k in ("endpoint", "transport", "api_available", "models", "desktop_main_sid",
                  "gateway_sids"):
            if k in det.metadata:
                data[k] = det.metadata[k]
        label = SemanticEngine._det_label(det, data)
        return TNode(id=det.node_id, kind=kind, label=label, data=data)

    @staticmethod
    def _det_label(det: Detection, data: dict[str, Any]) -> str:
        if det.semantic_type == "HERMES":
            pids = data.get("pids", [])
            pid = f" · PID {pids[0]}" if pids else ""
            return f"◈ HERMES\n● RUNNING{pid}"
        if det.semantic_type == "LM_STUDIO":
            ep = data.get("endpoint")
            ep = ep.replace("http://", "") if isinstance(ep, str) else "api"
            return f"◉ LM STUDIO\n{ep}"
        if det.semantic_type == "MCP_SERVER":
            return f"MCP SERVER\n{det.semantic_name}"
        if det.semantic_type == "LOCAL_LLM":
            return f"LOCAL LLM\n{det.semantic_name}"
        return f"{det.semantic_name}\n{det.confidence}"

    @staticmethod
    def _gpu_node(g: dict[str, Any]) -> TNode:
        idx = g.get("index", 0)
        data: dict[str, Any] = {"gpu_index": idx, "semantic_type": "GPU"}
        for k in ("name", "utilization_percent", "vram_used_mb", "vram_total_mb",
                  "temperature_c", "power_w", "driver", "fan_percent",
                  "clock_graphics_mhz", "clock_memory_mhz"):
            if k in g:
                data[k] = g[k]
        data["processes"] = g.get("processes", [])
        return TNode(id=f"gpu:{idx}", kind="GPU", label=_gpu_label(g), data=data)

    # ---------------------------------------------------------------- events

    def _next_id(self) -> str:
        self._seq += 1
        return f"{self._seq:06d}-{int(time.time() * 1000)}"

    def _node_edges(self, node_id: str, rels: list[SemanticRelationship]) -> list[dict]:
        out = []
        for r in rels:
            if node_id in (r.source, r.target):
                src, tgt = r.source, r.target
                if not r.directed and src > tgt:
                    src, tgt = tgt, src
                out.append(TEdge(
                    id=r.id, source=src, target=tgt, kind=r.kind,
                    proto="sem", ports=[], active=True, directed=r.directed,
                ).to_dict())
        return out

    def _detection_event(
        self, det: Detection, rels: list[SemanticRelationship]
    ) -> Event:
        etype = {
            "HERMES": EVENT_HERMES_DETECTED,
            "LM_STUDIO": EVENT_LM_STUDIO_DETECTED,
            "MCP_SERVER": EVENT_MCP_SERVER_DETECTED,
        }.get(det.semantic_type, "SEMANTIC_DETECTED")
        return Event(
            event_id=self._next_id(),
            event_type=etype,
            source=det.node_id,
            target=None,
            timestamp=_now_iso(),
            metadata={
                "semantic_type": det.semantic_type,
                "semantic_name": det.semantic_name,
                "confidence": det.confidence,
                "node_id": det.node_id,
                "node": self._det_node(det).to_dict(),
                "edges": self._node_edges(det.node_id, rels),
                "detection": det.to_dict(),
            },
        )

    def _model_event(
        self, node_id: str, state: str, detections: list[Detection],
        rels: list[SemanticRelationship],
    ) -> Event:
        det = next((d for d in detections if d.node_id == node_id), None)
        return Event(
            event_id=self._next_id(),
            event_type=EVENT_MODEL_LOADED if state == "LOADED" else EVENT_MODEL_AVAILABLE,
            source=node_id,
            target=None,
            timestamp=_now_iso(),
            metadata={
                "semantic_type": "LOCAL_LLM",
                "semantic_name": det.semantic_name if det else node_id,
                "confidence": det.confidence if det else "HIGH",
                "node_id": node_id,
                "state": state,
                "node": self._det_node(det).to_dict() if det else None,
                "edges": self._node_edges(node_id, rels),
            },
        )
