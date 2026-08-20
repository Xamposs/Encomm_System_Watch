"""AI telemetry package (Phase 17).

Real application-level AI telemetry: normalized schema, bounded buffer,
failure-isolated providers (Hermes gateway status API, OTEL seam,
env-gated fixture), localhost-only ingestion endpoint, WebSocket
activity/metrics/provider-status messages.
"""
from .base import (
    STATE_ACTIVE,
    STATE_AVAILABLE_NO_DATA,
    STATE_DEGRADED,
    STATE_UNAVAILABLE,
    TelemetryProvider,
)
from .buffer import AiTelemetryBuffer
from .models import (
    AITelemetryEvent,
    ALL_EVENT_TYPES,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    EVENT_AGENT_MESSAGE,
    EVENT_AI_ERROR,
    EVENT_MCP_CALL_FINISHED,
    EVENT_MCP_CALL_STARTED,
    EVENT_MODEL_REQUEST_FINISHED,
    EVENT_MODEL_REQUEST_STARTED,
    EVENT_RETRY,
    EVENT_TOOL_CALL_FINISHED,
    EVENT_TOOL_CALL_STARTED,
    contains_private_content,
)
from .registry import AiTelemetryRegistry

__all__ = [
    "AITelemetryEvent",
    "AiTelemetryBuffer",
    "AiTelemetryRegistry",
    "TelemetryProvider",
    "STATE_ACTIVE",
    "STATE_AVAILABLE_NO_DATA",
    "STATE_DEGRADED",
    "STATE_UNAVAILABLE",
    "ALL_EVENT_TYPES",
    "EVENT_AGENT_RUN_STARTED",
    "EVENT_AGENT_RUN_FINISHED",
    "EVENT_MODEL_REQUEST_STARTED",
    "EVENT_MODEL_REQUEST_FINISHED",
    "EVENT_TOOL_CALL_STARTED",
    "EVENT_TOOL_CALL_FINISHED",
    "EVENT_MCP_CALL_STARTED",
    "EVENT_MCP_CALL_FINISHED",
    "EVENT_AGENT_MESSAGE",
    "EVENT_RETRY",
    "EVENT_AI_ERROR",
    "contains_private_content",
]
