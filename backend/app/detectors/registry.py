"""Semantic detector registry.

Runs every registered detector over one snapshot and aggregates their
:class:`Detection` results and :class:`SemanticRelationship` edges.

Failure isolation is a hard rule: one broken detector (NVML missing, API
timeout, parser exception) must degrade ONLY that detector. ``run_all``
catches per-detector exceptions and carries on; the degraded detector is
reported via ``errors`` so the API can expose the truth without lying.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import Settings
from ..models.entities import Snapshot, TopologyResult
from .base import Detection, DetectorContext, SemanticRelationship
from .hermes import HermesDetector
from .lm_studio import LmStudioDetector
from .mcp import McpDetector

log = logging.getLogger("esw.detectors")

DEFAULT_HINTS: dict[str, Any] = {
    "hermes": {
        "process_patterns": ["Hermes.exe", "hermes.exe"],
        "path_patterns": ["hermes-agent"],
        "cmdline_patterns": ["hermes_cli.main"],
        "known_ports": [],
    },
    "lm_studio": {
        "process_patterns": ["LM Studio.exe", "LM Studio"],
        "path_patterns": ["LM Studio"],
        "default_ports": [1234],
    },
    "mcp": {
        "cmdline_patterns": ["mcp"],
        "path_patterns": [
            "node_modules/@modelcontextprotocol",
            "mcp-server",
            "mcp_server",
            "\\mcp\\",
        ],
        "launcher_patterns": ["hermes_cli.main", "hermes", "@modelcontextprotocol"],
        "package_patterns": ["@modelcontextprotocol", "mcp-"],
    },
}


@dataclass
class DetectorContext:
    """Everything a detector may legally look at (all local, all sanitized)."""

    snap: Snapshot
    topo: TopologyResult
    gpu: list[dict[str, Any]] = field(default_factory=list)
    pid_to_sid: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pid_to_sid:
            self.pid_to_sid = {
                p.pid: sid for sid, p in self.snap.processes.items() if p.pid is not None
            }


def _load_hints(path: Optional[Path]) -> dict[str, Any]:
    """Load config/detectors.json (hints only — never secrets).

    A missing/broken hints file is not fatal: built-in defaults remain.
    """
    if path is None:
        return json.loads(json.dumps(DEFAULT_HINTS))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        merged = json.loads(json.dumps(DEFAULT_HINTS))
        for section in ("hermes", "lm_studio", "mcp"):
            if isinstance(raw.get(section), dict):
                merged[section].update(raw[section])
        return merged
    except Exception as exc:  # noqa: BLE001 — hints must never break startup
        log.warning("detectors.json unreadable (%s) — using built-in defaults", exc)
        return json.loads(json.dumps(DEFAULT_HINTS))


class SemanticDetectorRegistry:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        hints_path = None
        override = os.environ.get("ESW_DETECTORS_CONFIG")
        if override:
            hints_path = Path(override)
        elif cfg.detectors_config_path:
            hints_path = Path(cfg.detectors_config_path)
        self.hints = _load_hints(hints_path)
        self.detectors = [
            HermesDetector(cfg, self.hints.get("hermes", {})),
            LmStudioDetector(cfg, self.hints.get("lm_studio", {})),
            McpDetector(cfg, self.hints.get("mcp", {})),
        ]
        self.errors: dict[str, str] = {}

    def run_all(
        self,
        snap: Snapshot,
        topo: TopologyResult,
        gpu: Optional[list[dict[str, Any]]] = None,
    ) -> tuple[list[Detection], list[SemanticRelationship]]:
        """Run every detector; a single failure never propagates.

        Returns (detections, relationships). Per-detector exceptions are
        recorded in ``self.errors`` (surfaced by the API) and the other
        detectors still run.
        """
        ctx = DetectorContext(snap=snap, topo=topo, gpu=gpu or [])
        all_detections: list[Detection] = []
        all_relationships: list[SemanticRelationship] = []
        self.errors = {}
        for det in self.detectors:
            try:
                detections, relationships = det.detect(ctx)
                all_detections.extend(detections)
                all_relationships.extend(relationships)
            except Exception as exc:  # noqa: BLE001 — failure isolation rule
                log.warning("detector %s failed: %s", det.name, exc, exc_info=True)
                self.errors[det.name] = f"{type(exc).__name__}: {exc}"
        return all_detections, all_relationships
