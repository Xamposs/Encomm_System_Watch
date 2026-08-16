"""Diff engine tests: lifecycle events, throttling, suppression rules."""
from datetime import datetime

from app.services.diff_engine import (
    EVENT_CONNECTION_CLOSED,
    EVENT_CONNECTION_OPENED,
    EVENT_PROCESS_METRICS_UPDATED,
    EVENT_PROCESS_STARTED,
    EVENT_PROCESS_STOPPED,
    DiffEngine,
)
from app.services.topology import TopologyEngine, edge_id

from conftest import make_conn, make_proc, make_snap


def build(cfg, snap):
    return snap, TopologyEngine(cfg).build(snap)


def test_first_diff_is_empty(cfg):
    snap, topo = build(cfg, make_snap([make_proc(1)], []))
    engine = DiffEngine(cfg)
    assert engine.diff(snap, topo) == []


def test_process_started_and_stopped(cfg):
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([make_proc(1, "a.exe")], []))
    engine.diff(snap1, topo1)

    snap2, topo2 = build(cfg, make_snap([make_proc(1, "a.exe"), make_proc(2, "b.exe")], []))
    evs = engine.diff(snap2, topo2)
    started = [e for e in evs if e.event_type == EVENT_PROCESS_STARTED]
    assert len(started) == 1
    assert started[0].source == "proc:2:1000000"
    assert started[0].metadata["name"] == "b.exe"
    assert started[0].metadata["node"]["id"] == "proc:2:1000000"

    snap3, topo3 = build(cfg, make_snap([make_proc(1, "a.exe")], []))
    evs = engine.diff(snap3, topo3)
    stopped = [e for e in evs if e.event_type == EVENT_PROCESS_STOPPED]
    assert len(stopped) == 1
    assert stopped[0].source == "proc:2:1000000"
    assert stopped[0].metadata["pid"] == 2


def test_connection_opened_matches_topology_edge(cfg):
    p1 = make_proc(1, "a.exe")
    p2 = make_proc(2, "b.exe")
    c = make_conn(pid=1, lport=53121, rip="127.0.0.1", rport=1234, kind="localhost")
    c2 = make_conn(pid=2, lport=1234, rip="127.0.0.1", rport=53121, kind="localhost")
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1, p2], [c, c2]))
    engine.diff(snap1, topo1)

    # add an external connection
    c3 = make_conn(pid=1, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    snap2, topo2 = build(cfg, make_snap([p1, p2], [c, c2, c3]))
    evs = engine.diff(snap2, topo2)
    opened = [e for e in evs if e.event_type == EVENT_CONNECTION_OPENED]
    assert len(opened) == 1
    ev = opened[0]
    expected_eid = edge_id(p1.stable_id, "ext:104.18.22.44", "EXTERNAL")
    assert ev.metadata["edge_id"] == expected_eid
    assert ev.metadata["kind"] == "EXTERNAL"
    assert ev.metadata["edge_port"] == 443
    assert ev.metadata["src_label"] == "a.exe"
    assert ev.metadata["tgt_label"] == "104.18.22.44"
    assert ev.source == p1.stable_id


def test_connection_closed_remaining_counts(cfg):
    p1 = make_proc(1, "a.exe")
    ext1 = make_conn(pid=1, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    ext2 = make_conn(pid=1, lip="192.168.1.5", lport=51001, rip="104.18.22.44", rport=443)
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1], [ext1, ext2]))
    engine.diff(snap1, topo1)

    # one of two conns closes
    snap2, topo2 = build(cfg, make_snap([p1], [ext2]))
    evs = engine.diff(snap2, topo2)
    closed = [e for e in evs if e.event_type == EVENT_CONNECTION_CLOSED]
    assert len(closed) == 1
    assert closed[0].metadata["remaining"] == 1
    eid = edge_id(p1.stable_id, "ext:104.18.22.44", "EXTERNAL")
    assert closed[0].metadata["edge_id"] == eid

    # last conn closes
    snap3, topo3 = build(cfg, make_snap([p1], []))
    evs = engine.diff(snap3, topo3)
    closed = [e for e in evs if e.event_type == EVENT_CONNECTION_CLOSED]
    assert len(closed) == 1
    assert closed[0].metadata["remaining"] == 0


def test_no_close_events_when_owner_process_stopped(cfg):
    p1 = make_proc(1, "a.exe")
    ext = make_conn(pid=1, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1], [ext]))
    engine.diff(snap1, topo1)

    snap2, topo2 = build(cfg, make_snap([], []))
    evs = engine.diff(snap2, topo2)
    types = {e.event_type for e in evs}
    assert EVENT_PROCESS_STOPPED in types
    assert EVENT_CONNECTION_CLOSED not in types  # suppressed: node dies with its edges


def test_metrics_throttled(cfg):
    p1 = make_proc(1, "a.exe", cpu=10.0, mem=100.0)
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1], []))
    engine.diff(snap1, topo1)

    # tiny delta -> no event
    p2 = make_proc(1, "a.exe", cpu=10.4, mem=100.5)
    snap2, topo2 = build(cfg, make_snap([p2], []))
    evs = engine.diff(snap2, topo2)
    assert not [e for e in evs if e.event_type == EVENT_PROCESS_METRICS_UPDATED]

    # large delta -> event with new values
    p3 = make_proc(1, "a.exe", cpu=10.0, mem=300.0)
    snap3, topo3 = build(cfg, make_snap([p3], []))
    evs = engine.diff(snap3, topo3)
    metrics = [e for e in evs if e.event_type == EVENT_PROCESS_METRICS_UPDATED]
    assert len(metrics) == 1
    assert metrics[0].metadata["memory_mb"] == 300.0
    assert metrics[0].metadata["cpu_percent"] == 10.0


def test_metrics_forced_interval(cfg):
    p1 = make_proc(1, "a.exe", cpu=10.0, mem=100.0)
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1], []))
    engine.diff(snap1, topo1)
    # identical metrics, but the engine is asked repeatedly; force interval is
    # 10s so a second identical tick must NOT emit
    snap2, topo2 = build(cfg, make_snap([p1], []))
    evs = engine.diff(snap2, topo2)
    assert not [e for e in evs if e.event_type == EVENT_PROCESS_METRICS_UPDATED]


def test_event_fields_are_complete(cfg):
    p1 = make_proc(1, "a.exe")
    ext = make_conn(pid=1, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    engine = DiffEngine(cfg)
    snap1, topo1 = build(cfg, make_snap([p1], []))
    engine.diff(snap1, topo1)
    snap2, topo2 = build(cfg, make_snap([p1], [ext]))
    evs = engine.diff(snap2, topo2)
    ev = evs[0]
    assert ev.event_id
    assert ev.event_type == EVENT_CONNECTION_OPENED
    assert ev.source and ev.target
    # ISO timestamp with timezone info
    parsed = datetime.fromisoformat(ev.timestamp)
    assert parsed.tzinfo is not None
    d = ev.to_dict()
    assert set(d) == {"event_id", "event_type", "source", "target", "timestamp", "metadata"}
