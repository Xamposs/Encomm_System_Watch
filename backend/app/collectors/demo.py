"""Synthetic DEMO collector.

Used ONLY when ESW_DEMO_MODE=1. Generates plausible-but-fake process and
connection activity so the frontend can be developed/reviewed without a live
system. Every snapshot produced through this collector is explicitly flagged
as demo mode by the backend, and the UI shows a DEMO badge.
"""
from __future__ import annotations

import math
import random
import socket
import time

from ..models.entities import ConnectionInfo, ProcessInfo

PROC_TEMPLATES = [
    ("chrome.exe", 6), ("msedge.exe", 4), ("python.exe", 3), ("node.exe", 3),
    ("vscode.exe", 2), ("explorer.exe", 1), ("svchost.exe", 6), ("dwm.exe", 1),
    ("powershell.exe", 2), ("cmd.exe", 1), ("git.exe", 1), ("spotify.exe", 1),
    ("discord.exe", 1), ("steam.exe", 1), ("postgres.exe", 2), ("redis-server.exe", 1),
    ("nginx.exe", 2), ("docker-desktop.exe", 2), ("slack.exe", 1), ("teams.exe", 1),
    ("obs64.exe", 1), ("vlc.exe", 1), ("audiodg.exe", 1), ("lsass.exe", 1),
    ("csrss.exe", 1), ("winlogon.exe", 1), ("dllhost.exe", 1), ("taskhostw.exe", 1),
    ("TextInputHost.exe", 1), ("SearchHost.exe", 1), ("widgets.exe", 1), ("notepad.exe", 1),
]

EXTERNAL_IPS = [
    "104.18.22.44", "142.250.190.78", "151.101.2.132", "185.199.108.133",
    "20.190.160.14", "34.120.208.123", "52.98.190.2", "23.56.88.44",
]


class DemoCollector:
    """Deterministic-ish fake collector: stable process set, time-varying activity."""

    skipped = 0

    def __init__(self) -> None:
        self._t0 = time.time()
        self._tick = 0
        self._rng = random.Random(7)
        self._procs: dict[str, ProcessInfo] = {}
        self._conns: dict[str, ConnectionInfo] = {}
        self._build_processes()
        self._build_connections()

    def _build_processes(self) -> None:
        pid = 500
        for name, weight in PROC_TEMPLATES:
            for _ in range(weight):
                pid += 1
                ct = self._t0 - self._rng.uniform(60, 60 * 60 * 12)
                base_cpu = self._rng.uniform(0.1, 4.0) if weight > 1 else self._rng.uniform(4, 30)
                self._procs[f"proc:{pid}:{int(ct * 1000)}"] = ProcessInfo(
                    pid=pid, create_time=ct, name=name,
                    exe=f"C:\\Program Files\\Demo\\{name}",
                    username="demo\\user", status="running",
                    cpu_percent=round(base_cpu, 1),
                    memory_mb=round(self._rng.uniform(8, 900), 1),
                    num_threads=self._rng.randint(4, 40), ppid=pid - 1,
                    cmdline=[name, "--demo-mode"],
                )

    def _build_connections(self) -> None:
        self._conns = {}
        by_name: dict[str, list[ProcessInfo]] = {}
        for p in self._procs.values():
            by_name.setdefault(p.name, []).append(p)

        def first(name: str) -> ProcessInfo | None:
            lst = by_name.get(name)
            return lst[0] if lst else None

        # listening sockets
        for name, port in (("node.exe", 3000), ("postgres.exe", 5432), ("nginx.exe", 80),
                           ("redis-server.exe", 6379), ("python.exe", 8000)):
            p = first(name)
            if not p:
                continue
            c = ConnectionInfo(pid=p.pid, proto="tcp", local_ip="127.0.0.1", local_port=port,
                               remote_ip="", remote_port=0, state="LISTEN", kind="listening")
            self._conns[c.key] = c

        # localhost pairs (process -> process)
        pairs = [("vscode.exe", "node.exe", 3000), ("python.exe", "postgres.exe", 5432),
                 ("chrome.exe", "node.exe", 3000), ("msedge.exe", "nginx.exe", 80)]
        for a, b, port in pairs:
            pa, pb = first(a), first(b)
            if not pa or not pb:
                continue
            c = ConnectionInfo(pid=pa.pid, proto="tcp", local_ip="127.0.0.1", local_port=self._rng.randint(49152, 60000),
                               remote_ip="127.0.0.1", remote_port=port, state="ESTABLISHED", kind="localhost")
            self._conns[c.key] = c

        # external connections from browser-ish processes
        for name, n in (("chrome.exe", 5), ("msedge.exe", 3), ("spotify.exe", 2), ("discord.exe", 2)):
            lst = by_name.get(name) or []
            for i in range(min(n, len(lst))):
                p = lst[i]
                ip = EXTERNAL_IPS[self._rng.randrange(len(EXTERNAL_IPS))]
                c = ConnectionInfo(pid=p.pid, proto="tcp", local_ip="192.168.1.20",
                                   local_port=self._rng.randint(49152, 60000),
                                   remote_ip=ip, remote_port=443, state="ESTABLISHED", kind="external")
                self._conns[c.key] = c

    def collect_processes(self) -> tuple[dict[str, ProcessInfo], dict[int, str]]:
        self._tick += 1
        t = time.time() - self._t0
        # churn: spawn a scratch process every 5 ticks, drop one every 11
        if self._tick % 5 == 0:
            pid = max(p.pid for p in self._procs.values()) + 1
            ct = time.time() - 0.5
            self._procs[f"proc:{pid}:{int(ct * 1000)}"] = ProcessInfo(
                pid=pid, create_time=ct, name="notepad.exe",
                exe="C:\\Windows\\System32\\notepad.exe", username="demo\\user",
                status="running", cpu_percent=0.2, memory_mb=6.0,
                num_threads=4, ppid=4000, cmdline=["notepad.exe"],
            )
        if self._tick % 11 == 0 and len(self._procs) > 80:
            sid = self._rng.choice(list(self._procs.keys()))
            if self._procs[sid].name not in ("svchost.exe", "lsass.exe"):
                del self._procs[sid]

        pid_map: dict[int, str] = {}
        for sid, p in self._procs.items():
            phase = hash(sid) % 100
            p.cpu_percent = round(max(0.0, min(96.0, p.cpu_percent + 3.0 * math.sin(t / 2.5 + phase))), 1)
            p.memory_mb = round(max(2.0, p.memory_mb + 2.0 * math.sin(t / 7.0 + phase / 3)), 1)
            pid_map[p.pid] = sid

        # rotate one external connection occasionally so open/close events exist
        if self._tick % 4 == 0:
            chrome = [p for p in self._procs.values() if p.name == "chrome.exe"]
            if chrome:
                p = self._rng.choice(chrome)
                ip = EXTERNAL_IPS[self._rng.randrange(len(EXTERNAL_IPS))]
                key = f"tcp|{p.pid}|192.168.1.20|{p.pid + 50000}|{ip}|443"
                self._conns[key] = ConnectionInfo(
                    pid=p.pid, proto="tcp", local_ip="192.168.1.20",
                    local_port=p.pid + 50000, remote_ip=ip, remote_port=443,
                    state="ESTABLISHED", kind="external",
                )
        return self._procs, pid_map

    def collect_network(self, pid_map: dict[int, str]) -> tuple[dict[str, ConnectionInfo], dict[str, str | None]]:
        conns: dict[str, ConnectionInfo] = {}
        owner: dict[str, str | None] = {}
        for ckey, c in self._conns.items():
            if c.pid is not None and c.pid not in pid_map:
                continue
            conns[ckey] = c
            owner[ckey] = pid_map.get(c.pid) if c.pid is not None else None
        return conns, owner

    def collect_system(self) -> dict:
        t = time.time() - self._t0
        return {
            "hostname": socket.gethostname(),
            "platform": "Windows 11 (DEMO)",
            "cpu_count": 16,
            "cpu_percent": round(max(4.0, min(80.0, 14 + 10 * math.sin(t / 4))), 1),
            "mem_percent": round(38 + 3 * math.sin(t / 9), 1),
            "mem_used_gb": round(13.4, 2),
            "mem_total_gb": round(32.0, 2),
            "boot_ts": time.time() - 60 * 60 * 5,
            "ts": time.time(),
        }
