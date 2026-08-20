"""TEST-ONLY deterministic AI telemetry fixture provider (Phase 17).

Unmistakably labeled TEST / FIXTURE / SYNTHETIC:

  - only instantiated when ``ESW_AI_TELEMETRY_FIXTURE=1`` is set at
    backend start (same opt-in pattern as the synthetic ETW provider);
  - every event carries ``test_only=True`` and ``source="fixture"``;
  - the registry reports ``fixture_mode: true`` while it is active;
  - the WebSocket ``ai_activity`` messages carry ``fixture: true``.

The scripted timeline exercises the full normalized lifecycle with
deterministic ids/values: run start -> model request -> tool call ->
MCP call -> run finish, then repeats with the next trace id. It proves
the pipeline end-to-end without ever pretending to be real telemetry.
"""
from __future__ import annotations

import time
from typing import Optional

from .base import STATE_ACTIVE, TelemetryProvider
from .models import (
    AITelemetryEvent,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    EVENT_MCP_CALL_FINISHED,
    EVENT_MCP_CALL_STARTED,
    EVENT_MODEL_REQUEST_FINISHED,
    EVENT_MODEL_REQUEST_STARTED,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
)

_STEPS = 8  # one full lifecycle
_STEP_INTERVAL_S = 1.0


class FixtureAiProvider(TelemetryProvider):
    name = "fixture"
    kind = "fixture"
    test_only = True

    def __init__(self) -> None:
        super().__init__()
        self._step = 0
        self._cycle = 0
        self._last_step_at = 0.0

    def start(self) -> None:
        self._set_state(
            STATE_ACTIVE,
            "TEST/FIXTURE/SYNTHETIC deterministic provider — "
            "ESW_AI_TELEMETRY_FIXTURE=1 (never active in real mode)",
            availability={
                "runs": True, "sessions": False, "model_requests": True,
                "tool_calls": True, "mcp_calls": True, "tokens": True,
                "tps": True, "latency": True, "traces": True,
            },
        )

    def poll(self, now: float | None = None) -> list[AITelemetryEvent]:
        now = time.time() if now is None else now
        if self._last_step_at == 0.0:
            self._last_step_at = now
            return [self._event(self._step)]
        if now - self._last_step_at < _STEP_INTERVAL_S:
            return []
        self._last_step_at = now
        self._step += 1
        if self._step >= _STEPS:
            self._step = 0
            self._cycle += 1
        return [self._event(self._step)]

    # ------------------------------------------------------------------ script
    def _event(self, step: int) -> AITelemetryEvent:
        cycle = self._cycle
        trace = f"fixture-trace-{cycle + 1}"
        run_id = f"fixture-run-{cycle + 1}"
        common = {
            "source": "fixture",
            "agent_id": run_id,
            "agent_name": "FIXTURE AGENT (TEST)",
            "trace_id": trace,
            "test_only": True,
            "metadata": {
                "fixture": True,
                "label": "TEST/FIXTURE/SYNTHETIC — deterministic acceptance harness",
            },
        }
        if step == 0:
            return AITelemetryEvent(
                event_type=EVENT_AGENT_RUN_STARTED, status="active", **common)
        if step == 1:
            return AITelemetryEvent(
                event_type=EVENT_MODEL_REQUEST_STARTED,
                span_id=f"fix-mr-s-{cycle + 1}", parent_span_id=None,
                model_id="fixture-model", status="running", **common)
        if step == 2:
            return AITelemetryEvent(
                event_type=EVENT_MODEL_REQUEST_FINISHED,
                span_id=f"fix-mr-f-{cycle + 1}", parent_span_id=None,
                model_id="fixture-model", status="ok",
                duration_ms=850.0, input_tokens=120, output_tokens=80,
                tps=94.1, **common)
        if step == 3:
            return AITelemetryEvent(
                event_type=EVENT_TOOL_CALL_STARTED,
                span_id=f"fix-tc-s-{cycle + 1}", parent_span_id=f"fix-mr-f-{cycle + 1}",
                tool_name="fixture-tool", status="running", **common)
        if step == 4:
            return AITelemetryEvent(
                event_type=EVENT_TOOL_CALL_FINISHED,
                span_id=f"fix-tc-f-{cycle + 1}", parent_span_id=f"fix-mr-f-{cycle + 1}",
                tool_name="fixture-tool", status="ok", duration_ms=210.0, **common)
        if step == 5:
            return AITelemetryEvent(
                event_type=EVENT_MCP_CALL_STARTED,
                span_id=f"fix-mcp-s-{cycle + 1}", parent_span_id=f"fix-tc-f-{cycle + 1}",
                tool_name="fixture-mcp-server", status="running", **common)
        if step == 6:
            return AITelemetryEvent(
                event_type=EVENT_MCP_CALL_FINISHED,
                span_id=f"fix-mcp-f-{cycle + 1}", parent_span_id=f"fix-tc-f-{cycle + 1}",
                tool_name="fixture-mcp-server", status="ok", duration_ms=95.0, **common)
        return AITelemetryEvent(
            event_type=EVENT_AGENT_RUN_FINISHED, status="finished",
            duration_ms=5_000.0, **common)
