"""READ-ONLY ETW attribution health detector (v0.3.1).

Detects the long-run ETW degradation documented at the 0.3.0 checkpoint: a
stale ``esw-telemetry`` ETW session keeps delivering provider events while
``events_mapped_to_edges`` stays frozen (edge attribution no longer works),
even though the topology still contains active connections.

The detector ONLY observes counters and reports a truthful state. It never
stops logman, restarts ETW sessions, restarts the backend, or kills
processes — the operator restarts manually.

States
------
``N/A``        no telemetry provider active (nothing to watch)
``OK``         attribution is moving (mapped counter increases) or the
               provider is quiet
``WATCHING``   provider events increasing but mapped frozen for less than
               the freeze threshold (pre-warning)
``DEGRADED``   provider events increasing but mapped frozen >= threshold
               while topology edges exist -> "ETW ATTRIBUTION DEGRADED"
``PROVIDER_DEAD`` provider stopped reporting ``alive`` (the capability
               demotion already handles this; reported for completeness)

The state machine is fully deterministic and wall-clock free: every call
passes ``now`` explicitly, so unit tests drive it with synthetic timelines.
"""
from __future__ import annotations

from typing import Any


class EtwAttributionHealth:
    def __init__(self, freeze_threshold_s: float = 45.0,
                 sample_interval_s: float = 5.0) -> None:
        self._threshold = freeze_threshold_s
        self._interval = sample_interval_s
        self._last_sample: float | None = None
        self._state = "N/A"
        self._previous_state = "N/A"
        self._mapped_last: int | None = None
        self._events_last: int | None = None
        self._frozen_since: float | None = None
        self._transition_emitted: bool = True  # nothing to emit at startup

    # ------------------------------------------------------------ sampling

    def sample(self, provider: dict[str, Any], aggregator: dict[str, Any],
               edges_tracked: int, active_conns: int, now: float) -> dict[str, Any]:
        """Feed one observation window. Throttled internally by
        ``sample_interval_s``; returns the current health dict either way."""
        if self._last_sample is not None and now - self._last_sample < self._interval:
            return self.state_dict(now)
        self._last_sample = now

        events = int(provider.get("events_received", 0) or 0)
        mapped = int(aggregator.get("events_mapped_to_edges", 0) or 0)
        alive = bool(provider.get("alive", True))

        if not provider:
            self._set_state("N/A", now, None)
        elif not alive:
            self._set_state("PROVIDER_DEAD", now, None)
        elif events <= 0:
            # provider running but nothing observed yet — nothing to attribute
            self._set_state("OK", now, None)
        elif self._mapped_last is None:
            # first sample: establish the baseline
            self._mapped_last = mapped
            self._events_last = events
            self._set_state("OK", now, None)
        elif mapped > self._mapped_last:
            # attribution is moving — healthy (even if events also climb)
            self._mapped_last = mapped
            self._events_last = events
            self._set_state("OK", now, None)
        elif events > self._events_last:
            # events arriving, mapped frozen: the exact degradation signature
            self._events_last = events
            if self._frozen_since is None:
                self._frozen_since = now
            frozen_for = now - self._frozen_since
            if frozen_for >= self._threshold:
                self._set_state("DEGRADED", now, frozen_for)
            else:
                self._set_state("WATCHING", now, frozen_for)
        else:
            # everything quiet — nothing new to attribute
            self._set_state("OK", now, None)
        return self.state_dict(now)

    def _set_state(self, state: str, now: float, frozen_for: float | None) -> None:
        if state != self._state:
            # transition: re-arm the one-shot event emission
            self._previous_state = self._state
            self._state = state
            self._transition_emitted = False
        if state != "DEGRADED" and state != "WATCHING":
            self._frozen_since = None
        elif self._frozen_since is None and frozen_for is not None:
            self._frozen_since = now - frozen_for

    # ------------------------------------------------------------ reporting

    @property
    def state(self) -> str:
        return self._state

    def state_dict(self, now: float | None = None) -> dict[str, Any]:
        frozen_for = None
        if self._frozen_since is not None and now is not None:
            frozen_for = round(now - self._frozen_since, 1)
        message = ""
        if self._state == "DEGRADED":
            message = (
                "ETW ATTRIBUTION DEGRADED — provider events are increasing but "
                "events_mapped_to_edges has been frozen for "
                f"{frozen_for or 0:.0f}s while tracked edges exist. "
                "Restart manually: logman stop esw-telemetry -ets, then restart "
                "the backend. No automatic action was taken."
            )
        elif self._state == "WATCHING":
            message = (
                "ETW attribution frozen for "
                f"{frozen_for or 0:.0f}s — will report DEGRADED at "
                f"{self._threshold:.0f}s unless mapping resumes."
            )
        elif self._state == "PROVIDER_DEAD":
            message = "Telemetry provider is not alive (capability was demoted)."
        return {
            "state": self._state,
            "message": message,
            "freeze_threshold_s": self._threshold,
            "sample_interval_s": self._interval,
            "frozen_for_s": frozen_for,
            "events_received": self._events_last,
            "events_mapped_to_edges": self._mapped_last,
        }

    def consume_transition(self) -> dict[str, Any] | None:
        """One-shot: returns the current state dict when the state just
        changed (so the caller can publish a single WS event), else None.
        The dict includes ``previous_state`` so the caller can decide
        whether the transition is worth surfacing."""
        if self._transition_emitted:
            return None
        self._transition_emitted = True
        d = self.state_dict()
        d["previous_state"] = self._previous_state
        return d

    def reset(self) -> None:
        self._last_sample = None
        self._state = "N/A"
        self._previous_state = "N/A"
        self._mapped_last = None
        self._events_last = None
        self._frozen_since = None
        self._transition_emitted = True
