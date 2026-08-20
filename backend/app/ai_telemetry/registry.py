"""AI telemetry registry + async poll loop (Phase 17).

Owns the provider set and the bounded buffer. The loop polls every
provider (each failure-isolated), ingests normalized events, and
publishes three WebSocket message types — only on changes/activity:

  ai_activity         events (runtime evidence + drawer rows)
  ai_metrics          compact metrics (changed only)
  ai_provider_status  provider state transitions only

Fixture mode is env-gated (``ESW_AI_TELEMETRY_FIXTURE=1``); while active
every message carries the fixture marker so TEST data can never be
mistaken for real observations.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, Optional

from .base import STATE_ACTIVE, STATE_UNAVAILABLE, TelemetryProvider
from .buffer import AiTelemetryBuffer
from .models import AITelemetryEvent, contains_private_content

log = logging.getLogger("esw.ai")

# publish cadence for metrics even when nothing changed but runs are active
_METRICS_ALIVE_INTERVAL_S = 10.0


class AiTelemetryRegistry:
    def __init__(self, poll_interval_s: float = 5.0) -> None:
        self.poll_interval_s = poll_interval_s
        self.buffer = AiTelemetryBuffer()
        self.providers: list[TelemetryProvider] = []
        self.publish_message: Callable[[dict], None] = lambda _m: None
        self.publish_events: Callable[[list[dict]], None] = lambda _e: None
        self.fixture_mode = False
        self._last_provider_status: Optional[dict] = None
        self._last_metrics: Optional[dict] = None
        self._last_metrics_at = 0.0
        self._last_publish_events: list[dict] = []

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Build the provider set. Env-gated fixture provider; otherwise
        the real Hermes gateway adapter + the OTEL seam."""
        if os.environ.get("ESW_AI_TELEMETRY_FIXTURE", "").strip().lower() in ("1", "true", "yes"):
            from .fixture_provider import FixtureAiProvider

            self.fixture_mode = True
            self.providers = [FixtureAiProvider()]
            log.warning("AI telemetry FIXTURE mode active — TEST/SYNTHETIC data only")
        else:
            from .hermes_provider import HermesGatewayProvider
            from .otel_provider import OtelSeam

            self.providers = [
                HermesGatewayProvider(poll_interval_s=self.poll_interval_s),
                OtelSeam(),
            ]
        for p in self.providers:
            try:
                p.start()
            except Exception:  # noqa: BLE001 — provider start must never kill
                log.warning("ai provider %s start failed", p.name, exc_info=True)

    def stop(self) -> None:
        for p in self.providers:
            try:
                p.stop()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ loop
    async def run_loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:  # noqa: BLE001 — registry must never die
                log.warning("ai telemetry tick failed", exc_info=True)
            await asyncio.sleep(self.poll_interval_s)

    def _tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        events: list[AITelemetryEvent] = []
        for p in self.providers:
            try:
                events += p.poll(now)
            except Exception:  # noqa: BLE001 — per-provider failure isolation
                log.warning("ai provider %s poll failed", p.name, exc_info=True)
        ingested = [self.buffer.ingest(ev, now) for ev in events]
        if ingested:
            self._publish_activity(ingested)
        self._publish_status(now)
        self._publish_metrics(now)

    # ------------------------------------------------------------ publishing
    def _publish_activity(self, events: list[AITelemetryEvent]) -> None:
        payload = {
            "type": "ai_activity",
            "ts": time.time(),
            "fixture": self.fixture_mode,
            "events": [e.to_dict() for e in events],
        }
        self.publish_message(payload)
        # drawer rows (reuse the generic event envelope)
        rows = []
        for e in events:
            d = e.to_dict()
            rows.append({
                "event_id": d.get("event_id"),
                "event_type": d.get("event_type"),
                "source": d.get("source"),
                "target": None,
                "timestamp": d.get("timestamp"),
                "metadata": {
                    "agent_id": d.get("agent_id"),
                    "agent_name": d.get("agent_name"),
                    "model_id": d.get("model_id"),
                    "tool_name": d.get("tool_name"),
                    "status": d.get("status"),
                    "duration_ms": d.get("duration_ms"),
                    "total_tokens": d.get("total_tokens"),
                    "tps": d.get("tps"),
                    "trace_id": d.get("trace_id"),
                    "test_only": d.get("test_only", False),
                    "fixture": self.fixture_mode,
                },
            })
        if rows:
            self.publish_events(rows)

    def _publish_status(self, now: float) -> None:
        states = {p.name: p.status() for p in self.providers}
        if states == self._last_provider_status:
            return
        self._last_provider_status = states
        self.publish_message({
            "type": "ai_provider_status",
            "ts": now,
            "fixture": self.fixture_mode,
            "providers": states,
        })

    def _publish_metrics(self, now: float) -> None:
        metrics = self.buffer.metrics(now)
        changed = metrics != self._last_metrics
        alive = metrics.get("runs", 0) > 0 and now - self._last_metrics_at >= _METRICS_ALIVE_INTERVAL_S
        if not changed and not alive:
            return
        self._last_metrics = metrics
        self._last_metrics_at = now
        self.publish_message({
            "type": "ai_metrics",
            "ts": now,
            "fixture": self.fixture_mode,
            "metrics": metrics,
        })

    # -------------------------------------------------------------- ingestion
    def ingest_external(
        self,
        event_type: str,
        source: str,
        *,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        model_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        status: Optional[str] = None,
        duration_ms: Optional[float] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        tps: Optional[float] = None,
        context_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        test_only: bool = False,
    ) -> AITelemetryEvent:
        """Validate + store one event from the local ingestion endpoint.

        Raises :class:`ValueError` when the payload carries private
        content (privacy gate). Never executes anything.
        """
        meta = dict(metadata or {})
        hit = contains_private_content(meta)
        if hit is not None:
            raise ValueError(f"private content rejected: {hit}")
        ev = AITelemetryEvent(
            event_type=event_type,
            source=source,
            agent_id=agent_id,
            agent_name=agent_name,
            model_id=model_id,
            tool_name=tool_name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            status=status,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            tps=tps,
            context_tokens=context_tokens,
            metadata=meta,
            test_only=test_only,
        )
        ingested = self.buffer.ingest(ev)
        self._publish_activity([ingested])
        return ingested

    # --------------------------------------------------------------- status
    def snapshot(self) -> dict[str, Any]:
        return {
            "fixture_mode": self.fixture_mode,
            "providers": {p.name: p.status() for p in self.providers},
            **self.buffer.snapshot(),
        }
