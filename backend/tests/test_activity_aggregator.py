"""ActivityAggregator tests: edge mapping, direction, batching, rates,
decay, bursts, process attribution, pruning, totals."""
import time

from app.config import Settings
from app.services.topology import TopologyEngine
from app.telemetry import ActivityAggregator
from app.telemetry.activity_aggregator import EVENT_TRAFFIC_BURST
from app.telemetry.base import NetworkActivityEvent

from conftest import make_conn, make_proc, make_snap


def _agg(cfg=None):
    agg = ActivityAggregator(cfg or Settings())
    agg._last_flush = time.time() - 0.2  # deterministic 200 ms window
    return agg


def _setup(procs, conns, cfg=None):
    snap = make_snap(procs, conns)
    topo = TopologyEngine(cfg or Settings()).build(snap)
    agg = _agg(cfg)
    agg.set_topology(snap, topo)
    return snap, topo, agg


def _ev(agg, pid, direction, lip, lport, rip, rport, size=4096):
    agg.record(NetworkActivityEvent(
        ts=time.time(), pid=pid, protocol="tcp", direction=direction,
        local_ip=lip, local_port=lport, remote_ip=rip, remote_port=rport,
        size=size,
    ))


def test_external_edge_direction_mapping():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443, size=4096)
    _ev(agg, 100, "IN", "192.168.1.5", 51000, "104.18.22.44", 443, size=2048)
    items, _, _ = agg.flush()

    assert len(items) == 1
    it = items[0]
    assert it["edge_id"] == eid
    assert it["fwd_bytes"] == 4096   # OUT: process -> remote
    assert it["rev_bytes"] == 2048   # IN:  remote -> process
    assert it["fwd_bps"] == round(4096 / 0.2, 1)
    assert it["rev_bps"] == round(2048 / 0.2, 1)
    assert it["duration_ms"] == 200


def test_localhost_canonical_reversal_direction():
    p1 = make_proc(pid=100, name="aaa.exe")
    p2 = make_proc(pid=200, name="bbb.exe")
    c1 = make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=1234, kind="localhost")
    c2 = make_conn(pid=200, lport=1234, rip="127.0.0.1", rport=53121, kind="localhost")
    _, topo, agg = _setup([p1, p2], [c1, c2])
    eid = next(iter(topo.edges))
    edge = topo.edges[eid]
    # canonical edge source is the lexicographically smaller stable id
    assert edge.source == p1.stable_id
    assert edge.target == p2.stable_id

    # p1 (edge source) sends -> travels src->tgt => fwd
    _ev(agg, 100, "OUT", "127.0.0.1", 53121, "127.0.0.1", 1234, size=3000)
    # p2 (edge target) sends -> travels tgt->src => rev
    _ev(agg, 200, "OUT", "127.0.0.1", 1234, "127.0.0.1", 53121, size=7000)
    items, _, _ = agg.flush()

    assert len(items) == 1
    assert items[0]["fwd_bytes"] == 3000
    assert items[0]["rev_bytes"] == 7000


def test_events_batched_per_edge():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p], [c])

    for size in (15_360, 20_480, 8_192):  # 15 KB + 20 KB + 8 KB
        _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443, size=size)
    items, _, _ = agg.flush()

    assert len(items) == 1
    assert items[0]["fwd_bytes"] == 44_032  # one item, summed, not 3 messages


def test_level_thresholds():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p], [c])
    base = ("192.168.1.5", 51000, "104.18.22.44", 443)

    agg._last_flush = time.time() - 0.2
    _ev(agg, 100, "OUT", *base, size=200_000)  # ~1 MB/s -> level 3
    assert agg.flush()[0][0]["level"] == 3

    agg._last_flush = time.time() - 0.2
    _ev(agg, 100, "OUT", *base, size=20_000)   # ~100 KB/s -> level 2
    assert agg.flush()[0][0]["level"] == 2

    agg._last_flush = time.time() - 0.2
    _ev(agg, 100, "OUT", *base, size=2_000)    # ~10 KB/s -> level 1
    assert agg.flush()[0][0]["level"] == 1


def test_unmapped_tuple_attributed_to_process_halo():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p], [c])

    # tuple not in the topology map (port changed between ticks)
    _ev(agg, 100, "OUT", "192.168.1.5", 51999, "104.18.22.44", 443, size=8192)
    _ev(agg, 100, "IN", "192.168.1.5", 51999, "104.18.22.44", 443, size=4096)
    items, _, node_items = agg.flush()

    assert items == []                       # no edge gets false activity
    assert node_items[0]["sid"] == p.stable_id
    assert node_items[0]["up_bps"] == round(8192 / 0.2, 1)
    assert node_items[0]["down_bps"] == round(4096 / 0.2, 1)


def test_unattributed_events_dropped_not_faked():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p], [c])

    _ev(agg, 999999, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443, size=8192)
    items, _, node_items = agg.flush()

    assert items == []
    assert node_items == []


def test_burst_detection_and_cooldown():
    cfg = Settings(telemetry_burst_bytes=200_000, telemetry_burst_cooldown_s=10.0)
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p], [c], cfg)
    base = ("192.168.1.5", 51000, "104.18.22.44", 443)

    _ev(agg, 100, "OUT", *base, size=150_000)
    _ev(agg, 100, "IN", *base, size=150_000)
    _, bursts, _ = agg.flush()
    assert len(bursts) == 1
    assert bursts[0].event_type == EVENT_TRAFFIC_BURST
    assert bursts[0].metadata["bytes"] == 300_000
    assert bursts[0].metadata["src_label"] == "chrome.exe"
    assert bursts[0].metadata["tgt_label"] == "104.18.22.44"

    # cooldown: same burst volume immediately after emits nothing
    _ev(agg, 100, "OUT", *base, size=300_000)
    _, bursts2, _ = agg.flush()
    assert bursts2 == []


def test_decay_states():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))
    _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443)
    agg.flush()

    assert agg.decay_stats(eid) == "ACTIVE"   # < 500 ms

    st = agg._edges[eid]
    st.last_activity = time.time() - 1.0
    assert agg.decay_stats(eid) == "RECENT"   # < 5 s

    st.last_activity = time.time() - 10.0
    assert agg.decay_stats(eid) == "IDLE"     # afterwards


def test_stale_edge_state_pruned():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))
    _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443)
    agg.flush()
    assert eid in agg._edges

    agg._edges[eid].last_seen = time.time() - 11.0
    agg.flush()  # empty window still prunes
    assert eid not in agg._edges


def test_totals_sum_captured_edges():
    p = make_proc(pid=100, name="chrome.exe")
    c1 = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                   rip="104.18.22.44", rport=443)
    c2 = make_conn(pid=100, lip="192.168.1.5", lport=52000,
                   rip="8.8.8.8", rport=53)
    _, _, agg = _setup([p], [c1, c2])

    _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443, size=4000)
    _ev(agg, 100, "IN", "192.168.1.5", 52000, "8.8.8.8", 53, size=6000)
    agg._last_flush = time.time() - 0.2
    agg.flush()

    t = agg.totals()
    assert t["down_bps"] == round(6000 / 0.2, 1)
    assert t["up_bps"] == round(4000 / 0.2, 1)


def test_totals_exclude_localhost_loopback():
    p1 = make_proc(pid=100, name="aaa.exe")
    p2 = make_proc(pid=200, name="bbb.exe")
    pe = make_proc(pid=300, name="chrome.exe")
    c1 = make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=1234, kind="localhost")
    c2 = make_conn(pid=200, lport=1234, rip="127.0.0.1", rport=53121, kind="localhost")
    ce = make_conn(pid=300, lip="192.168.1.5", lport=51000,
                   rip="104.18.22.44", rport=443)
    _, _, agg = _setup([p1, p2, pe], [c1, c2, ce])

    _ev(agg, 100, "OUT", "127.0.0.1", 53121, "127.0.0.1", 1234, size=9000)  # loopback
    _ev(agg, 300, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443, size=4000)  # upload
    _ev(agg, 300, "IN", "192.168.1.5", 51000, "104.18.22.44", 443, size=6000)   # download
    agg._last_flush = time.time() - 0.2
    agg.flush()

    t = agg.totals()
    assert t["down_bps"] == round(6000 / 0.2, 1)   # IN on external edge
    assert t["up_bps"] == round(4000 / 0.2, 1)     # OUT on external edge
    # loopback traffic is NOT counted in machine totals


def test_set_topology_preserves_existing_state():
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    snap, topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))
    _ev(agg, 100, "OUT", "192.168.1.5", 51000, "104.18.22.44", 443)
    agg.flush()
    before = agg._edges[eid].fwd_bps
    assert before > 0

    agg.set_topology(snap, topo)  # same topology, next tick
    assert agg._edges[eid].fwd_bps == before  # rates survive remapping
    assert agg._edges[eid].last_seen > 0
