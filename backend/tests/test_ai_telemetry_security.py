"""Phase 17 — AI telemetry SECURITY tests.

Prove the ingestion surface is metadata-only and cannot become a control
surface: schema whitelisting, payload bounds, private-content rejection,
and zero execution/control paths. Also covers the benchmark-mode message
suppression seam.
"""
from __future__ import annotations

import json

import pytest

import app.main as main
from app.ai_telemetry.models import (
    EVENT_AGENT_RUN_STARTED,
    EVENT_MODEL_REQUEST_FINISHED,
    EVENT_TOOL_CALL_STARTED,
)

_VALID = {
    "source": "acceptance-test",
    "event_type": EVENT_AGENT_RUN_STARTED,
    "agent_id": "test-agent-1",
    "agent_name": "TEST AGENT",
    "trace_id": "trace-1",
    "test_only": True,
}


def _post(client, payload):
    return client.post("/api/ai-telemetry/events", json=payload)


def test_ingestion_endpoint_rejects_unknown_fields(client):
    r = _post(client, {**_VALID, "prompt_text": "hello"})
    assert r.status_code == 422


def test_ingestion_endpoint_rejects_unsupported_event_type(client):
    r = _post(client, {**_VALID, "event_type": "HACK_THE_PLANET"})
    assert r.status_code == 422


def test_ingestion_endpoint_rejects_private_content(client):
    r = _post(client, {**_VALID, "metadata": {"prompt": "secret question"}})
    assert r.status_code == 422
    assert "private content rejected" in r.json()["error"]
    r = _post(client, {**_VALID, "metadata": {"note": "Bearer super-secret-token"}})
    assert r.status_code == 422


def test_ingestion_endpoint_rejects_oversized_metadata(client):
    r = _post(client, {**_VALID, "metadata": {"k" * 65: "v"}})
    assert r.status_code == 422
    big = {f"key{i}": "x" for i in range(40)}
    r = _post(client, {**_VALID, "metadata": big})
    assert r.status_code == 422


def test_ingestion_endpoint_rejects_oversized_body(client):
    r = client.post(
        "/api/ai-telemetry/events",
        content=json.dumps({**_VALID, "metadata": {"pad": "A" * 70_000}}),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413


def test_ingestion_endpoint_rejects_invalid_types(client):
    r = _post(client, {**_VALID, "duration_ms": "not-a-number"})
    assert r.status_code == 422
    r = _post(client, {**_VALID, "input_tokens": -5})
    assert r.status_code == 422
    r = _post(client, {**_VALID, "source": ""})
    assert r.status_code == 422


def test_ingestion_endpoint_accepts_valid_metadata_event(client):
    r = _post(client, {**_VALID, "metadata": {"profile": "test",
                                              "duration_ms": 12.5}})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["test_only"] is True
    assert body["fixture"] is False  # real mode backend: no fixture mixing
    assert body["event_id"]


def test_ingestion_endpoint_observes_only_no_control_paths(client):
    """The endpoint must never mutate anything outside the telemetry
    buffer. Assert the response carries no command/exec surface and the
    buffer only grew by the accepted event."""
    before = len(main.ai_registry.buffer.history)
    r = _post(client, {**_VALID, "event_type": EVENT_TOOL_CALL_STARTED,
                       "tool_name": "filesystem", "test_only": True})
    assert r.status_code == 200
    after = len(main.ai_registry.buffer.history)
    assert after == before + 1
    # no keys suggesting execution/control in the response
    assert not any(k in r.json() for k in ("command", "exec", "shell", "pid"))


def test_ai_telemetry_status_endpoint(client):
    r = client.get("/api/ai-telemetry")
    assert r.status_code == 200
    body = r.json()
    assert body["fixture_mode"] is False
    assert "providers" in body
    assert "active_runs" in body
    assert "caps" in body
    assert body["caps"]["event_history"] == 500


def test_ai_message_suppression_under_benchmark(monkeypatch):
    """Real AI telemetry never mixes into the TEST-ONLY benchmark graph."""
    main.benchmark._active = True
    try:
        assert main._ai_message_allowed({"type": "ai_activity"}) is False
        assert main._ai_message_allowed({"type": "ai_metrics"}) is False
        assert main._ai_message_allowed({"type": "ai_provider_status"}) is False
        # non-AI messages are untouched by this seam
        assert main._ai_message_allowed({"type": "network_activity"}) is True
        assert main._ai_message_allowed({"type": "events"}) is True
    finally:
        main.benchmark._active = False
    assert main._ai_message_allowed({"type": "ai_activity"}) is True


def test_model_request_endpoint_accepts_token_metrics(client):
    r = _post(client, {
        "source": "acceptance-test",
        "event_type": EVENT_MODEL_REQUEST_FINISHED,
        "model_id": "qwen2.5-coder:3b",
        "status": "ok",
        "duration_ms": 850.0,
        "input_tokens": 120,
        "output_tokens": 80,
        "tps": 94.1,
        "test_only": True,
    })
    assert r.status_code == 200
    assert r.json()["accepted"] == 1
