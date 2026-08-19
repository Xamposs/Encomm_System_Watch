"""MCP detector tests: process ancestry, stdio case, network case,
false-positive rejection."""
from __future__ import annotations

import pytest

from app.config import Settings
from app.detectors.mcp import McpDetector
from app.detectors.registry import DetectorContext
from app.services.topology import TopologyEngine

from conftest import make_conn, make_proc, make_snap

GW_EXE = r"C:\Users\xampos\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
FS_SERVER = (r"C:\Users\xampos\AppData\Local\hermes\hermes-agent\venv\node_modules"
             r"\@modelcontextprotocol\server-filesystem\dist\index.js")


def _gateway_proc(pid=25332, name="python.exe", ppid=9852):
    return make_proc(pid, name=name, exe=GW_EXE, ppid=ppid,
                     cmdline=[GW_EXE, "-m", "hermes_cli.main", "serve", "--port", "0"])


def _run(detector, snap):
    topo = TopologyEngine(Settings()).build(snap)
    ctx = DetectorContext(snap=snap, topo=topo)
    return detector.detect(ctx)


def test_ancestry_stdio_detection():
    """MCP server spawned by the Hermes gateway over stdio (no sockets)."""
    det = McpDetector(Settings())
    snap = make_snap(procs=[
        _gateway_proc(),
        make_proc(3311, name="node.exe", exe=r"C:\bin\node.exe", ppid=25332,
                  cmdline=["node", FS_SERVER, r"C:\data"]),
    ])
    detections, rels = _run(det, snap)
    assert len(detections) == 1
    d = detections[0]
    assert d.semantic_type == "MCP_SERVER"
    assert d.semantic_name == "filesystem"       # package path proves identity
    assert d.confidence == "HIGH"
    assert d.metadata["transport"] == "stdio"
    assert d.pids == [3311]
    kinds = {r.kind for r in rels}
    assert "SPAWNED" in kinds
    assert "PROCESS_PARENT" in kinds
    assert "MEMBER_OF" in kinds
    spawned = next(r for r in rels if r.kind == "SPAWNED")
    assert spawned.target == d.node_id
    # stdio: NO HOSTS/listener relationship — never a fake network edge
    assert "HOSTS" not in kinds


def test_network_transport_http():
    """MCP server owning a localhost listener is HTTP/SSE with HOSTS."""
    det = McpDetector(Settings())
    snap = make_snap(procs=[
        _gateway_proc(),
        make_proc(3312, name="node.exe", exe=r"C:\bin\node.exe", ppid=25332,
                  cmdline=["node", FS_SERVER, "--port", "8931"]),
    ])
    c = make_conn(3312, lip="127.0.0.1", lport=8931)
    snap.connections = {c.key: c}
    snap.owner_map = {c.key: next(p for s, p in snap.processes.items() if p.pid == 3312).stable_id}
    detections, rels = _run(det, snap)
    assert len(detections) == 1
    assert detections[0].metadata["transport"] == "HTTP/SSE"
    assert any(r.kind == "HOSTS" and r.target == "lst:tcp:127.0.0.1:8931" for r in rels)


def test_unknown_server_when_unproven():
    """MCP proven (gateway child) but no identity -> 'unknown', not a guess."""
    det = McpDetector(Settings())
    snap = make_snap(procs=[
        _gateway_proc(),
        make_proc(3313, name="node.exe", exe=r"C:\bin\node.exe", ppid=25332,
                  cmdline=["node", r"C:\tools\bridge\index.js", "--mcp", "--stdio"]),
    ])
    detections, _ = _run(det, snap)
    assert len(detections) == 1
    assert detections[0].semantic_name == "unknown"
    assert detections[0].confidence == "MEDIUM"


def test_false_positive_rejected():
    """'mcp' substring in a random command line without launcher/package
    context must NOT be classified as an MCP server."""
    det = McpDetector(Settings())
    snap = make_snap(procs=[
        make_proc(5001, name="mcproxy.exe", exe=r"C:\bin\mcproxy.exe", ppid=4,
                  cmdline=[r"C:\bin\mcproxy.exe", "--config", "a.json"]),
        make_proc(5002, name="explorer.exe", exe=r"C:\Windows\explorer.exe", ppid=4,
                  cmdline=[r"C:\Windows\explorer.exe"]),
    ])
    detections, _ = _run(det, snap)
    assert detections == []


def test_python_mcp_server_child_of_hermes():
    """A python-based MCP server is detected through the same ancestry path."""
    det = McpDetector(Settings())
    snap = make_snap(procs=[
        _gateway_proc(),
        make_proc(3314, name="python.exe", exe=r"C:\bin\python.exe", ppid=25332,
                  cmdline=["python.exe", "-m", "mcp_server_git", "--stdio"]),
    ])
    detections, _ = _run(det, snap)
    assert len(detections) == 1
    assert detections[0].semantic_name == "git"
