"""Semantic engine tests: node/edge building, change-only events, lost
detections, model state flips, process augmentation, USES_GPU edges."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.detectors.base import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_HIGH,
    Detection,
    SemanticRelationship,
    evidence,
)
from app.services.semantic import (
    EVENT_GPU_PROCESS_ATTACHED,
    EVENT_HERMES_DETECTED,
    EVENT_MODEL_AVAILABLE,
    EVENT_MODEL_LOADED,
    EVENT_SEMANTIC_LOST,
    SemanticEngine,
)
from app.services.topology import TopologyEngine

from conftest import make_proc, make_snap


def _hermes_det(sids, pids):
    return Detection(
        semantic_type="HERMES", semantic_name="HERMES AGENT",
        confidence=CONFIDENCE_CONFIRMED, node_id="sem:hermes",
        process_ids=sids, evidence=[evidence("executable_path", "Hermes.exe path")],
        metadata={"pids": pids, "state": "RUNNING"},
    )


def _model_det(model_id, state):
    return Detection(
        semantic_type="LOCAL_LLM", semantic_name=model_id,
        confidence=CONFIDENCE_HIGH, node_id=f"sem:model:{model_id[:8]}",
        process_ids=[], evidence=[evidence("api_response", "runtime api")],
        metadata={"pids": [], "state": state, "endpoint": "http://127.0.0.1:1234"},
    )


def _snap_with(procs):
    snap = make_snap(procs=procs)
    return snap, TopologyEngine(Settings()).build(snap)


def test_detection_events_emitted_once():
    eng = SemanticEngine(Settings())
    snap, topo = _snap_with([make_proc(1, name="Hermes.exe")])
    det = _hermes_det(["proc:1:1000"], [1])
    rels = [SemanticRelationship(source="proc:1:1000", target="sem:hermes", kind="MEMBER_OF",
                                 evidence=[evidence("process_name", "x")])]
    ev1 = eng.update([det], rels, [], snap, topo)
    assert len(ev1) == 1
    assert ev1[0].event_type == EVENT_HERMES_DETECTED
    assert ev1[0].metadata["node"]["kind"] == "SEMANTIC"
    assert ev1[0].metadata["node"]["id"] == "sem:hermes"
    assert len(ev1[0].metadata["edges"]) == 1
    # identical state -> NO repeated events (no spam)
    ev2 = eng.update([det], rels, [], snap, topo)
    assert ev2 == []


def test_semantic_lost_on_disappearance():
    eng = SemanticEngine(Settings())
    snap, topo = _snap_with([make_proc(1, name="Hermes.exe")])
    det = _hermes_det(["proc:1:1000"], [1])
    eng.update([det], [], [], snap, topo)
    events = eng.update([], [], [], snap, topo)
    assert len(events) == 1
    assert events[0].event_type == EVENT_SEMANTIC_LOST
    assert events[0].metadata["node_id"] == "sem:hermes"


def test_model_state_flip_events():
    eng = SemanticEngine(Settings())
    snap, topo = _snap_with([make_proc(1, name="a.exe")])
    m = _model_det("qwen3-4b", "AVAILABLE")
    ev1 = eng.update([m], [], [], snap, topo)
    assert [e.event_type for e in ev1] == [EVENT_MODEL_AVAILABLE]
    m2 = _model_det("qwen3-4b", "LOADED")
    ev2 = eng.update([m2], [], [], snap, topo)
    assert [e.event_type for e in ev2] == [EVENT_MODEL_LOADED]
    # no flip -> nothing
    ev3 = eng.update([m2], [], [], snap, topo)
    assert ev3 == []


def test_gpu_uses_edges_and_nodes():
    eng = SemanticEngine(Settings())
    procs = [make_proc(1234, name="llama.exe")]
    snap, topo = _snap_with(procs)
    gpu = [{
        "index": 0, "name": "NVIDIA GeForce GTX 1660 Ti",
        "utilization_percent": 74, "vram_used_mb": 5300, "vram_total_mb": 6144,
        "temperature_c": 67, "power_w": 92.0,
        "processes": [{"pid": 1234, "vram_mb": 5000.0}, {"pid": 9999}],
    }]
    eng.update([], [], gpu, snap, topo)
    nodes = eng.semantic_nodes()
    assert len(nodes) == 1
    assert nodes[0].kind == "GPU"
    assert nodes[0].id == "gpu:0"
    assert nodes[0].data["vram_total_mb"] == 6144
    edges = eng.semantic_edges()
    # only pid 1234 is a known process -> exactly one USES_GPU edge
    uses = [e for e in edges if e.kind == "USES_GPU"]
    assert len(uses) == 1
    assert uses[0].source == "proc:1234:1000000"
    assert uses[0].target == "gpu:0"


def test_augment_process_nodes_marks_semantic():
    eng = SemanticEngine(Settings())
    snap, topo = _snap_with([make_proc(1, name="Hermes.exe")])
    # make_proc(ct=1000.0) -> stable_id "proc:1:1000000"
    det = _hermes_det(["proc:1:1000000"], [1])
    eng.update([det], [], [], snap, topo)
    nodes = eng.augment_process_nodes(list(topo.nodes.values()))
    proc_node = next(n for n in nodes if n.kind == "PROCESS")
    assert proc_node.data["semantic"]["semantic_type"] == "HERMES"
    assert proc_node.data["semantic"]["confidence"] == CONFIDENCE_CONFIRMED
    # unrelated processes stay unmarked
    snap2, topo2 = _snap_with([make_proc(2, name="other.exe")])
    nodes2 = eng.augment_process_nodes(list(topo2.nodes.values()))
    other = next(n for n in nodes2 if n.kind == "PROCESS")
    assert "semantic" not in other.data


def test_summary_only_real_categories():
    eng = SemanticEngine(Settings())
    snap, topo = _snap_with([make_proc(1, name="a.exe")])
    s = eng.summary()
    assert s == {"hermes": False, "lm_studio": False, "models": [], "mcp": [], "gpu": []}
    eng.update([_hermes_det(["proc:1:1000"], [1])], [], [], snap, topo)
    s2 = eng.summary()
    assert s2["hermes"] is True
    assert s2["lm_studio"] is False
