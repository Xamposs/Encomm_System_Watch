"""Local ingestion endpoint schema (Phase 17).

POST /api/ai-telemetry/events — for EXPLICIT TRUSTED LOCAL
INSTRUMENTATION ONLY. The endpoint is a metadata sink, never a control
surface: it cannot execute tools, launch agents, call models, send MCP
commands, run shell commands or mutate any system state (proven by
security tests). Payloads are bounded, schema-whitelisted and scanned
for private content.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import ALL_EVENT_TYPES

MAX_BODY_BYTES = 64 * 1024        # whole-request bound (checked by endpoint)
MAX_METADATA_KEYS = 32
MAX_STRING_LEN = 128
MAX_METADATA_KEY_LEN = 64
MAX_METADATA_VALUE_LEN = 512


class AITelemetryEventIn(BaseModel):
    """Normalized AI telemetry event from explicit local instrumentation.

    ``extra="forbid"`` rejects any unknown top-level field (no arbitrary
    JSON dumping). All optional metrics stay absent when unavailable.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    event_type: str = Field(min_length=1, max_length=64)
    agent_id: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    agent_name: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    model_id: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    tool_name: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    trace_id: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    span_id: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    parent_span_id: Optional[str] = Field(default=None, max_length=MAX_STRING_LEN)
    status: Optional[str] = Field(default=None, max_length=32)
    duration_ms: Optional[float] = Field(default=None, ge=0, le=86_400_000.0)
    input_tokens: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    output_tokens: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    total_tokens: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    tps: Optional[float] = Field(default=None, ge=0, le=1_000_000_000.0)
    context_tokens: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    test_only: bool = False

    @field_validator("event_type")
    @classmethod
    def _event_type_supported(cls, v: str) -> str:
        if v not in ALL_EVENT_TYPES:
            raise ValueError(
                f"unsupported event_type '{v}' (supported: "
                + ", ".join(sorted(ALL_EVENT_TYPES)) + ")"
            )
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata_bounded(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata exceeds {MAX_METADATA_KEYS} keys")
        for k, val in v.items():
            if not isinstance(k, str) or len(k) > MAX_METADATA_KEY_LEN:
                raise ValueError("metadata keys must be strings ≤ 64 chars")
            if isinstance(val, str) and len(val) > MAX_METADATA_VALUE_LEN:
                raise ValueError(f"metadata value for '{k}' exceeds {MAX_METADATA_VALUE_LEN} chars")
        return v
