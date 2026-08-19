"""Semantic framework tests: evidence serialization, confidence handling,
detector failure isolation, hints loading."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.detectors.base import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    Detection,
    DetectionEvidence,
    SemanticRelationship,
    confidence_from_evidence,
    evidence,
)
from app.detectors.registry import DEFAULT_HINTS, SemanticDetectorRegistry
from app.services.topology import TopologyEngine

from conftest import make_proc, make_snap


# ---------------------------------------------------------------- models

def test_evidence_serialization():
    e = evidence("cmdline", "hermes gateway command line")
    d = e.to_dict()
    assert d == {"source": "cmdline", "detail": "hermes gateway command line"}


def test_detection_serialization():
    det = Detection(
        semantic_type="HERMES",
        semantic_name="HERMES AGENT",
        confidence=CONFIDENCE_HIGH,
        node_id="sem:hermes",
        process_ids=["proc:1:1000", "proc:2:1000"],
        evidence=[evidence("process_name", "Hermes.exe")],
        metadata={"pids": [1, 2], "state": "RUNNING"},
    )
    d = det.to_dict()
    assert d["semantic_type"] == "HERMES"
    assert d["semantic_name"] == "HERMES AGENT"
    assert d["confidence"] == "HIGH"
    assert d["node_id"] == "sem:hermes"
    assert d["process_ids"] == ["proc:1:1000", "proc:2:1000"]
    assert d["pids"] == [1, 2]
    assert d["evidence"][0]["source"] == "process_name"
    assert d["metadata"]["state"] == "RUNNING"


def test_relationship_id_stable():
    r1 = SemanticRelationship(source="a", target="b", kind="USES_GPU")
    r2 = SemanticRelationship(source="b", target="a", kind="USES_GPU", directed=False)
    assert r1.id == "se:a->b:USES_GPU"
    # undirected edges canonicalize endpoint order -> stable id
    assert r2.id == "se:a->b:USES_GPU"


# ------------------------------------------------------------- confidence

def test_confidence_mapping():
    assert confidence_from_evidence(["api_response"]) == CONFIDENCE_HIGH
    assert confidence_from_evidence(["executable_path"]) == CONFIDENCE_HIGH
    assert confidence_from_evidence(["cmdline"]) == CONFIDENCE_MEDIUM
    assert confidence_from_evidence(["process_name"]) == CONFIDENCE_MEDIUM
    assert confidence_from_evidence(["config_hint"]) == CONFIDENCE_LOW
    assert confidence_from_evidence(["filename_inference"]) == CONFIDENCE_LOW
    assert confidence_from_evidence([]) == CONFIDENCE_LOW


def test_confidence_never_fabricates_facts():
    """A weak single hint can never reach HIGH through combining noise."""
    assert confidence_from_evidence(["config_hint", "filename_inference"]) == CONFIDENCE_LOW


# --------------------------------------------------------- failure isolation

def test_registry_failure_isolation(monkeypatch):
    """One crashing detector degrades only itself; the others still run."""
    from app.detectors.base import Detection

    class BoomDetector:
        name = "boom"

        def detect(self, ctx):
            raise RuntimeError("boom")

    class OkDetector:
        name = "ok"

        def detect(self, ctx):
            return [Detection(
                semantic_type="HERMES", semantic_name="HERMES AGENT",
                confidence=CONFIDENCE_HIGH, node_id="sem:hermes",
                process_ids=[], evidence=[evidence("config_hint", "ok")],
            )], []

    reg = SemanticDetectorRegistry(Settings())
    reg.detectors = [BoomDetector(), OkDetector()]
    snap = make_snap(procs=[make_proc(1, name="a.exe")])
    topo = TopologyEngine(Settings()).build(snap)
    detections, rels = reg.run_all(snap, topo)
    assert len(detections) == 1
    assert detections[0].semantic_name == "HERMES AGENT"
    assert "boom" in reg.errors
    assert "ok" not in reg.errors


def test_broken_hints_file_falls_back_to_defaults(tmp_path):
    bad = tmp_path / "detectors.json"
    bad.write_text("{not json!!")
    import app.detectors.registry as reg_mod

    hints = reg_mod._load_hints(bad)
    assert hints == DEFAULT_HINTS


def test_hints_merge_user_section(tmp_path):
    good = tmp_path / "detectors.json"
    good.write_text('{"hermes": {"process_patterns": ["Custom.exe"]}}')
    import app.detectors.registry as reg_mod

    hints = reg_mod._load_hints(good)
    assert hints["hermes"]["process_patterns"] == ["Custom.exe"]
    # other sections keep defaults
    assert hints["lm_studio"]["default_ports"] == [1234]
