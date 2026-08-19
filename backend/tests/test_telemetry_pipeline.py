"""TIER2 provider -> aggregator integration tests (the missing-wiring bug).

These tests prove the complete logical chain exactly as the runtime uses
it, and they are the regression guard for the v0.2.1 bug:

    fake ETW event
        -> EtwTcpipProvider queue        (via the real ETW callback)
        -> provider.drain()              (the runtime loop's call)
        -> aggregator.record_many()      (batch ingestion, one lock)
        -> aggregator.flush()            (200 ms window)
        -> non-zero edge activity

Rule: NO test in this file inserts events directly into the aggregator.
Every event enters through a provider (EtwTcpipProvider._on_event or
SyntheticActivityProvider.emit) and crosses the drain() seam — exactly the
wiring that was missing in the runtime loop.
"""
import time

import app.main as main
from app.config import Settings
from app.services.topology import TopologyEngine
from app.telemetry import ActivityAggregator, EtwTcpipProvider
from app.telemetry.synthetic import SyntheticActivityProvider

from conftest import make_conn, make_proc, make_snap


def _fake_etw(task, pid, size, lip, lport, rip, rport):
    """Build an ETW tufo. For RECEIVE* tasks the parser treats daddr/dport
    as the LOCAL end (the packet arrived at this machine)."""
    if task.upper().startswith("RECEIVE"):
        return (0, {
            "Task Name": task, "PID": pid, "size": size,
            "daddr": lip, "dport": lport, "saddr": rip, "sport": rport,
        })
    return (0, {
        "Task Name": task, "PID": pid, "size": size,
        "saddr": lip, "sport": lport, "daddr": rip, "dport": rport,
    })


def _setup(procs, conns, cfg=None):
    snap = make_snap(procs, conns)
    topo = TopologyEngine(cfg or Settings()).build(snap)
    agg = ActivityAggregator(cfg or Settings())
    agg._last_flush = time.time() - 0.2  # deterministic 200 ms window
    agg.set_topology(snap, topo)
    return topo, agg


# ------------------------------------------------------------- the wiring

def test_full_chain_fake_etw_to_edge_activity():
    """THE regression test for Bug #1: fake ETW -> provider -> drain ->
    record_many -> aggregator -> non-zero edge activity."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    # the ETW callback thread appends real parsed events to the queue
    prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                             "192.168.1.5", 51000, "104.18.22.44", 443))
    prov._on_event(_fake_etw("SendIPv4", 1234, 8192,
                             "192.168.1.5", 51000, "104.18.22.44", 443))
    prov._on_event(_fake_etw("ReceiveIPv4", 1234, 2048,
                             "192.168.1.5", 51000, "104.18.22.44", 443))

    # runtime loop: drain then batch-ingest (this was missing)
    events = prov.drain()
    assert len(events) == 3
    agg.record_many(events)

    items, _, _ = agg.flush()
    assert len(items) == 1
    it = items[0]
    assert it["edge_id"] == eid
    assert it["fwd_bytes"] == 4096 + 8192   # OUT events
    assert it["rev_bytes"] == 2048          # IN event
    assert it["fwd_bps"] > 0 and it["rev_bps"] > 0


def test_runtime_tick_wires_provider_to_aggregator(monkeypatch):
    """The actual runtime entry point (_telemetry_tick) must drain the
    provider into the aggregator — with REAL instances, not stubs."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                             "192.168.1.5", 51000, "104.18.22.44", 443))

    monkeypatch.setattr(main, "telemetry_provider", prov)
    monkeypatch.setattr(main, "aggregator", agg)

    items, _, _ = main._telemetry_tick()
    assert len(items) == 1 and items[0]["edge_id"] == eid
    assert items[0]["fwd_bytes"] == 4096
    # provider queue is empty afterwards; tick is idempotent
    assert main._telemetry_tick()[0] == []


def test_telemetry_tick_survives_provider_failure(monkeypatch):
    """A crashing provider must NEVER kill SYSTEM WATCH.

    Invariants proven here:
      - drain() failures are caught, the loop keeps iterating;
      - when the provider recovers, real telemetry flows again
        (events drained -> recorded -> activity batches emitted);
      - no unhandled exception terminates the monitoring loop.
    """
    import asyncio

    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])

    class FlakyProvider(EtwTcpipProvider):
        """Raises on the first drain calls, then behaves normally."""

        def __init__(self, fail_calls: int):
            super().__init__()
            self.fail_calls = fail_calls
            self.calls = 0

        def drain(self):
            self.calls += 1
            if self.calls <= self.fail_calls:
                raise RuntimeError("etw session exploded")
            return super().drain()

    prov = FlakyProvider(fail_calls=2)
    # queue 3 real parsed events (the recovery payload)
    for _ in range(3):
        prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                                 "192.168.1.5", 51000, "104.18.22.44", 443))

    monkeypatch.setattr(main, "telemetry_provider", prov)
    monkeypatch.setattr(main, "aggregator", agg)

    async def run_loop():
        task = asyncio.create_task(main._telemetry_loop())
        await asyncio.sleep(1.0)  # ~5 iterations: 2 failures, then recovery
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return True  # reached only if the loop never died

    assert asyncio.run(run_loop()) is True   # loop survived the failures
    assert prov.calls > 2                    # it kept polling the provider
    cnt = agg.counters()
    assert cnt["events_recorded"] == 3       # recovery delivered the events
    assert cnt["activity_batches_emitted"] >= 1  # telemetry flows again


# -------------------------------------------- modern TCB schema (Windows 11)

def _modern_conn(task, tcb, pid, lip, lport, rip, rport, pid_field="ProcessId"):
    """Real Windows 11 TCPIP identity event shape (empirically probed)."""
    return (0, {
        "Task Name": task, "Tcb": f"0x{tcb:016X}", pid_field: str(pid),
        "LocalAddress": f"{lip}:{lport}", "RemoteAddress": f"{rip}:{rport}",
    })


def _modern_xfer(task, tcb, size):
    field = "BytesSent" if task == "TCPDATATRANSFERSEND" else "NumBytes"
    return (0, {"Task Name": task, "Tcb": f"0x{tcb:016X}", field: str(size)})


def test_full_chain_modern_tcb_etw_to_edge_activity():
    """THE modern-schema regression: connection identity -> TCB map ->
    transfer events -> provider queue -> drain -> record_many -> aggregator
    -> mapped directional edge activity. Real loopback client/server pair."""
    p1 = make_proc(pid=100, name="client.exe")
    p2 = make_proc(pid=200, name="server.exe")
    c1 = make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=19735, kind="localhost")
    c2 = make_conn(pid=200, lport=19735, rip="127.0.0.1", rport=53121, kind="localhost")
    topo, agg = _setup([p1, p2], [c1, c2])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    # client-side connection established after session start
    prov._on_event(_modern_conn("TCPCONNECTTCBCOMPLETE", 0xABC1, 100,
                                "127.0.0.1", 53121, "127.0.0.1", 19735))
    # server-side accepted connection
    prov._on_event(_modern_conn("TCPACCEPTLISTENERCOMPLETE", 0xABC2, 200,
                                "127.0.0.1", 19735, "127.0.0.1", 53121))
    # real traffic both ways
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0xABC1, 4096))   # client -> server
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0xABC1, 8192))   # client -> server
    prov._on_event(_modern_xfer("TCPDATATRANSFERRECEIVE", 0xABC1, 2048))  # client <- server
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0xABC2, 2048))   # server -> client
    prov._on_event(_modern_xfer("TCPDATATRANSFERRECEIVE", 0xABC2, 4096 + 8192))  # server <- client

    events = prov.drain()
    assert len(events) == 5
    agg.record_many(events)

    items, _, _ = agg.flush()
    assert len(items) == 1
    it = items[0]
    assert it["edge_id"] == eid
    # fwd = source->target (client -> server) = client OUT + server IN
    assert it["fwd_bytes"] == 4096 + 8192 + (4096 + 8192)
    # rev = target->source (server -> client) = server OUT + client IN
    assert it["rev_bytes"] == 2048 + 2048
    assert it["fwd_bps"] > 0 and it["rev_bps"] > 0

    cnt = agg.counters()
    assert cnt["events_recorded"] == 5
    assert cnt["events_mapped_to_edges"] == 5
    assert cnt["activity_batches_emitted"] == 1
    prov_cnt = prov.counters()
    assert prov_cnt["tcb_mappings_created"] == 2
    assert prov_cnt["tcb_lookup_hits"] == 5
    assert prov_cnt["tcb_lookup_misses"] == 0


def test_full_chain_modern_rundown_bootstrap_pre_existing():
    """Connections that existed BEFORE the backend started must be learned
    from TcpConnectionRundown and immediately attribute traffic."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    prov._on_event(_modern_conn("TCPCONNECTIONRUNDOWN", 0xBEEF, 1234,
                                "192.168.1.5", 51000, "104.18.22.44", 443,
                                pid_field="Pid"))
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0xBEEF, 1500))
    prov._on_event(_modern_xfer("TCPDATATRANSFERRECEIVE", 0xBEEF, 300))

    agg.record_many(prov.drain())
    items, _, _ = agg.flush()
    assert len(items) == 1 and items[0]["edge_id"] == eid
    assert items[0]["fwd_bytes"] == 1500 and items[0]["rev_bytes"] == 300
    assert prov.counters()["tcb_lookup_hits"] == 2


# ------------------------------------------------- queue health + counters

def test_provider_queue_bounded_with_drop_count():
    prov = EtwTcpipProvider()
    for i in range(20_005):
        prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                                 "192.168.1.5", 51000, "104.18.22.44", 443))
    assert prov.queue_depth() == 20_000          # bounded memory
    cnt = prov.counters()
    assert cnt["events_received"] == 20_005
    assert cnt["events_dropped"] == 5            # drops are countable
    assert len(prov.drain()) == 20_000
    assert prov.counters()["events_drained"] == 20_000


def test_record_many_batches_under_one_lock():
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, agg = _setup([p], [c])

    prov = EtwTcpipProvider()
    for _ in range(5000):
        prov._on_event(_fake_etw("SendIPv4", 1234, 1500,
                                 "192.168.1.5", 51000, "104.18.22.44", 443))
    agg.record_many(prov.drain())

    items, _, _ = agg.flush()
    assert len(items) == 1                       # one item, summed
    assert items[0]["fwd_bytes"] == 5000 * 1500
    assert agg.counters()["events_recorded"] == 5000


def test_aggregator_attribution_counters():
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.5", lport=51000,
                  rip="104.18.22.44", rport=443)
    _, agg = _setup([p], [c])

    prov = EtwTcpipProvider()
    prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                             "192.168.1.5", 51000, "104.18.22.44", 443))   # -> edge
    prov._on_event(_fake_etw("SendIPv4", 1234, 4096,
                             "192.168.1.5", 51999, "104.18.22.44", 443))   # -> node halo
    prov._on_event(_fake_etw("SendIPv4", 999999, 4096,
                             "192.168.1.5", 51000, "104.18.22.44", 443))   # unattributed
    agg.record_many(prov.drain())
    items, _, node_items = agg.flush()

    assert len(items) == 1 and len(node_items) == 1
    cnt = agg.counters()
    assert cnt["events_mapped_to_edges"] == 1
    assert cnt["events_mapped_to_nodes"] == 1
    assert cnt["events_unattributed"] == 1
    assert cnt["activity_batches_emitted"] == 1
    lb = cnt["last_batch"]
    assert lb["fwd_bytes"] == 4096
    assert lb["rev_bytes"] == 0


# --------------------- wildcard local-IP fallback (v0.2.2 final bug)

def test_wildcard_local_ip_attributed_to_correct_edge():
    """THE v0.2.2 regression: real Windows ETW reports outbound client
    sockets with local address '0.0.0.0' while psutil topology carries the
    resolved source IP (192.168.x.x). The event must map to the CORRECT
    edge via the unique wildcard fallback — and must NOT fall through to
    node-only attribution."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.50", lport=53000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    # real Windows 11 shape: the identity event itself carries the
    # wildcard local address (empirically observed in fresh sessions)
    prov._on_event(_modern_conn("TCPCONNECTIONRUNDOWN", 0x0A0A, 1234,
                                "0.0.0.0", 53000, "104.18.22.44", 443,
                                pid_field="Pid"))
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0x0A0A, 4096))
    prov._on_event(_modern_xfer("TCPDATATRANSFERRECEIVE", 0x0A0A, 2048))

    agg.record_many(prov.drain())
    items, _, node_items = agg.flush()

    assert len(items) == 1
    assert items[0]["edge_id"] == eid
    assert items[0]["fwd_bytes"] == 4096      # OUT -> owner_is_src -> fwd
    assert items[0]["rev_bytes"] == 2048      # IN  -> owner_is_src -> rev
    assert node_items == []                   # NOT degraded to node-only
    cnt = agg.counters()
    assert cnt["events_mapped_to_edges"] == 2
    assert cnt["events_mapped_to_nodes"] == 0
    assert cnt["exact_lookup_hits"] == 0
    assert cnt["wildcard_lookup_hits"] == 2
    assert cnt["wildcard_lookup_misses"] == 0
    assert cnt["wildcard_lookup_ambiguous"] == 0


def test_wildcard_local_ip_v6_unique_fallback():
    """IPv6 variant: ETW local address '::' with a resolved v6 topology
    address — the same unique wildcard fallback must attribute safely."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="2a02:214c:8000:100::45", lport=53000,
                  rip="2a06:98c1:3120::1", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    # bracketed '[::]:port' is the real ETW v6 wildcard shape
    prov._on_event((0, {
        "Task Name": "TCPCONNECTIONRUNDOWN", "Tcb": "0x0000000000000B0B",
        "Pid": "1234",
        "LocalAddress": "[::]:53000", "RemoteAddress": "[2a06:98c1:3120::1]:443",
    }))
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0x0B0B, 8192))

    agg.record_many(prov.drain())
    items, _, node_items = agg.flush()

    assert len(items) == 1 and items[0]["edge_id"] == eid
    assert items[0]["fwd_bytes"] == 8192
    assert node_items == []
    cnt = agg.counters()
    assert cnt["events_mapped_to_edges"] == 1
    assert cnt["wildcard_lookup_hits"] == 1
    assert cnt["wildcard_lookup_misses"] == 0
    assert cnt["wildcard_lookup_ambiguous"] == 0


def test_ambiguous_wildcard_fallback_never_guesses():
    """Two topology edges share the wildcard fallback identity (pid,
    local_port, remote_ip, remote_port) with different resolved local IPs.
    The aggregator must NOT pick one arbitrarily: no edge mapping, the
    ambiguity counter increments, and the event stays process-attributed."""
    from app.models.entities import Stats, TEdge, TNode, TopologyResult

    p1 = make_proc(pid=1234, name="chrome.exe")
    c1 = make_conn(pid=1234, lip="192.168.1.50", lport=53000,
                   rip="104.18.22.44", rport=443)
    c2 = make_conn(pid=1234, lip="10.1.2.3", lport=53000,
                   rip="104.18.22.44", rport=443)
    snap = make_snap([p1], [c1, c2])
    n_src = p1.stable_id
    n_tgt = "ext:104.18.22.44"
    topo = TopologyResult(
        ts=100.0,
        nodes={
            n_src: TNode(id=n_src, kind="process", label="chrome.exe"),
            n_tgt: TNode(id=n_tgt, kind="external", label="104.18.22.44"),
        },
        edges={
            "e1": TEdge(id="e1", source=n_src, target=n_tgt, kind="EXTERNAL",
                        proto="tcp", ports=[53000], active=True),
            "e2": TEdge(id="e2", source=n_src, target=n_tgt, kind="EXTERNAL",
                        proto="tcp", ports=[53000], active=True),
        },
        conn_targets={
            c1.key: (n_tgt, "EXTERNAL", "e1"),
            c2.key: (n_tgt, "EXTERNAL", "e2"),
        },
        stats=Stats(),
    )
    agg = ActivityAggregator(Settings())
    agg._last_flush = time.time() - 0.2
    agg.set_topology(snap, topo)

    prov = EtwTcpipProvider()
    prov._on_event(_modern_conn("TCPCONNECTIONRUNDOWN", 0x0C0C, 1234,
                                "0.0.0.0", 53000, "104.18.22.44", 443,
                                pid_field="Pid"))
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0x0C0C, 4096))

    agg.record_many(prov.drain())
    items, _, node_items = agg.flush()

    assert items == []                    # NO arbitrary edge selection
    assert len(node_items) == 1           # process-attributed, not lost
    cnt = agg.counters()
    assert cnt["events_mapped_to_edges"] == 0
    assert cnt["events_mapped_to_nodes"] == 1
    assert cnt["wildcard_lookup_ambiguous"] == 1
    assert cnt["wildcard_lookup_hits"] == 0
    assert cnt["wildcard_lookup_misses"] == 0


def test_non_wildcard_local_ip_never_uses_fallback():
    """A non-wildcard ETW local address that misses the exact key must NOT
    trigger the wildcard fallback even when the fallback identity exists —
    it could belong to a DIFFERENT edge."""
    p = make_proc(pid=1234, name="chrome.exe")
    c = make_conn(pid=1234, lip="192.168.1.50", lport=53000,
                  rip="104.18.22.44", rport=443)
    topo, agg = _setup([p], [c])
    eid = next(iter(topo.edges))

    prov = EtwTcpipProvider()
    prov._on_event(_modern_conn("TCPCONNECTIONRUNDOWN", 0x0D0D, 1234,
                                "192.168.1.50", 53000, "104.18.22.44", 443,
                                pid_field="Pid"))   # exact -> edge
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0x0D0D, 2048))
    # same fallback identity but a RESOLVED local IP matching nothing
    prov._on_event(_modern_conn("TCPCONNECTIONRUNDOWN", 0x0D0E, 1234,
                                "192.168.1.99", 53000, "104.18.22.44", 443,
                                pid_field="Pid"))
    prov._on_event(_modern_xfer("TCPDATATRANSFERSEND", 0x0D0E, 1024))

    agg.record_many(prov.drain())
    items, _, node_items = agg.flush()

    assert len(items) == 1 and items[0]["edge_id"] == eid
    assert items[0]["fwd_bytes"] == 2048      # only the exact-matching bytes
    assert len(node_items) == 1               # the 1024 went to the node halo
    cnt = agg.counters()
    assert cnt["exact_lookup_hits"] == 1
    assert cnt["wildcard_lookup_hits"] == 0
    assert cnt["wildcard_lookup_misses"] == 0
    assert cnt["wildcard_lookup_ambiguous"] == 0


# ------------------------------------- synthetic provider (LOGICAL TIER2)

def test_synthetic_provider_logical_tier2_chain():
    """Synthetic events (test-only provider) cross the same drain seam and
    must produce directional edge activity — the acceptance path."""
    p1 = make_proc(pid=100, name="aaa.exe")
    p2 = make_proc(pid=200, name="bbb.exe")
    c1 = make_conn(pid=100, lport=53121, rip="127.0.0.1", rport=19735, kind="localhost")
    c2 = make_conn(pid=200, lport=19735, rip="127.0.0.1", rport=53121, kind="localhost")
    topo, agg = _setup([p1, p2], [c1, c2])
    eid = next(iter(topo.edges))

    prov = SyntheticActivityProvider(target_port=19735)
    assert prov.start() is True
    try:
        # the seam tests use: emit (like the generator thread) -> drain
        prov._fabricate(100, "127.0.0.1", 53121, "127.0.0.1", 19735)
        prov._fabricate(200, "127.0.0.1", 19735, "127.0.0.1", 53121)
        events = prov.drain()
        assert len(events) == 4
        agg.record_many(events)
        items, _, _ = agg.flush()
        assert len(items) == 1
        it = items[0]
        assert it["edge_id"] == eid
        assert it["fwd_bytes"] > 0 and it["rev_bytes"] > 0   # both directions
    finally:
        prov.stop()
    cap = prov.capability()
    assert cap.level == "TIER2"
    assert "SYNTHETIC" in cap.source  # never mistakable for real ETW
