"""Windows network activity sources.

Primary provider: ETW session on the ``Microsoft-Windows-TCPIP`` provider
(GUID {2F07E2EE-15DB-40F1-90EF-9D7BA282188A}) via pywintrace. Only event
METADATA is retained (pid, 4-tuple, direction, byte count, timestamp) —
payload bytes are never exposed by the provider.

Two TCP event families are supported (empirically probed on Windows 11,
tcpip.sys 10.0.19041, elevated):

LEGACY (classic TCPIP manifest, still parsed for older Windows):
    SENDIPV4 / SENDIPV6 / RECEIVEIPV4 / RECEIVEIPV6 carry PID, size,
    saddr/daddr/sport/dport directly -> a NetworkActivityEvent is built
    directly (no correlation needed).

MODERN (this machine's actual manifest — task names resolve UPPERCASE):
    TcpDataTransferSend (field ``BytesSent``) and TcpDataTransferReceive
    (field ``NumBytes``) carry ONLY a Tcb handle + per-segment byte count
    — no pid, no addresses. Connection identity comes from separate
    events; the provider correlates Tcb -> (pid, local, remote):

      TcpConnectionRundown        snapshot of connections existing when the
                                  session starts (fields Tcb, Pid,
                                  LocalAddress, RemoteAddress)
      TcpConnectTcbComplete       new OUTBOUND connection completed
                                  (Tcb, ProcessId, addresses)
      TcpConnectTcbProceeding     connect in progress (pid may be 0; only
                                  used when a real pid is present)
      TcpAcceptListenerComplete   new ACCEPTED (server-side) connection
                                  (Tcb, ProcessId, addresses)

    Mappings are removed by: TcpDisconnectTcbComplete, TcpCloseTcbRequest,
    TcpAbortTcbComplete, TcpConnectTcbFailure, TcpConnectTcbFailedRcvRst,
    TcpConnectionTerminatedRcvRst (all keyed by Tcb), TcpRstSend (keyed by
    its sockaddr tuple — its Tcb is 0x0), and TcpTcbStateChange with
    NewState containing CLOSED. A bounded TTL sweep (5 min idle) plus a
    maximum-entry cap prevent the map from growing forever; a stale entry
    can therefore never attribute future traffic to an old connection.

    UDP is self-contained in the modern manifest: UdpEndpointSendMessages /
    UdpEndpointReceiveMessages carry Pid, LocalSockAddr, RemoteSockAddr,
    NumBytes, NumMessages -> attributed directly.

Direction semantics: a TcpDataTransferSend on a TCB means the socket
owning that TCB SENT bytes (OUT); TcpDataTransferReceive means the owning
socket RECEIVED bytes (IN). Byte counts are per-SEGMENT accounting, so
totals approximate (not equal) application payload sizes; retransmissions
and ACK-only segments are excluded by construction (we only read the data
transfer tasks).

Elevation: enabling this provider fails with ERROR_ACCESS_DENIED unless
the process is elevated. The provider detects this, reports
``elevation_required`` and the app falls back to the socket-lifecycle
tier. SYSTEM WATCH never auto-elevates.

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
from dataclasses import dataclass, field
from typing import Optional

import psutil

from .base import Capability, NetworkActivityEvent, NetworkActivityProvider

TCPIP_GUID = "{2F07E2EE-15DB-40F1-90EF-9D7BA282188A}"
ALL_KEYWORDS = 0xFFFFFFFFFFFFFFFF

# ------------------------------------------------------------------ tasks
# Task names arrive UPPERCASE from pywintrace's tdh resolution on this
# build; comparison is case-insensitive so older CamelCase manifests work.

# legacy direct-attribution tasks (classic TCPIP manifest)
LEGACY_SEND_TASKS = {"SENDIPV4", "SENDIPV6"}
LEGACY_RECV_TASKS = {"RECEIVEIPV4", "RECEIVEIPV6"}
LEGACY_EVENT_TASKS = LEGACY_SEND_TASKS | LEGACY_RECV_TASKS

# modern transfer tasks (Tcb + byte count only)
TCP_SEND_TASKS = {"TCPDATATRANSFERSEND"}
TCP_RECV_TASKS = {"TCPDATATRANSFERRECEIVE"}

# modern UDP tasks (self-contained: pid + sockaddrs + bytes)
UDP_SEND_TASKS = {"UDPENDPOINTSENDMESSAGES"}
UDP_RECV_TASKS = {"UDPENDPOINTRECEIVEMESSAGES"}

# identity events: create/refresh a Tcb -> ConnectionIdentity mapping
TCB_CREATE_TASKS = {
    "TCPCONNECTIONRUNDOWN",
    "TCPCONNECTTCBCOMPLETE",
    "TCPCONNECTTCBPROCEEDING",  # only when it carries a real pid
    "TCPACCEPTLISTENERCOMPLETE",
}

# events that terminate a connection: remove the mapping by Tcb
TCB_REMOVE_TASKS = {
    "TCPDISCONNECTTCBCOMPLETE",
    "TCPCLOSETCBREQUEST",
    "TCPABORTTCBCOMPLETE",
    "TCPCONNECTTCBFAILURE",
    "TCPCONNECTTCBFAILEDRCVDRST",
    "TCPCONNECTIONTERMINATEDRCVDRST",
}

# RST sent: Tcb is 0x0 in the event, so removal is keyed on the sockaddr tuple
TCB_REMOVE_BY_TUPLE_TASKS = {"TCPRSTSEND"}

# Tcb lifecycle tuning (boundedness — the map must never grow forever)
TCB_TTL_SECONDS = 300.0      # drop mappings idle longer than 5 minutes
TCB_MAP_MAX_ENTRIES = 8192   # hard cap; oldest entries evicted first

_ELEVATION_DETAIL = (
    "ETW Microsoft-Windows-TCPIP requires administrator privileges "
    "(ERROR_ACCESS_DENIED); run the backend elevated to enable per-edge traffic"
)


# ------------------------------------------------------------- normalization

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


def _normalize_tcb(value) -> Optional[int]:
    """Normalize a TCB handle to one canonical int key.

    Real events deliver Tcb as a hex string like ``'0xFFFF8E0F2627B8A0'``.
    The same underlying TCB must produce the same dict key across
    lifecycle and transfer events, so anything that is not a positive int
    (``0x0``, ``None``, garbage) maps to None and is ignored.
    """
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, (bytes, bytearray)):
        try:
            value = bytes(value).decode("ascii").strip()
        except Exception:
            return None
    if isinstance(value, str):
        try:
            n = int(value.strip(), 0)  # handles "0x...", "0X...", decimal
        except ValueError:
            return None
        return n if n > 0 else None
    return None


def _parse_endpoint(value) -> Optional[tuple[str, int]]:
    """Parse a TCPIP ``'ip:port'`` (or ``'[v6]:port'``) endpoint string.

    Empirically LocalAddress / RemoteAddress / LocalSockAddr /
    RemoteSockAddr are single strings carrying ip AND port, e.g.
    ``'127.0.0.1:62960'`` or ``'[2606:4700:110::c760]:63690'``.
    Returns (ip, port) or None when the value cannot be parsed (including
    port 0 wildcards such as ``'0.0.0.0:0'``).
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    ip: str
    port_s: str
    if v.startswith("["):
        end = v.find("]:")
        if end < 0:
            return None
        ip, port_s = v[1:end], v[end + 2:]
    else:
        # unbracketed values must be plain IPv4:port (ETW always brackets
        # IPv6 with its port); anything with more colons is rejected
        if v.count(":") != 1:
            return None
        idx = v.rfind(":")
        if idx <= 0 or idx == len(v) - 1:
            return None  # no port
        ip, port_s = v[:idx], v[idx + 1:]
    port = _norm_port(port_s)
    if port <= 0:
        return None
    ipn = _norm_ip(ip)
    if not ipn:
        return None
    return ipn, port


def _pid_field(out: dict) -> Optional[int]:
    """Explicit pid field (Pid / PID / ProcessId) as int, or None."""
    for key in ("PID", "Pid", "ProcessId"):
        raw = out.get(key)
        if raw is None:
            continue
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            return None
        return pid
    return None


@dataclass
class _TcbEntry:
    """Connection identity learned for one TCB (metadata only)."""

    pid: int
    protocol: str
    local_ip: str
    local_port: int
    remote_ip: str
    remote_port: int
    last_seen: float = field(default_factory=time.time)


class EtwTcpipProvider(NetworkActivityProvider):
    """ETW Microsoft-Windows-TCPIP provider (per-event metadata only)."""

    name = "etw-tcpip"

    def __init__(self, session_name: str = "esw-telemetry") -> None:
        self._session_name = session_name
        self._session = None
        self._queue: collections.deque[NetworkActivityEvent] = collections.deque()
        self._tcb_map: dict[int, _TcbEntry] = {}
        self._lock = threading.Lock()
        self._capability = Capability(
            level="TIER0", source="NONE",
            detail="telemetry provider not started", elevation_required=True,
            readiness="NONE",
        )
        # read-only diagnostics (exposed via /api/telemetry/debug)
        self._counters = {
            "events_received": 0, "events_dropped": 0, "events_drained": 0,
            "tcb_mappings_created": 0, "tcb_mappings_removed": 0,
            "tcb_map_size": 0, "tcb_lookup_hits": 0, "tcb_lookup_misses": 0,
        }

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        try:
            import etw  # pywintrace (FireEye ETW bindings)
        except Exception as exc:  # pragma: no cover - depends on install
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"pywintrace unavailable: {exc}", elevation_required=True,
                readiness="NONE",
            )
            return False
        try:
            # pywintrace's ProviderInfo passes guid to ctypes.byref(), so it
            # must be a ctypes GUID instance — a bare string fails with
            # "byref() argument must be a ctypes instance, not 'str'"
            provider = etw.ProviderInfo(
                "Microsoft-Windows-TCPIP", etw.GUID(TCPIP_GUID), any_keywords=ALL_KEYWORDS,
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
                readiness="NONE",
            )
            return False
        except OSError as exc:  # includes WindowsError / access denied variants
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"ETW start failed: {exc}", elevation_required=True,
                readiness="NONE",
            )
            return False
        except Exception as exc:  # pragma: no cover - defensive
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail=f"ETW start failed: {exc}", elevation_required=True,
                readiness="NONE",
            )
            return False
        self._session = session
        # session alive does NOT prove usable byte attribution — readiness
        # only becomes ACTIVE once the first real data event is correlated
        self._capability = Capability(
            level="TIER2", source="WINDOWS ETW (Microsoft-Windows-TCPIP)",
            detail="per-event metadata: pid, addresses, ports, direction, size",
            elevation_required=False,
            readiness="INITIALIZING",
        )
        return True

    def stop(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.stop()
            except Exception:
                pass

    def mark_degraded(self) -> None:
        """Called by the runtime when the ETW session dies mid-flight."""
        with self._lock:
            self._capability = Capability(
                level="TIER0", source="NONE",
                detail="ETW session ended; falling back to socket lifecycle",
                elevation_required=True,
                readiness="DEGRADED",
            )

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
        except Exception:
            return
        if task in LEGACY_EVENT_TASKS:
            self._handle_legacy(task, out)
        elif task in TCP_SEND_TASKS:
            self._handle_tcp_transfer(out, "OUT", "BytesSent")
        elif task in TCP_RECV_TASKS:
            self._handle_tcp_transfer(out, "IN", "NumBytes")
        elif task in UDP_SEND_TASKS:
            self._handle_udp(out, "OUT")
        elif task in UDP_RECV_TASKS:
            self._handle_udp(out, "IN")
        elif task in TCB_CREATE_TASKS:
            self._handle_tcb_create(out)
        elif task in TCB_REMOVE_TASKS:
            self._handle_tcb_remove(out)
        elif task in TCB_REMOVE_BY_TUPLE_TASKS:
            self._handle_tcb_remove_by_tuple(out)
        elif task == "TCPTCBSTATECHANGE":
            self._handle_state_change(out)

    # -- modern TCP: learn/refresh Tcb -> identity

    def _handle_tcb_create(self, out: dict) -> None:
        tcb = _normalize_tcb(out.get("Tcb"))
        pid = _pid_field(out)
        if tcb is None or pid is None or pid <= 0:
            return
        ep = _parse_endpoint(out.get("LocalAddress"))
        rp = _parse_endpoint(out.get("RemoteAddress"))
        if ep is None or rp is None:
            return
        now = time.time()
        with self._lock:
            self._counters["events_received"] += 1
            if tcb not in self._tcb_map:
                self._counters["tcb_mappings_created"] += 1
            self._tcb_map[tcb] = _TcbEntry(
                pid=pid, protocol="tcp",
                local_ip=ep[0], local_port=ep[1],
                remote_ip=rp[0], remote_port=rp[1],
                last_seen=now,
            )
            if len(self._tcb_map) > TCB_MAP_MAX_ENTRIES:
                self._evict_oldest()

    # -- modern TCP: byte transfer events, resolved through the TCB map

    def _handle_tcp_transfer(self, out: dict, direction: str, size_field: str) -> None:
        tcb = _normalize_tcb(out.get("Tcb"))
        if tcb is None:
            return
        size = _norm_size(out.get(size_field))
        now = time.time()
        with self._lock:
            self._counters["events_received"] += 1
            entry = self._tcb_map.get(tcb)
            if entry is None:
                self._counters["tcb_lookup_misses"] += 1
                return
            self._counters["tcb_lookup_hits"] += 1
            if size <= 0:
                return  # pure ACKs carry no data bytes; skip to reduce noise
            entry.last_seen = now
            ev = NetworkActivityEvent(
                ts=now,
                pid=entry.pid,
                protocol=entry.protocol,
                direction=direction,
                local_ip=entry.local_ip,
                local_port=entry.local_port,
                remote_ip=entry.remote_ip,
                remote_port=entry.remote_port,
                size=size,
            )
            self._queue.append(ev)
            if len(self._queue) > 20000:  # bounded memory under load
                self._queue.popleft()
                self._counters["events_dropped"] += 1
            if self._capability.readiness != "ACTIVE":
                self._capability.readiness = "ACTIVE"

    # -- modern UDP: self-contained (pid + sockaddrs + bytes)

    def _handle_udp(self, out: dict, direction: str) -> None:
        pid = _pid_field(out)
        if pid is None or pid <= 0:
            return
        ep = _parse_endpoint(out.get("LocalSockAddr"))
        rp = _parse_endpoint(out.get("RemoteSockAddr"))
        if ep is None or rp is None:
            return
        size = _norm_size(out.get("NumBytes"))
        if size <= 0:
            return
        with self._lock:
            self._counters["events_received"] += 1
            ev = NetworkActivityEvent(
                ts=time.time(),
                pid=pid,
                protocol="udp",
                direction=direction,
                local_ip=ep[0],
                local_port=ep[1],
                remote_ip=rp[0],
                remote_port=rp[1],
                size=size,
            )
            self._queue.append(ev)
            if len(self._queue) > 20000:
                self._queue.popleft()
                self._counters["events_dropped"] += 1
            if self._capability.readiness != "ACTIVE":
                self._capability.readiness = "ACTIVE"

    # -- modern TCP: TCB removal (explicit close / failure / abort)

    def _handle_tcb_remove(self, out: dict) -> None:
        tcb = _normalize_tcb(out.get("Tcb"))
        if tcb is None:
            return
        with self._lock:
            self._counters["events_received"] += 1
            if tcb in self._tcb_map:
                del self._tcb_map[tcb]
                self._counters["tcb_mappings_removed"] += 1

    def _handle_tcb_remove_by_tuple(self, out: dict) -> None:
        """TcpRstSend carries Tcb=0x0; remove by matching the sockaddr tuple."""
        ep = _parse_endpoint(out.get("LocalSockAddr"))
        rp = _parse_endpoint(out.get("RemoteSockAddr"))
        if ep is None or rp is None:
            return
        with self._lock:
            self._counters["events_received"] += 1
            for tcb in [
                t for t, e in self._tcb_map.items()
                if (e.local_ip, e.local_port, e.remote_ip, e.remote_port)
                in ((ep[0], ep[1], rp[0], rp[1]), (rp[0], rp[1], ep[0], ep[1]))
            ]:
                del self._tcb_map[tcb]
                self._counters["tcb_mappings_removed"] += 1

    def _handle_state_change(self, out: dict) -> None:
        """TcpTcbStateChange carries only the Tcb; a CLOSED state is final."""
        tcb = _normalize_tcb(out.get("Tcb"))
        if tcb is None:
            return
        new_state = str(out.get("NewState") or "").upper()
        with self._lock:
            self._counters["events_received"] += 1
            if "CLOSED" in new_state and tcb in self._tcb_map:
                del self._tcb_map[tcb]
                self._counters["tcb_mappings_removed"] += 1

    # -- legacy: direct pid + tuple + bytes

    def _handle_legacy(self, task: str, out: dict) -> None:
        if task in LEGACY_SEND_TASKS:
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
            if self._capability.readiness != "ACTIVE":
                self._capability.readiness = "ACTIVE"

    # ------------------------------------------------------------ draining

    def drain(self) -> list[NetworkActivityEvent]:
        with self._lock:
            self._sweep_tcb_map(time.time())
            out = list(self._queue)
            self._queue.clear()
            self._counters["events_drained"] += len(out)
            self._counters["tcb_map_size"] = len(self._tcb_map)
        return out

    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    def counters(self) -> dict:
        with self._lock:
            c = dict(self._counters)
            c["tcb_map_size"] = len(self._tcb_map)
            return c

    # ------------------------------------------------- TCB map housekeeping

    def _sweep_tcb_map(self, now: float) -> None:
        """Drop TTL-stale mappings and enforce the entry cap (cheap, bounded)."""
        stale = [
            t for t, e in self._tcb_map.items()
            if now - e.last_seen > TCB_TTL_SECONDS
        ]
        for t in stale:
            del self._tcb_map[t]
            self._counters["tcb_mappings_removed"] += 1
        if len(self._tcb_map) > TCB_MAP_MAX_ENTRIES:
            self._evict_oldest()

    def _evict_oldest(self) -> None:
        """Evict oldest last_seen entries until under the cap."""
        over = len(self._tcb_map) - TCB_MAP_MAX_ENTRIES
        if over <= 0:
            return
        oldest = sorted(
            self._tcb_map.items(), key=lambda kv: kv[1].last_seen,
        )[:over]
        for t, _ in oldest:
            del self._tcb_map[t]
            self._counters["tcb_mappings_removed"] += 1


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
