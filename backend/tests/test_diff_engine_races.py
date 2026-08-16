"""Diff-engine races: pid attribution flips and transient process misses.

Real Windows behavior: sockets created during process startup can briefly be
attributed to pid=None (SYSTEM) by psutil, then flip to the real PID; and
psutil.process_iter can transiently miss a process that is mid-startup.
Both must NOT swallow the real CONNECTION_OPENED events for the pair edge.
"""
from app.config import Settings
from app.services.diff_engine import (
    EVENT_CONNECTION_CLOSED,
    EVENT_CONNECTION_OPENED,
    DiffEngine,
)
from app.services.topology import TopologyEngine

from conftest import make_conn, make_proc, make_snap


def _tick(engine, procs, conns):
    snap = make_snap(procs, conns)
    topo = TopologyEngine(engine.cfg).build(snap)
    events = engine.diff(snap, topo)
    return events


def _types(events):
    return [e.event_type for e in events]


def _pair_conns(server_pid, client_pid):
    c1 = make_conn(pid=server_pid, lport=19734, rip="127.0.0.1", rport=54824, kind="localhost")
    c2 = make_conn(pid=client_pid, lport=54824, rip="127.0.0.1", rport=19734, kind="localhost")
    return [c1, c2]


def test_pid_flip_emits_pair_edge_open():
    """Sockets first seen as pid=None (SYSTEM), then attributed to real PIDs.

    The old key (pid=None) disappears and the new key appears; the pair edge
    open must be announced even though the socket 'existed' before.
    """
    engine = DiffEngine(Settings())
    # tick 1: sockets attributed to SYSTEM (process mid-startup)
    _tick(engine, [], _pair_conns(None, None))
    # tick 2: same sockets now owned by real processes
    server = make_proc(pid=100, name="server.exe")
    client = make_proc(pid=200, name="client.exe")
    events = _tick(engine, [server, client], _pair_conns(100, 200))

    opens = [e for e in events if e.event_type == EVENT_CONNECTION_OPENED]
    assert len(opens) == 2, f"expected 2 pair-edge opens, got {_types(events)}"
    kinds = {e.metadata["kind"] for e in opens}
    assert kinds == {"LOCALHOST"}
    # both conns announce the same canonical pair edge
    eids = {e.metadata["edge_id"] for e in opens}
    assert len(eids) == 1
    assert "LOCALHOST" in next(iter(eids))


def test_transient_process_miss_does_not_suppress_opens():
    """Owner transiently missing from the process scan (mid-startup).

    The sockets exist with real pids in the snapshot, so the opens must be
    emitted even though the owner sid is in `stopped` this tick.
    """
    engine = DiffEngine(Settings())
    server = make_proc(pid=100, name="server.exe")
    client = make_proc(pid=200, name="client.exe")
    # tick 1: processes visible, sockets pid=None (startup race)
    _tick(engine, [server, client], _pair_conns(None, None))
    # tick 2: sockets get real pids, BUT the processes vanish from the scan
    events = _tick(engine, [], _pair_conns(100, 200))

    opens = [e for e in events if e.event_type == EVENT_CONNECTION_OPENED]
    assert len(opens) == 2, f"expected 2 pair-edge opens, got {_types(events)}"
    # the SYSTEM-key closes from tick 1 must also be announced
    closes = [e for e in events if e.event_type == EVENT_CONNECTION_CLOSED]
    assert len(closes) == 2, f"expected 2 old-key closes, got {_types(events)}"
    for c in closes:
        assert c.metadata["kind"] != "LOCALHOST" or "sys:windows" in (c.source, c.target or "")


def test_genuine_stop_still_suppresses_closes():
    """Owner truly gone AND its sockets gone -> close events suppressed.

    The node itself disappears with its edges (existing contract).
    """
    engine = DiffEngine(Settings())
    server = make_proc(pid=100, name="server.exe")
    client = make_proc(pid=200, name="client.exe")
    _tick(engine, [server, client], _pair_conns(100, 200))
    _tick(engine, [server, client], _pair_conns(100, 200))  # settle
    events = _tick(engine, [], [])  # both processes + sockets gone

    closes = [e for e in events if e.event_type == EVENT_CONNECTION_CLOSED]
    assert closes == [], f"expected suppressed closes, got {_types(events)}"
    # the process stops are still announced
    assert "PROCESS_STOPPED" in _types(events)
