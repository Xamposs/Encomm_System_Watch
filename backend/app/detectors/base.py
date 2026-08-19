"""Semantic detection data models.

A :class:`Detection` answers the five questions the checkpoint demands:

  WHAT was detected?        ``semantic_type`` / ``semantic_name``
  WHICH process/resource?  ``process_ids`` / ``pids``
  WHY?                     ``evidence`` (each item names its source)
  HOW confident?           ``confidence`` (CONFIRMED/HIGH/MEDIUM/LOW)
  WHAT evidence?           ``evidence`` (source + detail pairs)

Confidence rules (strict truthfulness — a weak guess is never a fact):

  - CONFIRMED — direct runtime observation (a local API answered with
    matching identity, an exact known executable path + identity combo).
  - HIGH      — two or more independent strong signals (name + path +
    listener + ancestry agreement).
  - MEDIUM    — one strong signal or several weak ones agreeing.
  - LOW       — a single weak signal (filename inference, config hint).

Only HIGH/CONFIRMED classifications receive strong semantic styling in the
graph; MEDIUM/LOW stay subdued and unknown remains unknown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---- confidence levels -----------------------------------------------------
CONFIDENCE_CONFIRMED = "CONFIRMED"
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_ORDER = [CONFIDENCE_CONFIRMED, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW]

# ---- evidence sources (stable machine-readable ids) ------------------------
EVIDENCE_PROCESS_NAME = "process_name"
EVIDENCE_EXECUTABLE_PATH = "executable_path"
EVIDENCE_CMDLINE = "cmdline"
EVIDENCE_PARENT_RELATIONSHIP = "parent_relationship"
EVIDENCE_CHILD_RELATIONSHIP = "child_relationship"
EVIDENCE_OWNED_LISTENER = "owned_listener"
EVIDENCE_API_RESPONSE = "api_response"
EVIDENCE_CONFIG_HINT = "config_hint"
EVIDENCE_GPU_PID = "gpu_pid"
EVIDENCE_FILENAME_INFERENCE = "filename_inference"

# weight per source: the strongest single source decides the confidence
_EVIDENCE_WEIGHTS: dict[str, float] = {
    EVIDENCE_API_RESPONSE: 1.0,           # direct runtime proof
    EVIDENCE_EXECUTABLE_PATH: 0.7,        # exact path match is near-proof
    EVIDENCE_CMDLINE: 0.6,
    EVIDENCE_PROCESS_NAME: 0.45,
    EVIDENCE_PARENT_RELATIONSHIP: 0.45,
    EVIDENCE_CHILD_RELATIONSHIP: 0.45,
    EVIDENCE_OWNED_LISTENER: 0.5,
    EVIDENCE_GPU_PID: 0.55,               # NVML attribution is direct
    EVIDENCE_CONFIG_HINT: 0.25,           # never strong on its own
    EVIDENCE_FILENAME_INFERENCE: 0.2,     # weakest possible source
}


def confidence_from_evidence(sources: list[str]) -> str:
    """Map the strongest evidence source to a confidence level.

    Callers may upgrade to CONFIRMED only with a direct observation
    (API response / exact-path identity proof).
    """
    best = max((_EVIDENCE_WEIGHTS.get(s, 0.1) for s in sources), default=0.0)
    if best >= 0.7:
        return CONFIDENCE_HIGH
    if best >= 0.45:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def confidence_rank(c: str) -> int:
    return CONFIDENCE_ORDER.index(c) if c in CONFIDENCE_ORDER else len(CONFIDENCE_ORDER)


@dataclass
class DetectionEvidence:
    source: str          # one of the EVIDENCE_* constants
    detail: str          # human-readable, sanitized, no credentials

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "detail": self.detail}


@dataclass
class Detection:
    semantic_type: str          # HERMES | LM_STUDIO | MCP_SERVER | LOCAL_LLM
    semantic_name: str          # "Hermes Agent", "LM Studio", "filesystem", ...
    confidence: str             # CONFIRMED | HIGH | MEDIUM | LOW
    node_id: str                # stable semantic node id (sem:..., gpu:...)
    process_ids: list[str]      # underlying process stable ids
    evidence: list[DetectionEvidence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def pids(self) -> list[int]:
        return [int(pid) for pid in self.metadata.get("pids", [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_type": self.semantic_type,
            "semantic_name": self.semantic_name,
            "confidence": self.confidence,
            "node_id": self.node_id,
            "process_ids": self.process_ids,
            "pids": self.metadata.get("pids", []),
            "evidence": [e.to_dict() for e in self.evidence],
            "metadata": self.metadata,
        }

    def key(self) -> str:
        """Identity used for change detection (type + node)."""
        return f"{self.semantic_type}|{self.node_id}"


@dataclass
class SemanticRelationship:
    source: str                    # process sid or semantic node id
    target: str
    kind: str                      # USES_GPU | SERVES_MODEL | LOCAL_API |
                                   # PROCESS_PARENT | SPAWNED | HOSTS | MEMBER_OF
    evidence: list[DetectionEvidence] = field(default_factory=list)
    directed: bool = True

    @property
    def id(self) -> str:
        src, tgt = self.source, self.target
        if not self.directed and src > tgt:
            src, tgt = tgt, src
        return f"se:{src}->{tgt}:{self.kind}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "directed": self.directed,
            "evidence": [e.to_dict() for e in self.evidence],
        }


def evidence(source: str, detail: str) -> DetectionEvidence:
    return DetectionEvidence(source=source, detail=detail)


@dataclass
class DetectorContext:
    """Everything a detector may legally look at (all local, all sanitized).

    Defined here (not in the registry) so detector modules can import it
    without a registry <-> detector import cycle.
    """

    snap: Any
    topo: Any
    gpu: list[dict[str, Any]] = field(default_factory=list)
    pid_to_sid: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.pid_to_sid:
            self.pid_to_sid = {
                p.pid: sid for sid, p in self.snap.processes.items() if p.pid is not None
            }
