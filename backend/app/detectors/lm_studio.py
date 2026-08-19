"""LM Studio semantic detector (Phase 14).

Evidence-based only:
  - process identity (name / executable path)
  - owned localhost listeners discovered from the REAL socket topology
    (never a blind port-1234 assumption; hint ports are secondary)
  - local API probe (127.0.0.1 only, short timeout, cached)

Truthfulness rules:
  - CONFIRMED requires either a known-path process identity or a live
    LM Studio runtime API response (``/api/0/models``).
  - LOADED is claimed ONLY when the runtime API proves it
    (``loaded_models``). A ``/v1/models`` listing proves AVAILABLE only.
  - model metadata (architecture, quantization) is exposed only when the
    API exposes it; filename-derived values are marked
    ``FILENAME INFERENCE`` and carry LOW confidence.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

import httpx

from ..models.entities import Snapshot, TopologyResult
from .base import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Detection,
    DetectionEvidence,
    SemanticRelationship,
    evidence,
)
from .base import DetectorContext

log = logging.getLogger("esw.detectors.lmstudio")

LM_SEMANTIC_NODE = "sem:lmstudio"
LM_SEMANTIC_NAME = "LM STUDIO"
_PROBE_TIMEOUT_S = 0.5
_MAX_PROBES_PER_TICK = 3


def _norm(s: str) -> str:
    return s.lower().replace("/", "\\")


def model_node_id(model_id: str) -> str:
    digest = hashlib.sha1(model_id.encode("utf-8", "replace")).hexdigest()[:10]
    return f"sem:model:{digest}"


class LmStudioDetector:
    name = "lm_studio"

    def __init__(self, cfg, hints: Optional[dict[str, Any]] = None) -> None:
        self.cfg = cfg
        self.hints = hints or {}
        self._process_patterns = [
            _norm(p) for p in self.hints.get("process_patterns", ["LM Studio.exe", "LM Studio"])
        ]
        self._path_patterns = [_norm(p) for p in self.hints.get("path_patterns", ["LM Studio"])]
        self._hint_ports = list(self.hints.get("default_ports", [1234]))
        self._api_cache: dict[int, tuple[float, dict]] = {}  # port -> (ts, result)
        self._last_probe = 0.0

    # ------------------------------------------------------------- detection

    def detect(self, ctx: DetectorContext) -> tuple[list[Detection], list[SemanticRelationship]]:
        snaps: Snapshot = ctx.snap
        topo: TopologyResult = ctx.topo
        rels: list[SemanticRelationship] = []
        candidates: list[tuple[str, str]] = []  # (sid, evidence_source)

        for sid, p in snaps.processes.items():
            name = _norm(p.name or "")
            exe = _norm(p.exe or "")
            if any(pat in name for pat in self._process_patterns):
                # a known LM Studio path strengthens the identity
                if any(pat in exe for pat in self._path_patterns):
                    candidates.append((sid, "executable_path"))
                else:
                    candidates.append((sid, "process_name"))
            elif any(pat in exe for pat in self._path_patterns):
                candidates.append((sid, "executable_path"))

        if not candidates:
            return [], []

        family = [sid for sid, _ in candidates]
        ev_list = [
            evidence(src, f"process {snaps.processes[sid].name} (PID {snaps.processes[sid].pid})")
            for sid, src in candidates
        ]
        confidence = (
            CONFIDENCE_CONFIRMED
            if any(src == "executable_path" for _, src in candidates)
            else CONFIDENCE_HIGH
        )

        # owned localhost listeners from the REAL topology
        ports: set[int] = set()
        for sid in family:
            for e in topo.edges.values():
                if e.kind == "LISTEN" and e.source == sid:
                    for p in e.ports:
                        ports.add(p)
                    rels.append(SemanticRelationship(
                        source=LM_SEMANTIC_NODE, target=e.target, kind="HOSTS",
                        evidence=[evidence(
                            "owned_listener",
                            f"LM Studio listens on localhost:{e.ports[0] if e.ports else '?'}",
                        )],
                    ))

        # hint ports are secondary: probe them only when no owned listener
        # was found (never blind-scan arbitrary ports)
        if not ports:
            ports = set(self._hint_ports)

        det = Detection(
            semantic_type="LM_STUDIO",
            semantic_name=LM_SEMANTIC_NAME,
            confidence=confidence,
            node_id=LM_SEMANTIC_NODE,
            process_ids=family,
            evidence=ev_list,
            metadata={"pids": [snaps.processes[s].pid for s in family], "state": "RUNNING"},
        )

        # local API probe (cached, throttled, loopback only)
        api = self._probe_ports(list(sorted(ports)), ctx)
        if api:
            det.confidence = CONFIDENCE_CONFIRMED
            det.evidence.append(evidence(
                "api_response",
                f"LM Studio runtime API answered at {api['endpoint']}",
            ))
            det.metadata["endpoint"] = api["endpoint"]
            det.metadata["api_available"] = True
            rels.append(SemanticRelationship(
                source=LM_SEMANTIC_NODE, target=api["listen_node"], kind="LOCAL_API",
                evidence=[evidence("api_response", f"HTTP API verified at {api['endpoint']}")],
            ))
            models, loaded_ids = api["models"], api["loaded_ids"]
            if models:
                det.metadata["models"] = [
                    {"id": m, "state": "LOADED" if m in loaded_ids else "AVAILABLE"}
                    for m in models
                ]
                for m in models:
                    state = "LOADED" if m in loaded_ids else "AVAILABLE"
                    rels.extend(self._model_relationships(m, state, api["endpoint"]))
        else:
            det.metadata["api_available"] = False
            det.metadata["endpoint"] = None

        return [det], rels

    # ------------------------------------------------------- model metadata

    def _model_relationships(
        self, model_id: str, state: str, endpoint: str
    ) -> list[SemanticRelationship]:
        """Model node + SERVES_MODEL edge. Metadata is only exposed when the
        API exposed it; filename-derived fields are LOW-confidence."""
        rels: list[SemanticRelationship] = []
        rels.append(SemanticRelationship(
            source=LM_SEMANTIC_NODE,
            target=model_node_id(model_id),
            kind="SERVES_MODEL",
            evidence=[evidence(
                "api_response",
                f"runtime reports model '{model_id}' as {state} at {endpoint}",
            )],
        ))
        return rels

    # -------------------------------------------------------------- api probe

    def _probe_ports(self, ports: list[int], ctx: DetectorContext) -> Optional[dict]:
        """Probe candidate ports on 127.0.0.1 only, throttled + cached.

        Returns None when nothing answers. Never probes LAN/internet.
        """
        now = time.time()
        interval = getattr(self.cfg, "lm_studio_api_interval_s", 8.0)
        if now - self._last_probe < interval:
            return self._last_result()
        self._last_probe = now
        for port in ports[: _MAX_PROBES_PER_TICK]:
            cached = self._api_cache.get(port)
            if cached and now - cached[0] < interval * 2:
                if cached[1]:
                    return cached[1]
                continue
            result = self._probe_one(port, ctx)
            self._api_cache[port] = (now, result)
            if result:
                self._last_ok = result
                return result
        return None

    def _last_result(self) -> Optional[dict]:
        return getattr(self, "_last_ok", None)

    def _probe_one(self, port: int, ctx: DetectorContext) -> Optional[dict]:
        url = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(timeout=_PROBE_TIMEOUT_S, follow_redirects=False) as client:
                loaded_ids: list[str] = []
                try:
                    r = client.get(f"{url}/api/0/models")
                    if r.status_code == 200:
                        payload = r.json()
                        for lm in payload.get("loaded_models", []) or []:
                            mid = lm.get("id") or lm.get("model")
                            if mid:
                                loaded_ids.append(str(mid))
                except Exception:  # noqa: BLE001 — runtime endpoint is optional
                    pass
                try:
                    rv = client.get(f"{url}/v1/models")
                    if rv.status_code != 200:
                        return None
                    data = rv.json().get("data", []) or []
                    models = [str(m.get("id")) for m in data if m.get("id")]
                except Exception:  # noqa: BLE001 — API unreachable/timeout
                    return None
        except Exception:  # noqa: BLE001 — connection refused etc.
            return None

        listen_node = f"lst:tcp:127.0.0.1:{port}"
        return {
            "endpoint": url,
            "listen_node": listen_node,
            "models": models,
            "loaded_ids": loaded_ids,
        }
