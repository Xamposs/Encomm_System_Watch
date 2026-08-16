"""Machine-level metrics (CPU, RAM, platform info)."""
from __future__ import annotations

import platform
import socket
import time

import psutil


class SystemCollector:
    def __init__(self) -> None:
        self._cpu = 0.0

    def collect(self) -> dict:
        self._cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        return {
            "hostname": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "cpu_count": psutil.cpu_count() or 1,
            "cpu_percent": round(self._cpu, 1),
            "mem_percent": round(vm.percent, 1),
            "mem_used_gb": round(vm.used / (1024 ** 3), 2),
            "mem_total_gb": round(vm.total / (1024 ** 3), 2),
            "boot_ts": psutil.boot_time(),
            "ts": time.time(),
        }
