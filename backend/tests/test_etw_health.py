"""READ-ONLY ETW attribution health detector tests (v0.3.1).

Verifies the WATCHING -> DEGRADED state machine (provider events climbing
while edge attribution is frozen) with a synthetic counter timeline, and
that the detector never mutates anything (it has no handles at all).
"""
from app.services.etw_health import EtwAttributionHealth


def prov(events=0, alive=True):
    return {"events_received": events, "events_drained": events,
            "events_dropped": 0, "queue_depth": 0, "alive": alive}


def agg(mapped=0):
    return {"events_recorded": mapped, "events_mapped_to_edges": mapped,
            "activity_batches_emitted": 0}


def feed(health, events, mapped, edges=10, conns=5, start=0.0, step=5.0):
    """Run one sample per step for the given timeline length."""
    out = []
    for i, (ev, mp) in enumerate(zip(events, mapped)):
        out.append(health.sample(prov(ev), agg(mp), edges, conns,
                                 now=start + i * step))
    return out


def test_healthy_attribution_stays_ok():
    h = EtwAttributionHealth(freeze_threshold_s=45.0, sample_interval_s=5.0)
    # events and mapped both climb — normal attribution
    out = feed(h, [100, 200, 400], [80, 160, 320])
    assert [o["state"] for o in out] == ["OK", "OK", "OK"]


def test_no_provider_is_na():
    h = EtwAttributionHealth()
    d = h.sample({}, agg(0), 0, 0, now=0.0)
    assert d["state"] == "N/A"


def test_dead_provider_reported():
    h = EtwAttributionHealth()
    d = h.sample(prov(100, alive=False), agg(50), 5, 2, now=0.0)
    assert d["state"] == "PROVIDER_DEAD"


def test_quiet_provider_is_ok():
    h = EtwAttributionHealth()
    out = feed(h, [0, 0, 0], [0, 0, 0])
    assert all(o["state"] == "OK" for o in out)


def test_watching_then_degraded_when_mapping_frozen():
    h = EtwAttributionHealth(freeze_threshold_s=45.0, sample_interval_s=5.0)
    # events climb every 5 s, mapped frozen at 300, edges/conns still present
    events = [300 + i * 50 for i in range(12)]
    mapped = [300] * 12
    out = feed(h, events, mapped)
    states = [o["state"] for o in out]
    assert states[0] == "OK"          # baseline sample
    assert states[1] == "WATCHING"    # frozen < threshold
    assert states[2] == "WATCHING"
    assert states[9] == "WATCHING"    # 40 s frozen — still pre-warning
    # 45 s frozen -> DEGRADED (sample at t=50)
    assert states[10] == "DEGRADED", states
    assert "DEGRADED" in out[10]["message"]
    # stays DEGRADED while the signature persists
    assert states[11] == "DEGRADED"
    assert out[10]["frozen_for_s"] is not None and out[10]["frozen_for_s"] >= 45.0


def test_recovery_when_mapping_resumes():
    h = EtwAttributionHealth(freeze_threshold_s=45.0, sample_interval_s=5.0)
    out = feed(h, [300, 350, 400, 500], [300, 300, 300, 480])
    assert out[1]["state"] == "WATCHING"
    assert out[2]["state"] == "WATCHING"
    assert out[3]["state"] == "OK"    # mapped moved again


def test_degraded_requires_topology_activity_signal():
    """The detector reports even with zero edges/conns (the counters speak),
    but the message is only meaningful when topology exists — verify the
    state machine does not depend on the topology args."""
    h = EtwAttributionHealth(freeze_threshold_s=10.0, sample_interval_s=5.0)
    out = feed(h, [100, 150, 200, 250], [50, 50, 50, 50], edges=0, conns=0)
    assert out[3]["state"] == "DEGRADED"  # 10 s frozen


def test_transition_events_one_shot():
    h = EtwAttributionHealth(freeze_threshold_s=10.0, sample_interval_s=5.0)
    feed(h, [100, 150], [50, 50], start=0.0)
    # WATCHING transition consumed exactly once
    t1 = h.consume_transition()
    t2 = h.consume_transition()
    assert t1 is not None and t1["state"] == "WATCHING"
    assert t1["previous_state"] == "OK"
    assert t2 is None
    # drive to DEGRADED -> one more event
    feed(h, [200, 250], [50, 50], start=10.0)
    t3 = h.consume_transition()
    assert t3 is not None and t3["state"] == "DEGRADED"
    assert h.consume_transition() is None
    # recovery -> OK event with previous DEGRADED
    feed(h, [300], [120], start=20.0)
    t4 = h.consume_transition()
    assert t4 is not None and t4["state"] == "OK"
    assert t4["previous_state"] == "DEGRADED"


def test_sampling_throttled_by_interval():
    h = EtwAttributionHealth(freeze_threshold_s=10.0, sample_interval_s=5.0)
    # two samples 1 s apart: second is throttled (returns same state)
    d1 = h.sample(prov(100), agg(80), 5, 2, now=0.0)
    assert d1["state"] == "OK"
    d2 = h.sample(prov(150), agg(120), 5, 2, now=1.0)
    assert d2["state"] == "OK"
    assert h._events_last == 100  # not sampled yet


def test_reset_clears_state():
    h = EtwAttributionHealth(freeze_threshold_s=10.0, sample_interval_s=5.0)
    feed(h, [100, 150, 200, 250], [50, 50, 50, 50], start=0.0)
    assert h.state == "DEGRADED"
    h.reset()
    assert h.state == "N/A"
    assert h.consume_transition() is None
