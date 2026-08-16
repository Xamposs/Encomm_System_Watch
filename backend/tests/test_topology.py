"""Topology engine tests: node/edge generation, pairing, caps, stats."""
from app.services.topology import TopologyEngine, edge_id, SYSTEM_NODE_ID

from conftest import make_conn, make_proc, make_snap


def test_localhost_pair_creates_process_to_process_edge(cfg):
    p1 = make_proc(pid=100, name="hermes.exe")
    p2 = make_proc(pid=200, name="lmstudio.exe")
    c1 = make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=1234, kind="localhost")
    c2 = make_conn(pid=200, lport=1234, rip="127.0.0.1", rport=53121, kind="localhost")
    snap = make_snap([p1, p2], [c1, c2])
    topo = TopologyEngine(cfg).build(snap)

    eid = edge_id(p1.stable_id, p2.stable_id, "LOCALHOST")
    assert eid in topo.edges
    e = topo.edges[eid]
    assert e.kind == "LOCALHOST"
    assert e.directed is False
    assert e.active is True
    assert set(e.ports) == {53121, 1234}
    # both connections resolve to the same edge
    t1 = topo.conn_targets[c1.key]
    t2 = topo.conn_targets[c2.key]
    assert t1[2] == t2[2] == eid


def test_external_connection_creates_external_node(cfg):
    p = make_proc(pid=100, name="chrome.exe")
    c = make_conn(pid=100, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    snap = make_snap([p], [c])
    topo = TopologyEngine(cfg).build(snap)

    assert "ext:104.18.22.44" in topo.nodes
    ext = topo.nodes["ext:104.18.22.44"]
    assert ext.kind == "EXTERNAL_ENDPOINT"
    assert ext.label == "104.18.22.44"
    eid = edge_id(p.stable_id, "ext:104.18.22.44", "EXTERNAL")
    assert eid in topo.edges
    e = topo.edges[eid]
    assert e.directed is True
    assert e.ports == [443]


def test_listening_socket_creates_listen_node(cfg):
    p = make_proc(pid=100, name="python.exe")
    c = make_conn(pid=100, lport=8000, state="LISTEN", kind="listening")
    snap = make_snap([p], [c])
    topo = TopologyEngine(cfg).build(snap)

    nid = "lst:tcp:127.0.0.1:8000"
    assert nid in topo.nodes
    assert topo.nodes[nid].label == ":8000"
    eid = edge_id(p.stable_id, nid, "LISTEN")
    assert eid in topo.edges
    assert topo.edges[eid].kind == "LISTEN"
    # process node enriched
    pnode = topo.nodes[p.stable_id]
    assert pnode.data["conn_count"] == 1
    assert pnode.data["listening_ports"] == [8000]


def test_external_node_cap_aggregates_overflow(small_cfg):
    p = make_proc(pid=100, name="chrome.exe")
    conns = [
        make_conn(pid=100, lip="192.168.1.5", lport=51000 + i,
                  rip=f"10.0.0.{i}", rport=443)
        for i in range(1, 5)
    ]
    snap = make_snap([p], conns)
    topo = TopologyEngine(small_cfg).build(snap)  # cap = 2 external nodes

    assert "ext:10.0.0.1" in topo.nodes
    assert "ext:10.0.0.2" in topo.nodes
    assert "ext:10.0.0.3" not in topo.nodes
    agg = f"ext-agg:{p.stable_id}"
    assert agg in topo.nodes
    assert topo.nodes[agg].label == "EXTERNAL x2"
    # overflow conns resolve to the aggregated edge
    for i in (3, 4):
        c = conns[i - 1]
        tgt, kind, eid = topo.conn_targets[c.key]
        assert kind == "EXTERNAL"
        assert eid == edge_id(p.stable_id, agg, "EXTERNAL")


def test_unpaired_localhost_gets_local_endpoint_node(cfg):
    p = make_proc(pid=100, name="client.exe")
    c = make_conn(pid=100, lport=40000, rip="127.0.0.1", rport=9999, kind="localhost")
    snap = make_snap([p], [c])  # no reverse side -> unpaired
    topo = TopologyEngine(cfg).build(snap)

    nid = "loc:tcp:127.0.0.1:9999"
    assert nid in topo.nodes
    # LOCALHOST edges are canonicalized (sorted endpoint order)
    src, tgt = sorted([p.stable_id, nid])
    eid = edge_id(src, tgt, "LOCALHOST")
    assert eid in topo.edges
    assert topo.edges[eid].source == src
    assert topo.edges[eid].target == tgt


def test_system_owned_socket_attaches_to_system_node(cfg):
    p = make_proc(pid=100, name="x.exe")
    c = make_conn(pid=None, lport=445, state="LISTEN", kind="listening")
    snap = make_snap([p], [c], owner_map={c.key: None})
    topo = TopologyEngine(cfg).build(snap)

    eid = edge_id(SYSTEM_NODE_ID, "lst:tcp:127.0.0.1:445", "LISTEN")
    assert eid in topo.edges
    assert topo.edges[eid].source == SYSTEM_NODE_ID


def test_stats_counts(cfg):
    p1 = make_proc(pid=100, name="a.exe")
    p2 = make_proc(pid=200, name="b.exe")
    conns = [
        make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=1234, kind="localhost"),
        make_conn(pid=200, lport=1234, rip="127.0.0.1", rport=53121, kind="localhost"),
        make_conn(pid=100, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443),
        make_conn(pid=100, lip="192.168.1.5", lport=51001, rip="104.18.22.44", rport=443),
        make_conn(pid=100, lport=8000, state="LISTEN", kind="listening"),
        make_conn(pid=100, lip="192.168.1.5", lport=51002, rip="8.8.8.8", rport=53,
                  proto="udp", state=None, kind="external"),
        make_conn(pid=100, lip="192.168.1.5", lport=51003, rip="9.9.9.9", rport=80,
                  state="CLOSE_WAIT", kind="external"),  # not established
    ]
    snap = make_snap([p1, p2], conns)
    topo = TopologyEngine(cfg).build(snap)

    assert topo.stats.processes == 2
    assert topo.stats.listening == 1
    # established tcp: 2 localhost sockets + 2 external; + 1 udp = 5; CLOSE_WAIT excluded
    assert topo.stats.active_conns == 5
    # two tcp conns to the same external IP aggregate into one edge with 2 ports
    eid = edge_id(p1.stable_id, "ext:104.18.22.44", "EXTERNAL")
    assert set(topo.edges[eid].ports) == {443, 443}


def test_aggregated_edge_deduplicates_ports(cfg):
    p = make_proc(pid=100, name="a.exe")
    c1 = make_conn(pid=100, lip="192.168.1.5", lport=51000, rip="104.18.22.44", rport=443)
    c2 = make_conn(pid=100, lip="192.168.1.5", lport=51001, rip="104.18.22.44", rport=443)
    snap = make_snap([p], [c1, c2])
    topo = TopologyEngine(cfg).build(snap)
    eid = edge_id(p.stable_id, "ext:104.18.22.44", "EXTERNAL")
    assert topo.edges[eid].ports == [443]
    assert topo.nodes[p.stable_id].data["conn_count"] == 1  # one edge, not two
