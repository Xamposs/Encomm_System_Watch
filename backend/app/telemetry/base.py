"""Network telemetry — normalized activity events and capability tiers.

Capability tiers (truthful, never faked):

  TIER2  per-connection / per-edge byte activity (Windows ETW
         Microsoft-Windows-TCPIP provider: pid, addresses, ports,
         direction, size — metadata only, no payloads).
  TIER0  socket lifecycle only (CONNECTION_OPENED / CONNECTION_CLOSED).

Adapter totals (psutil per-interface byte counters) are always available
independently and are reported separately from captured telemetry, because
the two numbers are different measurements (system-wide vs. captured).

Elevation: enabling the TCPIP ETW provider requires administrator rights.
SYSTEM WATCH never auto-elevates; when the provider cannot be enabled the
app continues on the lower tier and reports the exact reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Capability:
    """What network telemetry is actually available right now."""

    level: str = "TIER0"           # TIER2 | TIER0
    source: str = "NONE"           # e.g. "WINDOWS ETW (Microsoft-Windows-TCPIP)"
    detail: str = "socket lifecycle only"
    elevation_required: bool = False
    enabled: bool = True           # False when telemetry is disabled (demo mode)

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "source": self.source,
            "detail": self.detail,
            "elevation_required": self.elevation_required,
            "enabled": self.enabled,
        }


@dataclass
class NetworkActivityEvent:
    """One observed network data event (metadata only — never payload)."""

    ts: float
    pid: Optional[int]
    protocol: str                  # "tcp" | "udp" (best effort from ETW)
    direction: str                 # "IN" | "OUT" relative to the owning socket
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    size: int                      # bytes in this event

    def to_dict(self) -> dict:
        return {
            "timestamp": round(self.ts, 3),
            "pid": self.pid,
            "protocol": self.protocol,
            "direction": self.direction,
            "local_ip": self.local_ip,
            "local_port": self.local_port,
            "remote_ip": self.remote_ip,
            "remote_port": self.remote_port,
            "bytes": self.size,
        }


class NetworkActivityProvider:
    """Interface for a Windows network activity source.

    Implementations must be safe to run from a background thread:
    ``start()`` may block briefly, the event callback runs on the ETW
    consumer thread, and ``drain()`` must be thread-safe.
    """

    name: str = "base"

    def start(self) -> bool:
        """Attempt to start the provider. Returns True when active."""
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def capability(self) -> Capability:
        raise NotImplementedError

    def drain(self) -> list[NetworkActivityEvent]:
        """Thread-safe: take and clear all buffered events."""
        raise NotImplementedError

    def alive(self) -> bool:
        """True while the provider is expected to keep delivering events."""
        return True

    def queue_depth(self) -> int:
        """Current buffered-event count (diagnostics)."""
        return 0

    def counters(self) -> dict:
        """Read-only diagnostic counters (diagnostics)."""
        return {}


@dataclass
class EdgeRateState:
    """Runtime activity state for one topology edge (not persisted)."""

    fwd_bps: float = 0.0           # bytes/sec travelling source -> target
    rev_bps: float = 0.0           # bytes/sec travelling target -> source
    last_activity: float = 0.0     # monotonic/epoch ts of last observed bytes
    level: int = 0                 # 0 idle, 1 low, 2 medium, 3 high
    last_seen: float = 0.0         # ts edge was present in topology

    def to_dict(self) -> dict:
        return {
            "fwd_bps": round(self.fwd_bps, 1),
            "rev_bps": round(self.rev_bps, 1),
            "last_activity": round(self.last_activity, 3),
            "level": self.level,
        }


@dataclass
class ProcessRateState:
    """Per-process aggregate activity (node halo / inspector data)."""

    down_bps: float = 0.0
    up_bps: float = 0.0
    last_activity: float = 0.0

    def to_dict(self) -> dict:
        return {
            "down_bps": round(self.down_bps, 1),
            "up_bps": round(self.up_bps, 1),
            "last_activity": round(self.last_activity, 3),
        }
