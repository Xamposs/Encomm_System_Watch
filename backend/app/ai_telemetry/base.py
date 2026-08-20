"""AI telemetry provider interface (Phase 17).

Every provider is independently failure-isolated: a broken source must
never take the registry (or the app) down. Providers are READ-ONLY
adapters — they observe, never control.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from .models import AITelemetryEvent

# provider states (section 26 of the phase brief)
STATE_ACTIVE = "ACTIVE"                      # interface found, real data flowing
STATE_AVAILABLE_NO_DATA = "AVAILABLE_NO_DATA"  # interface found, no activity signals yet
STATE_UNAVAILABLE = "UNAVAILABLE"            # no interface found
STATE_DEGRADED = "DEGRADED"                  # interface found but failing


class TelemetryProvider(ABC):
    """Base class for normalized AI telemetry sources."""

    name: str = "base"
    kind: str = "base"
    test_only: bool = False  # True for TEST/FIXTURE/SYNTHETIC providers

    def __init__(self) -> None:
        self._state = STATE_UNAVAILABLE
        self._detail = "not started"
        self._last_error: Optional[str] = None
        self._last_poll: float = 0.0
        self._availability: dict[str, bool] = {
            "runs": False, "sessions": False, "model_requests": False,
            "tool_calls": False, "mcp_calls": False, "tokens": False,
            "tps": False, "latency": False, "traces": False,
        }

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Called once at registry start. Must never raise."""

    def stop(self) -> None:
        """Called once at registry shutdown. Must never raise."""

    # ----------------------------------------------------------------- poll
    @abstractmethod
    def poll(self, now: float | None = None) -> list[AITelemetryEvent]:
        """One observation round. Returns normalized events (empty when
        nothing new happened). Must never raise — wrap everything."""

    # ---------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self._state,
            "detail": self._detail,
            "test_only": self.test_only,
            "availability": dict(self._availability),
            "last_poll": self._last_poll,
            "last_error": self._last_error,
        }

    def _set_state(self, state: str, detail: str, availability: dict[str, bool] | None = None) -> None:
        self._state = state
        self._detail = detail
        if availability is not None:
            self._availability = availability
        self._last_poll = time.time()

    def _fail(self, err: str) -> None:
        self._last_error = err
