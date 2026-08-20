"""OpenTelemetry-compatible seam (Phase 17).

A LIGHTWEIGHT normalization adapter — deliberately NOT a general-purpose
OTEL collector. It maps a small, well-known subset of OpenTelemetry span
semantic conventions (``gen_ai.*`` / ``agent.*`` / ``tool.*``) onto the
normalized :class:`AITelemetryEvent` schema, so an explicitly
instrumented local producer can drop AI spans into the pipeline later.

Status today: READY / NO REAL PRODUCER. No OTEL producer exists on this
machine, so the seam is unit-tested and labeled exactly that — it never
fabricates events on its own.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from .base import STATE_AVAILABLE_NO_DATA, TelemetryProvider
from .models import (
    AITelemetryEvent,
    EVENT_AGENT_MESSAGE,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    EVENT_AI_ERROR,
    EVENT_MCP_CALL_FINISHED,
    EVENT_MCP_CALL_STARTED,
    EVENT_MODEL_REQUEST_FINISHED,
    EVENT_MODEL_REQUEST_STARTED,
    EVENT_RETRY,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
)

# gen_ai semantic-convention attribute names (the subset we understand)
_ATTR_MODEL_REQUEST = "gen_ai.request.model"
_ATTR_MODEL_RESPONSE = "gen_ai.response.model"
_ATTR_OPERATION = "gen_ai.operation.name"
_ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
_ATTR_TOTAL_TOKENS = "gen_ai.usage.total_tokens"
_ATTR_TOOL_NAME = "gen_ai.tool.name"
_ATTR_TOOL_CALL_ID = "gen_ai.tool.call.id"
_ATTR_AGENT_ID = "agent.id"
_ATTR_AGENT_NAME = "agent.name"
_ATTR_AGENT_RUN_ID = "agent.run.id"
_ATTR_SYSTEM = "gen_ai.system"
_ATTR_SERVER_ADDR = "server.address"
_ATTR_SERVER_PORT = "server.port"

_START_OPS = frozenset({"chat", "generate", "complete", "embeddings", "agent"})
_TOOL_OPS = frozenset({"tool", "function", "mcp", "filesystem", "shell", "browser"})
_MCP_HINTS = ("mcp", "stdio", "sse", "jsonrpc")


def normalize_otel_span(span: dict[str, Any]) -> Optional[AITelemetryEvent]:
    """Map one OTEL-shaped span dict onto the normalized schema.

    Returns ``None`` when the span carries no recognized AI semantic
    attributes (it is not AI telemetry — do not guess). Only real span
    fields are copied; everything else stays ``None``.
    """
    attrs = dict(span.get("attributes") or {})
    name = str(span.get("name") or "").lower()
    kind = str(span.get("kind") or "").upper()

    is_ai = any(
        k.startswith("gen_ai.") or k.startswith("agent.") for k in attrs
    ) or name.startswith(("gen_ai.", "agent.", "tool.", "mcp."))
    if not is_ai:
        return None

    # operation classification (evidence-based, never guessed)
    op = str(attrs.get(_ATTR_OPERATION) or "").lower() or name
    if any(t in op for t in _MCP_HINTS) or name.startswith("mcp."):
        event_type = _span_type(span, EVENT_MCP_CALL_STARTED, EVENT_MCP_CALL_FINISHED)
    elif any(t in op for t in _TOOL_OPS) or name.startswith(("tool.", "function.")) \
            or _ATTR_TOOL_NAME in attrs:
        event_type = _span_type(span, EVENT_TOOL_CALL_STARTED, EVENT_TOOL_CALL_FINISHED)
    elif "run" in op or "agent" in op or _ATTR_AGENT_RUN_ID in attrs:
        # agent/run lifecycle spans must win over the generic model ops
        event_type = _span_type(span, EVENT_AGENT_RUN_STARTED, EVENT_AGENT_RUN_FINISHED)
    elif any(t in op for t in _START_OPS) or _ATTR_MODEL_REQUEST in attrs \
            or _ATTR_MODEL_RESPONSE in attrs:
        event_type = _span_type(span, EVENT_MODEL_REQUEST_STARTED, EVENT_MODEL_REQUEST_FINISHED)
    else:
        return None

    status_code = str((span.get("status") or {}).get("code") or "").upper()
    if status_code in ("ERROR", "2"):
        event_type = EVENT_AI_ERROR

    duration_ms = None
    start = span.get("start_time_unix_nano")
    end = span.get("end_time_unix_nano")
    if isinstance(start, int) and isinstance(end, int) and end >= start:
        duration_ms = round((end - start) / 1_000_000.0, 2)

    input_tokens = attrs.get(_ATTR_INPUT_TOKENS)
    output_tokens = attrs.get(_ATTR_OUTPUT_TOKENS)
    total_tokens = attrs.get(_ATTR_TOTAL_TOKENS)
    input_tokens = int(input_tokens) if isinstance(input_tokens, (int, float)) else None
    output_tokens = int(output_tokens) if isinstance(output_tokens, (int, float)) else None
    total_tokens = int(total_tokens) if isinstance(total_tokens, (int, float)) else None

    model_id = attrs.get(_ATTR_MODEL_RESPONSE) or attrs.get(_ATTR_MODEL_REQUEST)
    tool_name = attrs.get(_ATTR_TOOL_NAME)

    metadata: dict[str, Any] = {}
    for key, attr_name in (
        ("system", _ATTR_SYSTEM), ("server_address", _ATTR_SERVER_ADDR),
        ("server_port", _ATTR_SERVER_PORT), ("operation", _ATTR_OPERATION),
    ):
        if attr_name in attrs:
            metadata[key] = attrs[attr_name]
    if _ATTR_TOOL_CALL_ID in attrs:
        metadata["tool_call_id"] = attrs[_ATTR_TOOL_CALL_ID]

    return AITelemetryEvent(
        event_type=event_type,
        source="otel-seam",
        agent_id=attrs.get(_ATTR_AGENT_ID) or attrs.get(_ATTR_AGENT_RUN_ID),
        agent_name=attrs.get(_ATTR_AGENT_NAME),
        model_id=str(model_id) if model_id else None,
        tool_name=str(tool_name) if tool_name else None,
        trace_id=_short_id(span.get("trace_id")),
        span_id=_short_id(span.get("span_id")),
        parent_span_id=_short_id(span.get("parent_span_id")),
        status="ok" if status_code in ("OK", "1") else ("error" if status_code in ("ERROR", "2") else None),
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        context_tokens=int(attrs["gen_ai.usage.context_tokens"]) if isinstance(
            attrs.get("gen_ai.usage.context_tokens"), (int, float)) else None,
        metadata=metadata,
    )


def _span_type(span: dict[str, Any], start_t: str, end_t: str) -> str:
    kind = str(span.get("kind") or "").upper()
    if kind in ("INTERNAL",) and span.get("end_time_unix_nano") is not None:
        return end_t
    if kind in ("CLIENT", "PRODUCER") and span.get("end_time_unix_nano") is None:
        return start_t
    # no kind evidence: presence of an end time implies a finished span
    return end_t if span.get("end_time_unix_nano") is not None else start_t


def _short_id(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:16] if len(s) > 16 else (s or None)


class OtelSeam(TelemetryProvider):
    """Provider-shaped wrapper around :func:`normalize_otel_span`.

    READY / NO REAL PRODUCER: the normalization path is complete and
    unit-tested, but no OTEL producer is wired on this machine — the
    seam stays AVAILABLE_NO_DATA until explicit local instrumentation
    exists.
    """

    name = "otel"
    kind = "otel-seam"

    def __init__(self) -> None:
        super().__init__()
        self._set_state(
            STATE_AVAILABLE_NO_DATA,
            "OTEL normalization seam READY — NO REAL PRODUCER (explicit "
            "local instrumentation required; none found on this machine)",
            availability={k: False for k in self._availability},
        )

    def poll(self, now: float | None = None) -> list[AITelemetryEvent]:
        # no producer to poll — the seam is a transformer, not a source
        return []
