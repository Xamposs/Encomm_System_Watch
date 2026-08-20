"""Phase 17 — REAL AI telemetry: schema, privacy gate, bounded buffer,
Hermes gateway provider (real adapter), OTEL seam, fixture provider,
registry. Every test here is deterministic — real-machine integration is
covered by acceptance.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.ai_telemetry.buffer import (
    CAP_ACTIVE_TRACES,
    CAP_EVENT_HISTORY,
    CAP_RECENT_SPANS,
    AiTelemetryBuffer,
)
from app.ai_telemetry.fixture_provider import FixtureAiProvider
from app.ai_telemetry.hermes_provider import HermesGatewayProvider
from app.ai_telemetry.models import (
    AITelemetryEvent,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    EVENT_AI_ERROR,
    EVENT_MCP_CALL_FINISHED,
    EVENT_MCP_CALL_STARTED,
    EVENT_MODEL_REQUEST_FINISHED,
    EVENT_MODEL_REQUEST_STARTED,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
    contains_private_content,
)
from app.ai_telemetry.otel_provider import normalize_otel_span
from app.ai_telemetry.registry import AiTelemetryRegistry
from app.ai_telemetry.base import (
    STATE_ACTIVE,
    STATE_AVAILABLE_NO_DATA,
    STATE_DEGRADED,
    STATE_UNAVAILABLE,
)

# ================================================================ models


def test_finalize_fills_identity_and_derives_total_tokens():
    ev = AITelemetryEvent(
        event_type=EVENT_MODEL_REQUEST_FINISHED, source="test",
        input_tokens=100, output_tokens=50,
    ).finalize(now=1_700_000_000.0)
    assert ev.timestamp is not None
    assert ev.event_id.startswith("1700000000")
    assert ev.total_tokens == 150
    assert ev.tps is None  # no duration evidence -> no TPS


def test_tps_derived_only_from_real_tokens_and_duration():
    ev = AITelemetryEvent(
        event_type=EVENT_MODEL_REQUEST_FINISHED, source="test",
        output_tokens=80, duration_ms=800.0,
    ).finalize()
    assert ev.tps == 100.0


def test_to_dict_omits_absent_fields():
    ev = AITelemetryEvent(event_type=EVENT_AGENT_RUN_STARTED, source="s",
                          agent_id="a1").finalize()
    d = ev.to_dict()
    assert d["agent_id"] == "a1"
    assert "model_id" not in d
    assert "tps" not in d
    assert d["test_only"] is False


def test_private_content_gate_rejects_content_keys():
    assert contains_private_content({"prompt": "hello"}) is not None
    assert contains_private_content({"response": "hi"}) is not None
    assert contains_private_content({"reasoning": "think"}) is not None
    assert contains_private_content({"messages": [{"role": "user"}]}) is not None
    assert contains_private_content({"api_key": "x"}) is not None
    assert contains_private_content({"nested": {"content": "x"}}) is not None
    assert contains_private_content({"list": [{"text": "x"}]}) is not None
    assert contains_private_content({"authorization": "Bearer xyz"}) is not None


def test_private_content_gate_rejects_credential_shapes():
    assert contains_private_content({"meta": "Bearer abc123"}) is not None
    assert contains_private_content({"meta": "sk-abcdef12345678901234"}) is not None
    assert contains_private_content({"meta": "password=supersecret"}) is not None
    assert contains_private_content({"meta": "api_key: supersecret"}) is not None


def test_private_content_gate_allows_metadata():
    assert contains_private_content({"duration_ms": 12.5, "profile": "x"}) is None
    assert contains_private_content({"token_count": 12}) is None  # counts are metadata
    assert contains_private_content({"tool_name": "filesystem"}) is None
    assert contains_private_content({}) is None
    assert contains_private_content(None) is None


# ================================================================ buffer


def _run_event(ev_type, trace="t1", **kw):
    base = dict(source="test", trace_id=trace)
    base.update(kw)
    return AITelemetryEvent(event_type=ev_type, **base)


def test_buffer_run_lifecycle_and_trace_correlation():
    b = AiTelemetryBuffer()
    start = b.ingest(_run_event(EVENT_AGENT_RUN_STARTED, agent_id="a1",
                                agent_name="Hermes"))
    assert start.runtime["kind"] == "AGENT_RUN"
    assert start.runtime["node_id"] == "ai:run:t1"
    assert b.active_runs() and b.active_runs()[0]["agent_id"] == "a1"

    mr = b.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED, span_id="s1",
                             model_id="qwen3", total_tokens=42))
    assert mr.runtime["parent_node_id"] == "ai:run:t1"  # real trace parentage

    b.ingest(_run_event(EVENT_AGENT_RUN_FINISHED))
    assert b.active_runs() == []
    assert len(b.history) == 3


def test_buffer_metrics_only_real_evidence():
    b = AiTelemetryBuffer()
    b.ingest(_run_event(EVENT_AGENT_RUN_STARTED, agent_id="a1"), now=1_800_000_000.0)
    b.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED, model_id="qwen3",
                        tool_name=None, input_tokens=100, output_tokens=50),
             now=1_800_000_000.0)
    m = b.metrics(now=1_800_000_060.0)
    assert m["runs"] == 1
    assert m["model"] == "qwen3"
    assert "tokens_per_s" in m
    # fixture/test-only tokens never enter the token window
    b2 = AiTelemetryBuffer()
    b2.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED, total_tokens=999,
                         test_only=True), now=1_800_000_000.0)
    assert "tokens_per_s" not in b2.metrics(now=1_800_000_060.0)


def test_buffer_caps_are_bounded():
    b = AiTelemetryBuffer()
    for i in range(CAP_EVENT_HISTORY + 50):
        b.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED,
                            span_id=f"s{i}", total_tokens=1))
    assert len(b.history) == CAP_EVENT_HISTORY
    # recent spans cap
    for i in range(CAP_RECENT_SPANS + 10):
        b.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED, span_id=f"x{i}"))
    assert len(b.recent_spans) == CAP_RECENT_SPANS
    # active traces cap
    b2 = AiTelemetryBuffer()
    for i in range(CAP_ACTIVE_TRACES + 5):
        b2.ingest(_run_event(EVENT_AGENT_RUN_STARTED, trace=f"t{i}"))
    assert len(b2.traces) == CAP_ACTIVE_TRACES


def test_buffer_run_ttl_prunes_stale_traces():
    b = AiTelemetryBuffer()
    b.ingest(_run_event(EVENT_AGENT_RUN_STARTED, trace="old"), now=1_000.0)
    b.ingest(_run_event(EVENT_AGENT_RUN_STARTED, trace="new"), now=1_000.0)
    assert len(b.active_runs()) == 2
    b.ingest(_run_event(EVENT_MODEL_REQUEST_FINISHED, trace="new"),
             now=1_000.0 + 700.0)
    # 'old' exceeded RUN_TTL_S (600) without activity -> pruned
    assert [r["trace_id"] for r in b.active_runs()] == ["new"]


# ========================================================== hermes provider


class _FakeProc:
    def __init__(self, pid, cmdline):
        self.pid = pid
        self.info = {"pid": pid, "cmdline": cmdline}


class _FakeConn:
    def __init__(self, pid, port):
        self.pid = pid
        self.status = "LISTEN"
        self.laddr = SimpleNamespace(ip="127.0.0.1", port=port)


def _patch_discovery(monkeypatch, procs, conns):
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))
    monkeypatch.setattr(psutil, "net_connections", lambda *a, **k: conns)


def _status_payload(agents=0, sessions=0, platforms=None, components=None,
                    version="0.20.4"):
    return {
        "version": version,
        "active_agents": agents,
        "active_sessions": sessions,
        "gateway_platforms": platforms or {},
        "components": components or {},
        "install_id": "PRIVATE-INSTALL-ID",       # must never leak
        "hermes_home": "C:\\PRIVATE\\hermes",      # must never leak
        "config_path": "C:\\PRIVATE\\config.yaml",
        "env_path": "C:\\PRIVATE\\.env",
    }


def _health_payload():
    return {"ok": True, "version": "0.20.4", "auth_required": False}


def _status_fake(*payloads):
    """health -> static payload; status -> one payload per poll."""
    it = iter(payloads)

    def fake(ep, path):
        if path == "/api/health":
            return _health_payload()
        return next(it)

    return fake


def test_hermes_provider_unavailable_without_gateway(monkeypatch):
    _patch_discovery(monkeypatch, [], [])
    p = HermesGatewayProvider()
    assert p.poll() == []
    assert p.status()["state"] == STATE_UNAVAILABLE
    assert p.status()["availability"]["tokens"] is False


def test_hermes_provider_available_no_data(monkeypatch):
    _patch_discovery(monkeypatch,
                     [_FakeProc(111, ["python", "-m", "hermes_cli.main", "serve",
                                      "--host", "127.0.0.1", "--port", "0"])],
                     [_FakeConn(111, 5555)])
    p = HermesGatewayProvider()
    monkeypatch.setattr(p, "_get_json", _status_fake(_status_payload(agents=0, sessions=0)))
    assert p.poll() == []
    assert p.status()["state"] == STATE_AVAILABLE_NO_DATA


def test_hermes_provider_active_and_run_deltas(monkeypatch):
    _patch_discovery(monkeypatch,
                     [_FakeProc(111, ["python", "-m", "hermes_cli.main",
                                      "--profile", "testp", "serve",
                                      "--host", "127.0.0.1", "--port", "0"])],
                     [_FakeConn(111, 5555)])
    p = HermesGatewayProvider()
    monkeypatch.setattr(p, "_get_json", _status_fake(
        _status_payload(agents=0, sessions=1),
        _status_payload(agents=1, sessions=1),
        _status_payload(agents=2, sessions=1),
        _status_payload(agents=1, sessions=1),
    ))
    assert p.poll() == []                      # baseline, no storm
    evs = p.poll()
    assert [e.event_type for e in evs] == [EVENT_AGENT_RUN_STARTED]
    assert evs[0].agent_id == "hermes:testp:1"
    assert evs[0].metadata["count_before"] == 0
    assert evs[0].metadata["count_after"] == 1
    evs = p.poll()
    assert [e.event_type for e in evs] == [EVENT_AGENT_RUN_STARTED]
    assert evs[0].agent_id == "hermes:testp:2"
    evs = p.poll()
    assert [e.event_type for e in evs] == [EVENT_AGENT_RUN_FINISHED]
    assert evs[0].agent_id == "hermes:testp:1"  # FIFO identity (documented)
    assert p.status()["state"] == STATE_ACTIVE


def test_hermes_provider_degraded_on_poll_failure(monkeypatch):
    _patch_discovery(monkeypatch,
                     [_FakeProc(111, ["python", "-m", "hermes_cli.main", "serve"])],
                     [_FakeConn(111, 5555)])
    p = HermesGatewayProvider()
    monkeypatch.setattr(p, "_get_json", lambda ep, path: None)
    assert p.poll() == []
    assert p.status()["state"] == STATE_DEGRADED


def test_hermes_provider_platform_error_is_change_only(monkeypatch):
    _patch_discovery(monkeypatch,
                     [_FakeProc(111, ["python", "-m", "hermes_cli.main", "serve"])],
                     [_FakeConn(111, 5555)])
    p = HermesGatewayProvider()
    monkeypatch.setattr(p, "_get_json", _status_fake(
        _status_payload(agents=0, sessions=0, platforms={
            "default:telegram": {"state": "connected", "error_code": None}}),
        _status_payload(agents=0, sessions=0, platforms={
            "default:telegram": {"state": "error",
                                 "error_code": "flood",
                                 "error_message": "rate limited"}}),
        _status_payload(agents=0, sessions=0, platforms={
            "default:telegram": {"state": "error",
                                 "error_code": "flood",
                                 "error_message": "rate limited"}}),
    ))
    p.poll()
    evs = p.poll()
    assert [e.event_type for e in evs] == [EVENT_AI_ERROR]
    assert evs[0].metadata["platform"] == "default:telegram"
    assert evs[0].metadata["error_code"] == "flood"
    assert p.poll() == []  # unchanged -> no new event


def test_hermes_provider_never_leaks_private_status_fields(monkeypatch):
    _patch_discovery(monkeypatch,
                     [_FakeProc(111, ["python", "-m", "hermes_cli.main", "serve"])],
                     [_FakeConn(111, 5555)])
    p = HermesGatewayProvider()
    monkeypatch.setattr(p, "_get_json",
                        _status_fake(_status_payload(agents=1, sessions=1)))
    p.poll()
    p.poll()  # produce a run-started event
    # assert on the status() detail + availability which is what the API exposes
    detail = p.status()["detail"]
    assert "install" not in detail.lower()
    assert "config_path" not in detail.lower()
    assert "env_path" not in detail.lower()
    assert p.status()["availability"]["tokens"] is False


# ================================================================ otel seam


def test_otel_model_request_span():
    span = {
        "name": "chat",
        "kind": "CLIENT",
        "trace_id": "0123456789abcdef0123456789abcdef",
        "span_id": "fedcba9876543210",
        "parent_span_id": "0000000000000001",
        "start_time_unix_nano": 1_700_000_000_000_000_000,
        "end_time_unix_nano": 1_700_000_000_850_000_000,
        "status": {"code": "OK"},
        "attributes": {
            "gen_ai.system": "deepseek",
            "gen_ai.request.model": "deepseek-v4-flash",
            "gen_ai.operation.name": "chat",
            "gen_ai.usage.input_tokens": 120,
            "gen_ai.usage.output_tokens": 80,
            "gen_ai.usage.total_tokens": 200,
            "server.address": "127.0.0.1",
            "server.port": 11434,
        },
    }
    ev = normalize_otel_span(span)
    assert ev is not None
    assert ev.event_type == EVENT_MODEL_REQUEST_FINISHED
    assert ev.model_id == "deepseek-v4-flash"
    assert ev.input_tokens == 120 and ev.output_tokens == 80
    assert ev.total_tokens == 200
    assert ev.duration_ms == 850.0
    assert ev.trace_id == "0123456789abcdef"
    assert ev.span_id == "fedcba9876543210"
    assert ev.parent_span_id == "0000000000000001"
    assert ev.status == "ok"
    # tps is derived at ingest (finalize) from REAL otel tokens + duration
    ev.finalize()
    assert ev.tps is not None


def test_otel_tool_and_mcp_spans():
    tool = normalize_otel_span({
        "name": "tool.filesystem",
        "kind": "CLIENT",
        "attributes": {"gen_ai.tool.name": "filesystem",
                       "gen_ai.tool.call.id": "call_1"},
    })
    assert tool is not None and tool.event_type == EVENT_TOOL_CALL_STARTED
    assert tool.tool_name == "filesystem"
    mcp = normalize_otel_span({
        "name": "mcp.registry",
        "kind": "CLIENT",
        "end_time_unix_nano": 1_700_000_000_000_000_000,
        "attributes": {"gen_ai.tool.name": "mcp-server"},
    })
    assert mcp is not None and mcp.event_type == EVENT_MCP_CALL_FINISHED


def test_otel_agent_run_and_error_spans():
    run = normalize_otel_span({
        "name": "agent.run",
        "kind": "INTERNAL",
        "attributes": {"agent.id": "hermes:1", "agent.name": "Hermes",
                       "agent.run.id": "run-9"},
    })
    assert run is not None and run.event_type == EVENT_AGENT_RUN_STARTED
    assert run.agent_id == "hermes:1"
    err = normalize_otel_span({
        "name": "chat",
        "kind": "CLIENT",
        "status": {"code": "ERROR"},
        "attributes": {"gen_ai.request.model": "m"},
    })
    assert err is not None and err.event_type == EVENT_AI_ERROR
    assert err.status == "error"


def test_otel_non_ai_span_is_ignored():
    assert normalize_otel_span({"name": "http.get", "kind": "CLIENT",
                                "attributes": {"http.url": "x"}}) is None


# ========================================================== fixture provider


def test_fixture_provider_deterministic_lifecycle():
    p = FixtureAiProvider()
    p.start()
    assert p.test_only is True
    assert p.status()["state"] == STATE_ACTIVE
    seen = []
    for _ in range(16):  # two full cycles (8 steps each)
        evs = p.poll(now=1_000.0 + _)
        if evs:
            seen.append(evs[0])
    types = [e.event_type for e in seen]
    assert types[:8] == [
        EVENT_AGENT_RUN_STARTED, EVENT_MODEL_REQUEST_STARTED,
        EVENT_MODEL_REQUEST_FINISHED, EVENT_TOOL_CALL_STARTED,
        EVENT_TOOL_CALL_FINISHED, EVENT_MCP_CALL_STARTED,
        EVENT_MCP_CALL_FINISHED, EVENT_AGENT_RUN_FINISHED,
    ]
    assert all(e.test_only for e in seen)
    assert all(e.source == "fixture" for e in seen)
    assert seen[0].trace_id == "fixture-trace-1"
    assert seen[8].trace_id == "fixture-trace-2"  # cycle advances trace id
    # token metrics in the fixture are real *fixture* values; total_tokens
    # and tps are finalized by the buffer at ingest (pipeline contract)
    from app.ai_telemetry.buffer import AiTelemetryBuffer

    b = AiTelemetryBuffer()
    fin = b.ingest(seen[2])
    assert fin.input_tokens == 120 and fin.output_tokens == 80
    assert fin.total_tokens == 200 and fin.tps == 94.1


# ================================================================ registry


def test_registry_real_mode_provider_set(monkeypatch):
    monkeypatch.delenv("ESW_AI_TELEMETRY_FIXTURE", raising=False)
    r = AiTelemetryRegistry()
    r.start()
    assert [p.name for p in r.providers] == ["hermes", "otel"]
    assert r.fixture_mode is False
    r.stop()


def test_registry_fixture_mode_env_gated(monkeypatch):
    monkeypatch.setenv("ESW_AI_TELEMETRY_FIXTURE", "1")
    r = AiTelemetryRegistry()
    r.start()
    assert r.fixture_mode is True
    assert [p.name for p in r.providers] == ["fixture"]
    assert r.snapshot()["fixture_mode"] is True
    r.stop()


def test_registry_ingest_rejects_private_content():
    r = AiTelemetryRegistry()
    with pytest.raises(ValueError, match="private content rejected"):
        r.ingest_external(EVENT_AGENT_RUN_STARTED, "tester",
                          metadata={"prompt": "hello"})
    with pytest.raises(ValueError, match="private content rejected"):
        r.ingest_external(EVENT_AI_ERROR, "tester",
                          metadata={"detail": "Bearer abc123"})


def test_registry_tick_publishes_activity_and_status(monkeypatch):
    import psutil

    # sandbox: no real gateway discovery in tests
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([]))
    monkeypatch.setattr(psutil, "net_connections", lambda *a, **k: [])
    r = AiTelemetryRegistry()
    r.start()
    messages = []
    r.publish_message = messages.append
    r._tick(now=1_000.0)
    # first tick: baseline -> provider status published once
    types = [m["type"] for m in messages]
    assert "ai_provider_status" in types
    status = [m for m in messages if m["type"] == "ai_provider_status"][0]
    assert status["fixture"] is False
    assert set(status["providers"]) == {"hermes", "otel"}
    # hermes truthfully reports UNAVAILABLE in this sandbox (no gateway)
    assert status["providers"]["hermes"]["state"] == STATE_UNAVAILABLE
    # second tick: nothing changed -> no new status message
    r._tick(now=1_005.0)
    assert sum(1 for m in messages if m["type"] == "ai_provider_status") == 1


def test_registry_ingest_external_flow():
    r = AiTelemetryRegistry()
    messages = []
    rows = []
    r.publish_message = messages.append
    r.publish_events = rows.extend
    ev = r.ingest_external(EVENT_MODEL_REQUEST_FINISHED, "local-instrument",
                           model_id="qwen3", total_tokens=42, test_only=True)
    assert ev.event_id is not None
    acts = [m for m in messages if m["type"] == "ai_activity"]
    assert len(acts) == 1
    assert acts[0]["events"][0]["event_type"] == EVENT_MODEL_REQUEST_FINISHED
    assert acts[0]["events"][0]["test_only"] is True
    assert len(rows) == 1
    assert rows[0]["metadata"]["total_tokens"] == 42
