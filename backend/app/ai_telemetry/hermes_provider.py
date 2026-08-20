"""Hermes gateway status provider (Phase 17) — REAL read-only adapter.

Reconnaissance result on this machine (2026-08-20):

  Hermes desktop app + gateways ARE running. Each gateway
  (``python -m hermes_cli.main [--profile X] serve --host 127.0.0.1
  --port 0``) binds a DYNAMIC localhost port and exposes an HTTP API.
  The unauthenticated surface is exactly two endpoints:

    GET /api/health   -> {"ok": true, "version": "...", "auth_required": false}
    GET /api/status   -> version, active_agents, active_sessions,
                         gateway_platforms[*].state, components[*].status,
                         profiles, ...

  Everything deeper (/api/sessions, /api/agents, /api/analytics/usage,
  /api/mcp/servers, ...) requires authentication (HTTP 401) and/or would
  expose private conversation content — SYSTEM WATCH never authenticates
  and never scrapes content, so those metric families truthfully report
  UNAVAILABLE:

    tokens, TPS, per-request model, tool names, MCP calls, trace ids
    -> NO SAFE STRUCTURED INTERFACE FOUND (401-protected / not exposed)

  What this provider CAN truthfully observe (deltas of real counts):
    - gateway presence + version
    - active agent runs (count)      -> AGENT_RUN_STARTED / FINISHED
    - active sessions (count)        -> metrics only (a session is not a run)
    - platform connection states     -> AI_ERROR only when error_code appears
    - component error states         -> AI_ERROR only on "error"

  Run identity: the status API exposes COUNTS ONLY, so individual run ids
  are inferred FIFO per gateway from count deltas and the metadata always
  records count_before/count_after — the evidence is transparent, never
  invented. This is a deliberate, documented approximation of identity,
  NOT of the counts themselves.

  Privacy: only the fields listed above are extracted. install_id,
  hermes_home, config_path, env_path and all other payload fields are
  never serialized into events.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any, Optional

from .base import STATE_ACTIVE, STATE_AVAILABLE_NO_DATA, STATE_DEGRADED, \
    STATE_UNAVAILABLE, TelemetryProvider
from .models import (
    AITelemetryEvent,
    EVENT_AGENT_RUN_FINISHED,
    EVENT_AGENT_RUN_STARTED,
    EVENT_AI_ERROR,
)

_GATEWAY_CMDLINE = re.compile(r"hermes_cli\.main", re.IGNORECASE)
_PROFILE_ARG = re.compile(r"--profile[= ](\S+)", re.IGNORECASE)
_TIMEOUT_S = 3.0


class _GatewayEndpoint:
    __slots__ = ("pid", "port", "profile", "base", "agents", "sessions",
                 "prev_agents", "prev_sessions", "run_queue", "seq",
                 "platforms", "components", "version", "healthy", "failing")

    def __init__(self, pid: int, port: int, profile: str) -> None:
        self.pid = pid
        self.port = port
        self.profile = profile
        self.base = f"http://127.0.0.1:{port}"
        self.agents = 0
        self.sessions = 0
        self.prev_agents: Optional[int] = None
        self.prev_sessions: Optional[int] = None
        self.run_queue: deque[str] = deque(maxlen=64)
        self.seq = 0
        self.platforms: dict[str, dict[str, Any]] = {}
        self.components: dict[str, str] = {}
        self.version: Optional[str] = None
        self.healthy = True
        self.failing = 0


class HermesGatewayProvider(TelemetryProvider):
    """Read-only adapter over the Hermes gateway status API.

    Discovery is dynamic: every poll re-scans the process table for
    ``hermes_cli.main ... serve`` processes and maps their PIDs to
    localhost listeners (real socket evidence). ``ESW_HERMES_API_URL``
    overrides discovery with an explicit endpoint (still read-only GETs).
    """

    name = "hermes"
    kind = "hermes-gateway-status"

    def __init__(self, poll_interval_s: float = 5.0) -> None:
        super().__init__()
        self.poll_interval_s = poll_interval_s
        self._endpoints: dict[str, _GatewayEndpoint] = {}
        self._override = os.environ.get("ESW_HERMES_API_URL", "").strip().rstrip("/")

    # ------------------------------------------------------------ discovery
    def _discover(self) -> dict[str, _GatewayEndpoint]:
        if self._override:
            return self._endpoints or self._override_endpoint()
        found: dict[str, _GatewayEndpoint] = {}
        try:
            import psutil
        except Exception:  # noqa: BLE001 — psutil is always present here
            return {}
        try:
            conns = psutil.net_connections(kind="tcp")
        except Exception:  # noqa: BLE001
            conns = []
        listeners: dict[int, int] = {}
        for c in conns:
            if c.status == "LISTEN" and c.laddr and c.laddr.ip in ("127.0.0.1", "::1"):
                listeners[c.pid] = c.laddr.port
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmd = p.info.get("cmdline") or []
            except Exception:  # noqa: BLE001 — vanishing process
                continue
            joined = " ".join(cmd)
            if not _GATEWAY_CMDLINE.search(joined) or " serve" not in f" {joined} ":
                continue
            pid = p.info["pid"]
            port = listeners.get(pid)
            if port is None:
                continue  # gateway without a localhost listener: no API to read
            m = _PROFILE_ARG.search(joined)
            profile = m.group(1) if m else "default"
            key = f"{pid}:{port}"
            found[key] = self._endpoints.get(key) or _GatewayEndpoint(pid, port, profile)
        return found

    def _override_endpoint(self) -> dict[str, _GatewayEndpoint]:
        ep = _GatewayEndpoint(pid=0, port=0, profile="override")
        ep.base = self._override
        return {"override": ep}

    # ----------------------------------------------------------------- poll
    def poll(self, now: float | None = None) -> list[AITelemetryEvent]:
        now = time.time() if now is None else now
        events: list[AITelemetryEvent] = []
        try:
            endpoints = self._discover()
        except Exception:  # noqa: BLE001 — discovery must never raise
            endpoints = {}
        self._endpoints = endpoints

        if not endpoints:
            self._set_state(
                STATE_UNAVAILABLE,
                "no Hermes gateway process with a localhost listener found — "
                "semantic Hermes detection is a separate, independent signal",
                availability={k: False for k in self._availability},
            )
            return events

        for key, ep in endpoints.items():
            try:
                events += self._poll_endpoint(ep, now)
            except Exception:  # noqa: BLE001 — per-endpoint failure isolation
                ep.failing += 1
                self._fail(f"gateway {ep.base} poll failed")

        ok = sum(1 for ep in endpoints.values() if ep.healthy)
        if ok == 0:
            self._set_state(
                STATE_DEGRADED,
                f"{len(endpoints)} Hermes gateway(s) found but status API "
                "unreachable",
                availability=self._availability,
            )
        elif ok < len(endpoints):
            self._set_state(
                STATE_DEGRADED,
                f"{ok}/{len(endpoints)} Hermes gateways responding",
                availability=self._availability,
            )
        else:
            total_agents = sum(ep.agents for ep in endpoints.values())
            total_sessions = sum(ep.sessions for ep in endpoints.values())
            avail = dict(self._availability)
            avail["runs"] = True
            avail["sessions"] = True
            if total_agents == 0 and total_sessions == 0:
                self._set_state(
                    STATE_AVAILABLE_NO_DATA,
                    f"Hermes gateway status API reachable ({len(endpoints)} "
                    "gateway(s)) — zero active agents/sessions observed",
                    availability=avail,
                )
            else:
                self._set_state(
                    STATE_ACTIVE,
                    f"Hermes gateway status API reachable — "
                    f"{total_agents} agent run(s), {total_sessions} session(s) "
                    "(counts only; tokens/TPS/tool names are NOT exposed "
                    "without auth — deep telemetry UNAVAILABLE)",
                    availability=avail,
                )
        return events

    # ------------------------------------------------------- one endpoint
    def _poll_endpoint(self, ep: _GatewayEndpoint, now: float) -> list[AITelemetryEvent]:
        events: list[AITelemetryEvent] = []
        health = self._get_json(ep, "/api/health")
        status = self._get_json(ep, "/api/status")
        if health is None or status is None:
            # any failed fetch this poll degrades the endpoint; a full
            # success restores it (fail-fast, restore-lazy)
            ep.failing += 1
            ep.healthy = False
            return events
        ep.failing = 0
        ep.healthy = True
        ep.version = status.get("version") or health.get("version") or ep.version

        agents = int(status.get("active_agents") or 0)
        sessions = int(status.get("active_sessions") or 0)
        meta_common = {
            "profile": ep.profile,
            "version": ep.version,
            "gateway": ep.base,
        }

        # ---- run lifecycle: real count deltas ----------------------------
        if ep.prev_agents is not None and agents != ep.prev_agents:
            if agents > ep.prev_agents:
                for _ in range(agents - ep.prev_agents):
                    ep.seq += 1
                    run_id = f"hermes:{ep.profile}:{ep.seq}"
                    ep.run_queue.append(run_id)
                    events.append(AITelemetryEvent(
                        event_type=EVENT_AGENT_RUN_STARTED,
                        source="hermes-gateway-status",
                        agent_id=run_id,
                        agent_name=f"Hermes ({ep.profile})",
                        status="active",
                        metadata={
                            **meta_common,
                            "count_before": ep.prev_agents,
                            "count_after": agents,
                            "identity": "count-inferred (gateway exposes counts only)",
                        },
                    ))
            else:
                for _ in range(ep.prev_agents - agents):
                    run_id = ep.run_queue.popleft() if ep.run_queue else \
                        f"hermes:{ep.profile}:{ep.seq - (ep.prev_agents - agents) - 1}"
                    events.append(AITelemetryEvent(
                        event_type=EVENT_AGENT_RUN_FINISHED,
                        source="hermes-gateway-status",
                        agent_id=run_id,
                        agent_name=f"Hermes ({ep.profile})",
                        status="finished",
                        metadata={
                            **meta_common,
                            "count_before": ep.prev_agents,
                            "count_after": agents,
                            "identity": "count-inferred (gateway exposes counts only)",
                        },
                    ))
        ep.prev_agents = agents
        ep.agents = agents
        ep.prev_sessions = sessions
        ep.sessions = sessions

        # ---- platform error states (change-only, error evidence only) ----
        platforms = status.get("gateway_platforms") or {}
        if isinstance(platforms, dict) and platforms != ep.platforms:
            for name, p in platforms.items():
                if not isinstance(p, dict):
                    continue
                prev = ep.platforms.get(name) or {}
                if p.get("error_code") or p.get("error_message"):
                    if not (prev.get("error_code") or prev.get("error_message")):
                        events.append(AITelemetryEvent(
                            event_type=EVENT_AI_ERROR,
                            source="hermes-gateway-status",
                            agent_name="Hermes",
                            status="error",
                            metadata={
                                **meta_common,
                                "platform": name,
                                "state": p.get("state"),
                                "error_code": p.get("error_code"),
                                "error_message": p.get("error_message"),
                            },
                        ))
            ep.platforms = dict(platforms)

        # ---- component error states (change-only) ------------------------
        comps = status.get("components") or {}
        if isinstance(comps, dict):
            comp_states = {
                k: (v.get("status") if isinstance(v, dict) else str(v))
                for k, v in comps.items()
            }
            if comp_states != ep.components:
                for name, st in comp_states.items():
                    prev = ep.components.get(name)
                    if st == "error" and prev != "error":
                        events.append(AITelemetryEvent(
                            event_type=EVENT_AI_ERROR,
                            source="hermes-gateway-status",
                            agent_name="Hermes",
                            status="error",
                            metadata={**meta_common, "component": name, "state": st},
                        ))
                ep.components = comp_states
        return events

    # ---------------------------------------------------------------- http
    def _get_json(self, ep: _GatewayEndpoint, path: str) -> Optional[dict]:
        url = f"{ep.base}{path}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310 — localhost-only (127.0.0.1)
                if resp.status != 200:
                    return None
                body = resp.read(256 * 1024)
                data = json.loads(body)
                return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 — any network/parse failure
            return None
