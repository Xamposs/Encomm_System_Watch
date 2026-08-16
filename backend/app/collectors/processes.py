"""Real Windows process collection.

Every field is fetched defensively: a single inaccessible or vanishing process
must never crash the collector. Processes are identified by a stable id built
from PID + creation time, because Windows reuses PIDs.

Performance notes (measured on a 294-process Windows 11 desktop):
  - psutil Process objects cache name/create_time/exe/username/ppid, so those
    are near-free after the first pass.
  - status/num_threads/cmdline are stable per process: cached here with a TTL.
  - memory_info + cpu_times change every tick and are the irreducible cost;
    they are fetched in ONE batched as_dict() call (~0.6s for ~300 processes)
    which is ~2x faster than two separate calls. psutil does not parallelize
    across threads on Windows, so no thread pool is used.
"""
from __future__ import annotations

import time
from typing import Optional

import psutil

from ..models.entities import ProcessInfo

_META_TTL_S = 60.0


class ProcessCollector:
    def __init__(self) -> None:
        self._prev_cpu: dict[str, tuple[float, float]] = {}  # stable_id -> (cpu_time_total, wall_monotonic)
        self._meta: dict[str, tuple[float, Optional[int], str, int, Optional[str], Optional[str], list[str]]] = {}
        self._cpu_count = max(1, psutil.cpu_count() or 1)
        self.skipped = 0

    @staticmethod
    def stable_id(pid: int, create_time: float) -> str:
        return f"proc:{pid}:{int(create_time * 1000)}"

    def collect(self) -> tuple[dict[str, ProcessInfo], dict[int, str]]:
        now_mono = time.monotonic()
        now_wall = time.time()
        procs: dict[str, ProcessInfo] = {}
        pid_map: dict[int, str] = {}
        skipped = 0

        try:
            raw = list(psutil.process_iter())
        except Exception:
            return {}, {}

        for p in raw:
            try:
                pid = p.pid
                try:
                    create_time = p.create_time()
                except Exception:
                    skipped += 1  # vanished between enumeration and inspection
                    continue
                sid = self.stable_id(pid, create_time)

                try:
                    name = p.name() or "unknown"
                except Exception:
                    name = "unknown"

                # batched memory + cpu in one call (fast path)
                try:
                    info = p.as_dict(attrs=["memory_info", "cpu_times"])
                    mem = info["memory_info"].rss / (1024.0 * 1024.0)
                    cpu_total = info["cpu_times"].user + info["cpu_times"].system
                except Exception:
                    try:
                        mem = p.memory_info().rss / (1024.0 * 1024.0)
                    except Exception:
                        mem = 0.0
                    try:
                        ct = p.cpu_times()
                        cpu_total = ct.user + ct.system
                    except Exception:
                        cpu_total = None

                cpu_percent = 0.0
                if cpu_total is not None:
                    prev = self._prev_cpu.get(sid)
                    if prev is not None:
                        dt = now_mono - prev[1]
                        if dt > 0.05:
                            cpu_percent = (cpu_total - prev[0]) / dt / self._cpu_count * 100.0
                            cpu_percent = max(0.0, min(999.0, cpu_percent))
                    self._prev_cpu[sid] = (cpu_total, now_mono)

                # stable metadata, cached with TTL
                meta = self._meta.get(sid)
                if meta is None or now_wall - meta[0] > _META_TTL_S:
                    def _safe(fn):
                        try:
                            return fn()
                        except Exception:
                            return None

                    ppid = _safe(p.ppid)
                    status = _safe(p.status) or "unknown"
                    threads = _safe(p.num_threads) or 0
                    exe = _safe(p.exe)
                    username = _safe(p.username)
                    cmdline = _safe(p.cmdline) or []
                    meta = (now_wall, ppid, status, threads, exe, username, cmdline[:8])
                    self._meta[sid] = meta
                _, ppid, status, threads, exe, username, cmdline = meta

                procs[sid] = ProcessInfo(
                    pid=pid,
                    create_time=create_time,
                    name=name,
                    exe=exe,
                    username=username,
                    status=status,
                    cpu_percent=round(cpu_percent, 1),
                    memory_mb=round(mem, 1),
                    num_threads=threads,
                    ppid=ppid,
                    cmdline=cmdline,
                )
                pid_map[pid] = sid
            except Exception:
                skipped += 1

        self._prev_cpu = {k: v for k, v in self._prev_cpu.items() if k in procs}
        self._meta = {k: v for k, v in self._meta.items() if k in procs}
        self.skipped = skipped
        return procs, pid_map
