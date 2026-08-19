"""main.py telemetry integration tests: stats net block, capability
reporting, REST endpoint."""
import app.main as main
from app.telemetry import Capability


def _reset_state():
    main._state["telemetry"] = None
    main._state["adapter"] = None


def test_stats_tier2_reports_captured(monkeypatch):
    _reset_state()
    main._state["telemetry"] = Capability(
        level="TIER2", source="WINDOWS ETW (Microsoft-Windows-TCPIP)",
    ).to_dict()
    main._state["adapter"] = {"down_bps": 5_000_000.0, "up_bps": 800_000.0}
    monkeypatch.setattr(main.aggregator, "totals", lambda: {"down_bps": 12_000.5, "up_bps": 2_100.0})

    stats = main._stats_dict()
    net = stats["net"]
    assert net["source"] == "CAPTURED"
    assert net["down_bps"] == 12_000.5
    assert net["up_bps"] == 2_100.0
    # both measurements present and distinguishable
    assert net["adapter_down_bps"] == 5_000_000.0
    assert net["adapter_up_bps"] == 800_000.0
    assert stats["telemetry"]["level"] == "TIER2"


def test_stats_tier0_with_adapter_reports_adapter_totals(monkeypatch):
    _reset_state()
    main._state["telemetry"] = Capability(
        level="TIER0", source="NONE", elevation_required=True,
    ).to_dict()
    main._state["adapter"] = {"down_bps": 3.5, "up_bps": 1.25}
    monkeypatch.setattr(main.aggregator, "totals", lambda: {"down_bps": 0.0, "up_bps": 0.0})

    stats = main._stats_dict()
    net = stats["net"]
    assert net["source"] == "ADAPTER_TOTALS"
    assert net["down_bps"] == 3.5
    assert net["up_bps"] == 1.25
    assert stats["telemetry"]["elevation_required"] is True


def test_stats_no_adapter_net_is_none_not_zeroes():
    _reset_state()
    main._state["telemetry"] = Capability(level="TIER0", source="NONE").to_dict()
    main._state["adapter"] = None
    stats = main._stats_dict()
    assert stats["net"] is None  # hide rather than fake zeroes


def test_api_telemetry_endpoint(client):
    _reset_state()
    r = client.get("/api/telemetry")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"level", "source", "detail", "elevation_required",
                         "enabled", "readiness"}


def test_api_telemetry_debug_endpoint(client):
    _reset_state()
    r = client.get("/api/telemetry/debug")
    assert r.status_code == 200
    body = r.json()
    assert body["telemetry"]["level"] in ("TIER0", "TIER2")
    assert body["provider"]["queue_depth"] >= 0
    assert body["aggregator"]["events_recorded"] == 0
    assert body["aggregator"]["events_mapped_to_edges"] == 0
    assert body["aggregator"]["events_unattributed"] == 0
    assert body["aggregator"]["activity_batches_emitted"] == 0
    assert "last_batch" in body["aggregator"]
    assert body["edges_tracked"] == 0
    assert body["processes_tracked"] == 0
