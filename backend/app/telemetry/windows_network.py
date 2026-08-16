"""Windows network activity sources.

Primary provider: ETW session on the ``Microsoft-Windows-TCPIP`` provider
(GUID {2F07E2EE-15DB-40F1-90EF-9D7BA282188A}) via pywintrace. Each packet
generates a metadata event (task SENDIPV4/SENDIPV6/RECEIVEIPV4/RECEIVEIPV6)
carrying PID, size, source/destination addresses and ports, plus direction
via the task name. Payload bytes are never exposed by the provider; only
the aggregated size field is retained.

Empirically validated on Windows 11 (unelevated): enabling this provider
fails with ERROR_ACCESS_DENIED unless the process is elevated. The provider
detects this, reports ``elevation_required`` and the app falls back to the
socket-lifecycle tier. SYSTEM WATCH never auto-elevates.

Alternative documented sources (same privilege requirements, not used):
  - GetPerTcpConnectionEStats (iphlpapi) — per-connection byte counters,
    TCP only, no UDP, poll-based. rc=5 (access denied) when unelevated.
  - Microsoft-Windows-Network-DataUsage ETW provider — per-app usage,
    analytic channel, also requires elevation.
ETW TCPIP is preferred because it is event-driven, covers UDP, and exposes
explicit direction without polling.
"""
from __future__ import annotations

import collections
import socket
import struct
import threading
import time
from typing import Optional

import psutil

from .base import Capability, NetworkActivityEvent, NetworkActivityProvider

TCPIP_GUID = "{2F07E2EE-15DB-40F1-90EF-9D7BA282188A}"
ALL_KEYWORDS = 0xFFFFFFFFFFFFFFFF

SEND_TASKS = {"SENDIPV4", "SENDIPV6"}
RECV_TASKS = {"RECEIVEIPV4", "RECEIVEIPV6"}
EVENT_TASKS = SEND_TASKS | RECV_TASKS

_ELEVATION_DETAIL = (
    "ETW Microsoft-Windows-TCPIP requires administrator privileges "
    "(ERROR_ACCESS_DENIED); run the backend elevated to enable per-edge traffic"
)


def _norm_ip(value) -> str:
    """Normalize an ETW address field (string, int, or raw bytes) to text."""
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("["):
            v = v[1:-1]
        low = v.lower()
        if low.startswith("::ffff:"):  # IPv4-mapped IPv6
            return v[7:]
        return v
    if isinstance(value, int):
        try:
            return socket.inet_ntoa(struct.pack(">I", value & 0xFFFFFFFF))
        except Exception:
            return ""
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
        try:
            if len(b) == 4:
                return socket.inet_ntop(socket.AF_INET, b)
            if len(b) == 16:
                return socket.inet_ntop(socket.AF_INET6, b)
        except Exception:
            return ""
    return ""


def _norm_port(value) -> int:
    try:
        return max(0, min(65535, int(value)))
    except Exception:
        return 0


def _norm_size(value) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


class EtwTcpipProvider(NetworkActivityProvider):
    """ETW Microsoft-Windows-TCPIP provider (per-packet metadata only)."""

    name = "etw-tcpip"

    def __init__(self, session_name: str = "esw-telemetry") -> None:
        self._session_name = session_name
        self._session = None
        self._queue: collections.deque[NetworkActivityEvent] = collections.deque()
        self._lock = threading.Lock()
        self._capability = Capability(
            level="TIER0", source="NONE",
            detail="telemetry provider not started", elevation_required=True,
        )
        # read-only diagnostics (exposed via /api/telemetry/debug)
        self._counters = {"events_received": 0, "events_dropped": 0, "events_drained": 0}

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        try:
            import etw  # pywintrace (FireEye ETW bindings)
        except Exception as exc:  # pragma: no cover - depends on install
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"pywintrace unavailable: {exc}", elevation_required=True,
            )
            return False
        try:
            provider = etw.ProviderInfo(
                "Microsoft-Windows-TCPIP", TCPIP_GUID, any_keywords=ALL_KEYWORDS,
            )
            session = etw.ETW(
                session_name=self._session_name,
                providers=[provider],
                event_callback=self._on_event,
                ring_buf_size=1024,
            )
            session.start()
        except PermissionError:
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=_ELEVATION_DETAIL, elevation_required=True,
            )
            return False
        except OSError as exc:  # includes WindowsError / access denied variants
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"ETW start failed: {exc}", elevation_required=True,
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"ETW start failed: {exc}", elevation_required=True,
            )
            return False
        self._session = session
        self._capability = Capability(
            level="TIER2", source="WINDOWS ETW (Microsoft-Windows-TCPIP)",
            detail="per-packet metadata: pid, addresses, ports, direction, size",
            elevation_required=False,
        )
        return True

    def stop(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass

    def capability(self) -> Capability:
        return self._capability

    def alive(self) -> bool:
        session = self._session
        if session is None or not getattr(session, "running", False):
            return False
        consumer = getattr(session, "consumer", None)
        thread = getattr(consumer, "process_thread", None)
        return thread is None or thread.is_alive()

    # ------------------------------------------------------------- callback

    def _on_event(self, event_tufo) -> None:
        """Runs on the ETW consumer thread. Parses + buffers, never blocks."""
        try:
            _, out = event_tufo
            task = str(out.get("Task Name", "")).upper()
            if task not in EVENT_TASKS:
                return
            if task in SEND_TASKS:
                direction = "OUT"
                local_ip, remote_ip = out.get("saddr"), out.get("daddr")
                local_port, remote_port = out.get("sport"), out.get("dport")
            else:
                direction = "IN"
                local_ip, remote_ip = out.get("daddr"), out.get("saddr")
                local_port, remote_port = out.get("dport"), out.get("sport")
            pid = out.get("PID")
            if pid is None:
                return
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                return
            size = _norm_size(out.get("size"))
            if size <= 0:
                return  # pure ACKs carry no data bytes; skip to reduce noise
            local_ip = _norm_ip(local_ip)
            remote_ip = _norm_ip(remote_ip)
            if not local_ip or not remote_ip:
                return
            ev = NetworkActivityEvent(
                ts=time.time(),
                pid=pid,
                protocol="tcp",
                direction=direction,
                local_ip=local_ip,
                local_port=_norm_port(local_port),
                remote_ip=remote_ip,
                remote_port=_norm_port(remote_port),
                size=size,
            )
            with self._lock:
                self._queue.append(ev)
                self._counters["events_received"] += 1
                if len(self._queue) > 20000:  # bounded memory under load
                    self._queue.popleft()
                    self._counters["events_dropped"] += 1
        except Exception:
            # a malformed event must never kill the ETW consumer thread
            pass

    def drain(self) -> list[NetworkActivityEvent]:
        with self._lock:
            out = list(self._queue)
            self._queue.clear()
            self._counters["events_drained"] += len(out)
        return out

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def counters(self) -> dict:
        with self._lock:
            return dict(self._counters)


class AdapterTotalsSampler:
    """System-wide per-interface byte counters (psutil), delta -> bps.

    These are ADAPTER TOTALS: the sum over all physical interfaces. They are
    a different measurement from captured process telemetry (which may miss
    elevated/system processes and UDP), so they are reported separately and
    labeled explicitly in the UI.
    """

    def __init__(self) -> None:
        self._last: Optional[tuple[int, int, float]] = None  # sent, recv, ts

    def sample(self) -> Optional[tuple[float, float]]:
        """Return (down_bps, up_bps) or None before the first delta exists."""
        try:
            pernic = psutil.net_io_counters(pernic=True)
        except Exception:
            return None
        total_sent = 0
        total_recv = 0
        for name, c in pernic.items():
            low = name.lower()
            if "loopback" in low or low.startswith("lo"):
                continue
            total_sent += c.bytes_sent
            total_recv += c.bytes_recv
        now = time.time()
        if self._last is None:
            self._last = (total_sent, total_recv, now)
            return None
        prev_sent, prev_recv, prev_ts = self._last
        dt = now - prev_ts
        self._last = (total_sent, total_recv, now)
        if dt <= 0:
            return None
        down = max(0.0, (total_recv - prev_recv) / dt)
        up = max(0.0, (total_sent - prev_sent) / dt)
        return down, up
