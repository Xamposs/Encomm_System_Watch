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
