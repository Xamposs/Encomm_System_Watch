"""Synthetic network activity provider — LOGICAL TIER2 validation only.

NOT a real network-observation source. This provider exists for the
acceptance harness and manual verification: it fabricates metadata events
for ESTABLISHED loopback connections to a target port and pushes them
through the REAL production chain (bounded queue -> drain -> record_many
-> aggregator -> edge mapping -> WebSocket batch -> GraphController ->
EdgePulseOverlay). Because the events carry real tuples taken from the
actual socket table, they map to real topology edges — so the full logical
TIER2 pipeline is exercised without administrator privileges.

Truthfulness rules:
  - activated ONLY via ESW_TELEMETRY_PROVIDER=synthetic (never the default);
  - the capability it reports is labeled SYNTHETIC so the UI, acceptance
    tests and reports can never mistake it for real ETW-observed bytes;
  - payloads are never touched (metadata only, like the ETW provider).

Do NOT use this as a substitute for real TIER2 validation on an elevated
backend — it proves wiring and decay, not ETW observation.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Optional

import psutil

from .base import Capability, NetworkActivityEvent, NetworkActivityProvider

DEFAULT_TARGET_PORT = 19735  # tools/network_activity_test harness port (Test S)


def _size(value) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


class SyntheticActivityProvider(NetworkActivityProvider):
    """Test-only provider: fabricates events for real loopback sockets.

    The background thread scans the socket table (psutil) for ESTABLISHED
    loopback connections involving ``target_port`` and emits OUT + IN
    metadata events per scan tick. Bytes are synthetic; tuples are real.
    """

    name = "synthetic-test"

    def __init__(
        self,
        target_port: int = DEFAULT_TARGET_PORT,
        rate_hz: float = 100.0,
        bytes_per_event: int = 4096,
    ) -> None:
        self._target_port = target_port
        self._interval = 1.0 / max(1.0, rate_hz)
        self._bytes = max(1, bytes_per_event)
        self._queue: collections.deque[NetworkActivityEvent] = collections.deque()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._capability = Capability(
            level="TIER2",
            source="SYNTHETIC TEST PROVIDER (logical)",
            detail="test-only fabricated events over real loopback sockets — "
                   "NOT real ETW observation; use for pipeline/decay validation",
            elevation_required=False,
            readiness="ACTIVE",
        )
        self._counters = {"events_received": 0, "events_dropped": 0, "events_drained": 0}

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="esw-synthetic-provider", daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def mark_degraded(self) -> None:
        self._capability = Capability(
            level="TIER0", source="NONE",
            detail="synthetic provider stopped; falling back to socket lifecycle",
            elevation_required=False,
            readiness="DEGRADED",
        )

    def capability(self) -> Capability:
        return self._capability

    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------- generator

    def _run(self) -> None:
        """Fabricate events for REAL loopback sockets without load spikes.

        The socket table is scanned at a modest rate (2 Hz — the collector
        also reads psutil and concurrent hammering corrupts pid attribution),
        and the matched tuples are cached in a registry. A fast ticker then
        emits OUT+IN metadata events for the cached tuples (~100 Hz), so the
        byte-rate shape is realistic while psutil is touched ~2×/s.
        """
        registry: set[tuple[int, str, int, str, int]] = set()
        next_scan = 0.0
        while not self._stop.is_set():
            t0 = time.time()
            if t0 >= next_scan:
                try:
                    conns = psutil.net_connections(kind="tcp")
                except Exception:
                    conns = []
                fresh: set[tuple[int, str, int, str, int]] = set()
                for c in conns:
                    if c.status != "ESTABLISHED" or c.pid is None:
                        continue
                    if c.laddr is None or c.raddr is None:
                        continue
                    lip, lport = c.laddr
                    rip, rport = c.raddr
                    if not (lip.startswith("127.") and rip.startswith("127.")):
                        continue
                    if lport != self._target_port and rport != self._target_port:
                        continue
                    fresh.add((c.pid, lip, lport, rip, rport))
                registry = fresh  # drops tuples whose socket closed (<=0.5 s)
                next_scan = t0 + 0.5
            for tup in registry:
                self._fabricate(*tup)
            elapsed = time.time() - t0
            self._stop.wait(max(0.0, self._interval - elapsed))

    def _fabricate(self, pid: int, lip: str, lport: int, rip: str, rport: int) -> None:
        """One OUT + one IN event for a real socket tuple (metadata only)."""
        now = time.time()
        for direction in ("OUT", "IN"):
            ev = NetworkActivityEvent(
                ts=now, pid=pid, protocol="tcp", direction=direction,
                local_ip=lip, local_port=lport,
                remote_ip=rip, remote_port=rport,
                size=self._bytes,
            )
            self.emit(ev)

    # -------------------------------------------------------------- public

    def emit(self, ev: NetworkActivityEvent) -> None:
        """Thread-safe append (also the unit-test seam: inject fake events
        exactly like the ETW callback does, then drain())."""
        with self._lock:
            self._queue.append(ev)
            self._counters["events_received"] += 1
            if len(self._queue) > 20000:
                self._queue.popleft()
                self._counters["events_dropped"] += 1

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
