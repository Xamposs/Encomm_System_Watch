"""Semantic detection framework (GPU + AI semantic observability, v0.3.0).

Detectors convert raw process/socket topology into evidence-backed semantic
identities (HERMES, LM STUDIO, MCP SERVER, LOCAL LLM) plus semantic resource
relationships (USES_GPU, SERVES_MODEL, LOCAL_API, PROCESS_PARENT, SPAWNED,
HOSTS, MEMBER_OF). Raw process truth is always preserved underneath.
"""
from .base import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Detection,
    DetectionEvidence,
    SemanticRelationship,
    confidence_from_evidence,
)
from .base import DetectorContext
from .registry import SemanticDetectorRegistry

__all__ = [
    "CONFIDENCE_CONFIRMED",
    "CONFIDENCE_HIGH",
    "CONFIDENCE_LOW",
    "CONFIDENCE_MEDIUM",
    "Detection",
    "DetectionEvidence",
    "SemanticRelationship",
    "SemanticDetectorRegistry",
    "DetectorContext",
    "confidence_from_evidence",
]
