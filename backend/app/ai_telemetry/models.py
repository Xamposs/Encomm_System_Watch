"""Normalized AI telemetry schema + privacy gate (Phase 17).

OS network telemetry is NOT application AI telemetry. A TCP connection is
not a tool call, network bytes are not tokens, socket throughput is not
TPS. Every field in :class:`AITelemetryEvent` is OPTIONAL and must stay
``None`` when no real source provides it — nothing here is ever estimated
from process/network evidence.

Privacy boundary: this module also implements the metadata-only gate for
the local ingestion endpoint. Prompts, responses, reasoning content,
arbitrary tool arguments, file contents and credentials are rejected —
SYSTEM WATCH is metadata observability, never content capture.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------- event types
EVENT_AGENT_RUN_STARTED = "AGENT_RUN_STARTED"
EVENT_AGENT_RUN_FINISHED = "AGENT_RUN_FINISHED"
EVENT_MODEL_REQUEST_STARTED = "MODEL_REQUEST_STARTED"
EVENT_MODEL_REQUEST_FINISHED = "MODEL_REQUEST_FINISHED"
EVENT_TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
EVENT_TOOL_CALL_FINISHED = "TOOL_CALL_FINISHED"
EVENT_MCP_CALL_STARTED = "MCP_CALL_STARTED"
EVENT_MCP_CALL_FINISHED = "MCP_CALL_FINISHED"
EVENT_AGENT_MESSAGE = "AGENT_MESSAGE"
EVENT_RETRY = "RETRY"
EVENT_AI_ERROR = "AI_ERROR"

ALL_EVENT_TYPES: frozenset[str] = frozenset({
    EVENT_AGENT_RUN_STARTED, EVENT_AGENT_RUN_FINISHED,
    EVENT_MODEL_REQUEST_STARTED, EVENT_MODEL_REQUEST_FINISHED,
    EVENT_TOOL_CALL_STARTED, EVENT_TOOL_CALL_FINISHED,
    EVENT_MCP_CALL_STARTED, EVENT_MCP_CALL_FINISHED,
    EVENT_AGENT_MESSAGE, EVENT_RETRY, EVENT_AI_ERROR,
})

# Event types that OPEN a run/trace and those that CLOSE it.
RUN_START_TYPES: frozenset[str] = frozenset({EVENT_AGENT_RUN_STARTED})
RUN_END_TYPES: frozenset[str] = frozenset({EVENT_AGENT_RUN_FINISHED})

# -------------------------------------------------------------------- privacy
# Exact metadata keys that must never be ingested (metadata is metadata).
_FORBIDDEN_KEYS = re.compile(
    r"^(?:prompt|prompts|response|responses|content|reasoning|thinking|"
    r"message|messages|text|input_text|output_text|question|answer|query|"
    r"file_contents|code|script|api_key|apikey|authorization|auth_header|"
    r"bearer|password|passwd|secret|client_secret|private_key|credential|"
    r"credentials|access_token|refresh_token|token|session_key|auth_token|"
    r"cookie|cookies)$",
    re.IGNORECASE,
)
# Value shapes that mark a payload as carrying private content.
_FORBIDDEN_VALUE = re.compile(
    r"(?i)(?:bearer\s+\S+|-----BEGIN|sk-[a-z0-9]{16,}|"
    r"(?:api[_-]?key|password|passwd|secret|authorization|token)\s*[=:]\s*\S+|"
    r"ghp_[a-zA-Z0-9]{20,})"
)
_VALUE_SCAN_LIMIT = 512  # only scan the head of each string value


def contains_private_content(metadata: dict[str, Any]) -> Optional[str]:
    """Return the offending key description if metadata carries private
    content, else ``None``.

    Recursive (nested dicts/lists) with bounded scanning: only string
    values are inspected, only their first :data:`_VALUE_SCAN_LIMIT`
    characters, and the traversal depth is capped.
    """
    return _scan(metadata, depth=0)


def _scan(node: Any, depth: int) -> Optional[str]:
    if depth > 4 or node is None:
        return None
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and _FORBIDDEN_KEYS.match(k.strip()):
                return f"forbidden key '{k}'"
            hit = _scan(v, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(node, (list, tuple)):
        for v in node:
            hit = _scan(v, depth + 1)
            if hit is not None:
                return hit
    elif isinstance(node, str):
        if _FORBIDDEN_VALUE.search(node[:_VALUE_SCAN_LIMIT]):
            return "credential-like value"
    return None


# ---------------------------------------------------------------------- model
@dataclass
class AITelemetryEvent:
    """One normalized application-level AI telemetry event.

    Every metric is optional; ``None`` means the source did not provide it.
    ``test_only`` marks TEST/FIXTURE/SYNTHETIC events that must never be
    mistaken for real observations.
    """

    event_type: str
    source: str
    timestamp: Optional[str] = None          # ISO-8601 (assigned at ingest)
    event_id: Optional[str] = None           # assigned at ingest if absent
    agent_id: Optional[str] = None
    agent_name: Optional[str] = None
    model_id: Optional[str] = None
    tool_name: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    status: Optional[str] = None             # ok / error / retry / ...
    duration_ms: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    tps: Optional[float] = None
    context_tokens: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    test_only: bool = False
    # runtime graph hint computed by the buffer (frontend consumes this)
    runtime: Optional[dict[str, Any]] = None

    def finalize(self, now: float | None = None) -> "AITelemetryEvent":
        """Fill timestamp/event_id and compute total_tokens/tps when the
        source provided enough real evidence."""
        now = time.time() if now is None else now
        if self.timestamp is None:
            from datetime import datetime, timezone

            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        if self.event_id is None:
            self.event_id = f"{int(now * 1000):013d}-{uuid.uuid4().hex[:8]}"
        if self.total_tokens is None and self.input_tokens is not None \
                and self.output_tokens is not None:
            self.total_tokens = self.input_tokens + self.output_tokens
        # TPS is derived ONLY from real token + duration evidence from the
        # same source — never estimated from network bytes.
        if self.tps is None and self.output_tokens is not None \
                and self.duration_ms is not None and self.duration_ms > 0:
            self.tps = round(self.output_tokens / (self.duration_ms / 1000.0), 2)
        return self

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "source": self.source,
            "event_type": self.event_type,
            "test_only": self.test_only,
        }
        for name in (
            "agent_id", "agent_name", "model_id", "tool_name", "trace_id",
            "span_id", "parent_span_id", "status", "duration_ms",
            "input_tokens", "output_tokens", "total_tokens", "tps",
            "context_tokens",
        ):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.metadata:
            out["metadata"] = self.metadata
        if self.runtime:
            out["runtime"] = self.runtime
        return out
