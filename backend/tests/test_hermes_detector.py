"""Hermes detector tests: strong detection, ambiguous rejection,
hint-assisted detection, relationships."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.detectors.base import CONFIDENCE_CONFIRMED, CONFIDENCE_MEDIUM
from app.detectors.hermes import HERMES_SEMANTIC_NODE, HermesDetector
from app.detectors.registry import DetectorContext
from app.services.topology import TopologyEngine

from conftest import make_conn, make_proc, make_snap

HERMES_EXE = (
    r"C:\Users\xampos\AppData\Local\hermes\hermes-agent\apps\desktop"
    r"\release\win-unpacked\Hermes.exe"
)


def _hermes_snap(with_path=True, with_gateway=True, with_children=True):
    procs = [
        make_proc(9852, name="Hermes.exe", exe=HERMES_EXE if with_path else r"C:\bin\Hermes.exe",
                  cmdline=[HERMES_EXE if with_path else r"C:\bin\Hermes.exe"]),
    ]
    if with_children:
        procs.append(make_proc(14356, name="Hermes.exe", exe=HERMES_EXE,
                               ppid=9852, cmdline=[HERMES_EXE, "--type=gpu-process"]))
        procs.append(make_proc(22120, name="Hermes.exe", exe=HERMES_EXE,
                               ppid=9852, cmdline=[HERMES_EXE, "--type=renderer"]))
    if with_gateway:
        gw_exe = r"C:\Users\xampos\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
        procs.append(make_proc(
            25332, name="python.exe", exe=gw_exe, ppid=9852,
            cmdline=[gw_exe, "-m", "hermes_cli.main", "serve", "--host", "127.0.0.1", "--port", "0"],
        ))
        procs.append(make_proc(
            18744, name="python.exe", exe=r"C:\Users\xampos\AppData\Roaming\uv\python\cpython-3.11\python.exe",
            ppid=25332, cmdline=["python.exe", "-m", "hermes_cli.main", "serve"],
        ))
    return make_snap(procs=procs)


def _run(detector, snap, gpu=None):
    topo = TopologyEngine(Settings()).build(snap)
    ctx = DetectorContext(snap=snap, topo=topo, gpu=gpu or [])
    return detector.detect(ctx)


def test_strong_detection_confirmed():
    det = HermesDetector(Settings())
    snap = _hermes_snap()
    detections, rels = _run(det, snap)
    assert len(detections) == 1
    d = detections[0]
    assert d.semantic_type == "HERMES"
    assert d.semantic_name == "HERMES AGENT"
    assert d.confidence == CONFIDENCE_CONFIRMED
    assert d.node_id == HERMES_SEMANTIC_NODE
    # 5 underlying processes: 3x Hermes.exe + 2x gateway
    assert len(d.process_ids) == 5
    assert d.pids == [9852, 14356, 22120, 25332, 18744]
    sources = {e.source for e in d.evidence}
    assert "executable_path" in sources
    assert "cmdline" in sources
    # membership edges for every process + real parent/child edges
    kinds = {r.kind for r in rels}
    assert "MEMBER_OF" in kinds
    assert "PROCESS_PARENT" in kinds
    member_targets = [r for r in rels if r.kind == "MEMBER_OF"]
    assert len(member_targets) == 5
    assert all(r.target == HERMES_SEMANTIC_NODE for r in member_targets)


def test_name_only_is_not_confirmed():
    """A bare 'Hermes.exe' name (wrong path, no gateway) is MEDIUM — a weak
    guess must never become a fact."""
    det = HermesDetector(Settings())
    snap = _hermes_snap(with_path=False, with_gateway=False, with_children=False)
    detections, _ = _run(det, snap)
    assert len(detections) == 1
    assert detections[0].confidence == CONFIDENCE_MEDIUM
    assert all(e.source != "executable_path" for e in detections[0].evidence)


def test_no_match_rejected():
    det = HermesDetector(Settings())
    snap = make_snap(procs=[
        make_proc(1, name="python.exe", exe=r"C:\bin\python.exe",
                  cmdline=["python.exe", "-m", "app"]),
        make_proc(2, name="node.exe", exe=r"C:\bin\node.exe",
                  cmdline=["node", "server.js"]),
    ])
    detections, rels = _run(det, snap)
    assert detections == []
    assert rels == []


def test_hint_assisted_detection():
    """A name matched ONLY through config hints is LOW, never styled strong."""
    det = HermesDetector(Settings(), hints={"process_patterns": ["AgentHermes"]})
    snap = make_snap(procs=[
        make_proc(42, name="AgentHermes.exe", exe=r"C:\Games\AgentHermes.exe",
                  cmdline=["AgentHermes.exe"]),
    ])
    detections, _ = _run(det, snap)
    assert len(detections) == 1
    assert detections[0].confidence == "LOW"
    assert detections[0].evidence[0].source == "config_hint"


def test_gateway_listener_hosts_relationship():
    det = HermesDetector(Settings())
    snap = _hermes_snap()
    snap.connections = {
        make_conn(25332, lip="127.0.0.1", lport=34221).key: make_conn(
            25332, lip="127.0.0.1", lport=34221
        ),
    }
    snap.owner_map = {k: snap.processes[
        next(s for s, p in snap.processes.items() if p.pid == 25332)
    ].stable_id for k in snap.connections}
    detections, rels = _run(det, snap)
    hosts = [r for r in rels if r.kind == "HOSTS"]
    assert len(hosts) == 1
    assert hosts[0].source == HERMES_SEMANTIC_NODE
    assert hosts[0].target == "lst:tcp:127.0.0.1:34221"
