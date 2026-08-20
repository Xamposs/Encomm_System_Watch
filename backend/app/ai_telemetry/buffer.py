"""Bounded AI telemetry buffer + trace correlation (Phase 17).

Caps (section 25 of the phase brief):

- active traces:    20   (AGENT_RUN_STARTED without FINISHED)
- recent spans:    100   (individual MODEL/TOOL/MCP spans)
- event history:   500   (chronological evidence, feeds the drawer)
- run TTL:        600 s  (a run left unfinished is pruned by age)

Pruning is strictly by count cap + TTL — no unbounded maps. The buffer
also computes the compact ``runtime`` graph hint each event carries so
the frontend can place bounded transient runtime nodes without guessing.
"""
from __future__ import annotations

import time
from collections import OrderedDict, deque
from typing import Any, Optional

from .models import (
    AITelemetryEvent,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    RUN_END_TYPES,
    RUN_START_TYPES,
)

CAP_EVENT_HISTORY = 500
CAP_ACTIVE_TRACES = 20
CAP_RECENT_SPANS = 100
RUN_TTL_S = 600.0
SPAN_TTL_S = 300.0


class _Trace:
    __slots__ = ("trace_id", "agent_id", "agent_name", "run_node_id",
                 "started_at", "last_seen", "span_count", "model_ids",
                 "tool_names", "status", "test_only")

    def __init__(self, trace_id: str, agent_id: Optional[str],
                 agent_name: Optional[str], run_node_id: str,
                 now: float, test_only: bool) -> None:
        self.trace_id = trace_id
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.run_node_id = run_node_id
        self.started_at = now
        self.last_seen = now
        self.span_count = 0
        self.model_ids: set[str] = set()
        self.tool_names: set[str] = set()
        self.status = "active"
        self.test_only = test_only


class AiTelemetryBuffer:
    def __init__(self) -> None:
        self.history: deque[AITelemetryEvent] = deque(maxlen=CAP_EVENT_HISTORY)
        self.traces: OrderedDict[str, _Trace] = OrderedDict()
        self.recent_spans: deque[AITelemetryEvent] = deque(maxlen=CAP_RECENT_SPANS)
        self._seq = 0
        # sliding token window for a truthful tokens/s (real events only)
        self._token_window: deque[tuple[float, int]] = deque(maxlen=4096)

    # ---------------------------------------------------------------- ingest
    def ingest(self, ev: AITelemetryEvent, now: float | None = None) -> AITelemetryEvent:
        """Store one event, correlate it with its trace, attach the runtime
        graph hint. Returns the finalized event."""
        now = time.time() if now is None else now
        ev.finalize(now)
        trace_id = ev.trace_id
        runtime: dict[str, Any] = {}

        if ev.event_type in RUN_START_TYPES:
            run_id = trace_id or ev.event_id or f"run-{self._seq}"
            node_id = f"ai:run:{run_id}"
            runtime = {
                "node_id": node_id,
                "parent_node_id": None,
                "kind": "AGENT_RUN",
                "label": f"AGENT RUN{': ' + ev.agent_name if ev.agent_name else ''}",
                "test_only": ev.test_only,
            }
            if trace_id:
                tr = _Trace(trace_id, ev.agent_id, ev.agent_name, node_id,
                            now, ev.test_only)
                self.traces[trace_id] = tr
                self.traces.move_to_end(trace_id)
                self._prune_traces(now)
        elif ev.event_type in RUN_END_TYPES:
            if trace_id and trace_id in self.traces:
                tr = self.traces.pop(trace_id)
                runtime = {
                    "node_id": tr.run_node_id,
                    "parent_node_id": None,
                    "kind": "AGENT_RUN",
                    "label": f"AGENT RUN{': ' + (tr.agent_name or ev.agent_name or '')}",
                    "test_only": tr.test_only or ev.test_only,
                    "finished": True,
                }
            else:
                runtime = {
                    "node_id": f"ai:run:{trace_id or ev.event_id}",
                    "parent_node_id": None,
                    "kind": "AGENT_RUN",
                    "test_only": ev.test_only,
                    "finished": True,
                }
        else:
            parent = self.traces.get(trace_id) if trace_id else None
            kind = ev.event_type.split("_STARTED")[0].split("_FINISHED")[0]
            if kind == "MODEL_REQUEST":
                kind = "MODEL_REQUEST"
            elif kind == "TOOL_CALL":
                kind = "TOOL_CALL"
            elif kind == "MCP_CALL":
                kind = "MCP_CALL"
            else:
                kind = "AI_EVENT"
            node_id = f"ai:{kind.lower()}:{ev.span_id or ev.event_id}"
            runtime = {
                "node_id": node_id,
                "parent_node_id": parent.run_node_id if parent else None,
                "kind": kind,
                "test_only": ev.test_only,
                "label": kind.replace("_", " ").title()
                + (f" · {ev.tool_name}" if ev.tool_name else "")
                + (f" · {ev.model_id}" if ev.model_id else ""),
            }
            if parent is not None:
                parent.last_seen = now
                parent.span_count += 1
                if ev.model_id:
                    parent.model_ids.add(ev.model_id)
                if ev.tool_name:
                    parent.tool_names.add(ev.tool_name)
                if ev.status == "error":
                    parent.status = "error"
            if ev.span_id:
                self.recent_spans.append(ev)

        ev.runtime = runtime
        self.history.append(ev)
        # token window: only REAL token evidence from the source
        if ev.total_tokens is not None and not ev.test_only:
            self._token_window.append((now, ev.total_tokens))
        self._seq += 1
        self._prune_traces(now)
        self._prune_spans(now)
        return ev

    # ---------------------------------------------------------------- prune
    def _prune_traces(self, now: float) -> None:
        while len(self.traces) > CAP_ACTIVE_TRACES:
            self.traces.popitem(last=False)
        stale = [tid for tid, tr in self.traces.items()
                 if now - tr.last_seen > RUN_TTL_S]
        for tid in stale:
            self.traces.pop(tid, None)

    def _prune_spans(self, now: float) -> None:
        while self.recent_spans and now - _ts_of(self.recent_spans[0]) > SPAN_TTL_S:
            self.recent_spans.popleft()

    # --------------------------------------------------------------- metrics
    def active_runs(self) -> list[dict[str, Any]]:
        out = []
        for tid, tr in self.traces.items():
            out.append({
                "trace_id": tid,
                "agent_id": tr.agent_id,
                "agent_name": tr.agent_name,
                "node_id": tr.run_node_id,
                "started_at": tr.started_at,
                "spans": tr.span_count,
                "models": sorted(tr.model_ids),
                "tools": sorted(tr.tool_names),
                "status": tr.status,
                "test_only": tr.test_only,
            })
        return out

    def metrics(self, now: float | None = None) -> dict[str, Any]:
        """Compact metrics — only fields with real values."""
        now = time.time() if now is None else now
        m: dict[str, Any] = {"runs": len(self.traces)}
        # tokens/s over the last 60 s (real token events only)
        cutoff = now - 60.0
        window = [t for t, _n in self._token_window if t >= cutoff]
        if window:
            m["tokens_per_s"] = round(len(window) and sum(
                n for t, n in self._token_window if t >= cutoff) / 60.0, 1)
        tools: set[str] = set()
        models: set[str] = set()
        for tr in self.traces.values():
            tools.update(tr.tool_names)
            models.update(tr.model_ids)
        if tools:
            m["tools"] = len(tools)
        if models:
            m["model"] = sorted(models)[0]
        errs = [e for e in self.history if e.event_type == "AI_ERROR" and not e.test_only]
        if errs:
            m["errors"] = len(errs)
        return m

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        return {
            "active_runs": self.active_runs(),
            "metrics": self.metrics(now),
            "history_count": len(self.history),
            "recent_spans_count": len(self.recent_spans),
            "caps": {
                "event_history": CAP_EVENT_HISTORY,
                "active_traces": CAP_ACTIVE_TRACES,
                "recent_spans": CAP_RECENT_SPANS,
            },
        }


def _ts_of(ev: AITelemetryEvent) -> float:
    try:
        from datetime import datetime

        return datetime.fromisoformat(ev.timestamp or "").timestamp()
    except Exception:  # noqa: BLE001 — malformed ts degrades to now
        return time.time()
