"""GPU / VRAM collector (Phase 18).

Primary source: NVML via ``pynvml`` (C-level, cheap, no subprocess).
Fallback: ``nvidia-smi`` CSV parsing when NVML is unavailable.

Rules:
  - multiple GPUs supported from the start (one entry per index)
  - only fields actually exposed are filled; unavailable = omitted
  - never fabricate a value
  - every failure degrades to an empty result — the collector can NEVER
    crash SYSTEM WATCH
  - per-process GPU attribution: NVML compute+graphics process lists;
    per-process VRAM only when the API really provides it
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from typing import Any, Optional

log = logging.getLogger("esw.gpu")

# field key -> nvidia-smi query token (both -u and CSV names)
_SMI_QUERY = [
    ("index", "index"),
    ("name", "name"),
    ("utilization_percent", "utilization.gpu"),
    ("vram_used_mb", "memory.used"),
    ("vram_total_mb", "memory.total"),
    ("temperature_c", "temperature.gpu"),
    ("power_w", "power.draw"),
    ("driver", "driver_version"),
    ("fan_percent", "fan.speed"),
    ("clock_graphics_mhz", "clocks.gr"),
    ("clock_memory_mhz", "clocks.mem"),
]


def _num(v: Any) -> Optional[float]:
    try:
        f = float(str(v).replace(",", "").strip())
        return f
    except (TypeError, ValueError):
        return None


class GpuCollector:
    def __init__(self) -> None:
        self.source: str = "NONE"          # NVML | NVIDIA_SMI | NONE
        self.error: Optional[str] = None
        self._nvml: Any = None
        self._prev_pids: dict[int, set[int]] = {}   # gpu index -> pids (stale tracking)
        self._pids_seen = False
        self._init_nvml()

    # ------------------------------------------------------------ init paths

    def _init_nvml(self) -> None:
        try:
            import pynvml  # type: ignore[import-not-found]

            pynvml.nvmlInit()
            self._nvml = pynvml
            self.source = "NVML"
            log.info("GPU collector: NVML active")
        except Exception as exc:  # noqa: BLE001 — fall back to nvidia-smi
            self._nvml = None
            self.source = "NVIDIA_SMI" if shutil.which("nvidia-smi") else "NONE"
            self.error = f"NVML unavailable: {exc}"
            log.info("GPU collector: NVML unavailable (%s), fallback=%s", exc, self.source)

    # ---------------------------------------------------------------- sample

    def sample(self, with_processes: bool = True) -> list[dict[str, Any]]:
        """One GPU snapshot. Never raises."""
        try:
            if self._nvml is not None:
                return self._sample_nvml(with_processes)
            if self.source == "NVIDIA_SMI":
                return self._sample_smi(with_processes)
        except Exception as exc:  # noqa: BLE001 — collector must never crash
            log.warning("GPU sample failed: %s", exc, exc_info=True)
            self.error = f"{type(exc).__name__}: {exc}"
        return []

    # ------------------------------------------------------------- NVML path

    def _sample_nvml(self, with_processes: bool) -> list[dict[str, Any]]:
        nvml = self._nvml
        out: list[dict[str, Any]] = []
        count = nvml.nvmlDeviceGetCount()
        for idx in range(count):
            h = nvml.nvmlDeviceGetHandleByIndex(idx)
            g: dict[str, Any] = {"index": idx}
            try:
                g["name"] = nvml.nvmlDeviceGetName(h).decode() if isinstance(
                    nvml.nvmlDeviceGetName(h), bytes
                ) else str(nvml.nvmlDeviceGetName(h))
            except Exception:  # noqa: BLE001
                pass
            try:
                g["utilization_percent"] = nvml.nvmlDeviceGetUtilizationRates(h).gpu
            except Exception:  # noqa: BLE001
                pass
            try:
                mem = nvml.nvmlDeviceGetMemoryInfo(h)
                g["vram_used_mb"] = round(mem.used / 1024.0 / 1024.0, 1)
                g["vram_total_mb"] = round(mem.total / 1024.0 / 1024.0, 1)
            except Exception:  # noqa: BLE001
                pass
            try:
                g["temperature_c"] = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
            except Exception:  # noqa: BLE001
                pass
            try:
                g["power_w"] = round(nvml.nvmlDeviceGetPowerUsage(h) / 1000.0, 1)
            except Exception:  # noqa: BLE001
                pass
            try:
                g["driver"] = nvml.nvmlSystemGetDriverVersion().decode() if isinstance(
                    nvml.nvmlSystemGetDriverVersion(), bytes
                ) else str(nvml.nvmlSystemGetDriverVersion())
            except Exception:  # noqa: BLE001
                pass
            try:
                g["fan_percent"] = nvml.nvmlDeviceGetFanSpeed(h)
            except Exception:  # noqa: BLE001
                pass
            try:
                g["clock_graphics_mhz"] = nvml.nvmlDeviceGetClockInfo(h, nvml.NVML_CLOCK_GRAPHICS)
                g["clock_memory_mhz"] = nvml.nvmlDeviceGetClockInfo(h, nvml.NVML_CLOCK_MEM)
            except Exception:  # noqa: BLE001
                pass
            if with_processes:
                g["processes"] = self._nvml_processes(h)
            out.append(g)
        return out

    def _nvml_processes(self, handle) -> list[dict[str, Any]]:
        """Per-process GPU attribution (compute + graphics contexts)."""
        procs: list[dict[str, Any]] = []
        try:
            for pi in self._nvml.nvmlDeviceGetComputeRunningProcesses(handle):
                procs.append(self._proc_dict(pi))
        except Exception:  # noqa: BLE001
            pass
        try:
            for pi in self._nvml.nvmlDeviceGetGraphicsRunningProcesses(handle):
                procs.append(self._proc_dict(pi))
        except Exception:  # noqa: BLE001
            pass
        # dedupe by pid (compute+graphics can both list the same process)
        seen: dict[int, dict[str, Any]] = {}
        for p in procs:
            if p["pid"] in seen:
                old = seen[p["pid"]]
                if p.get("vram_mb") and not old.get("vram_mb"):
                    old["vram_mb"] = p["vram_mb"]
            else:
                seen[p["pid"]] = p
        return sorted(seen.values(), key=lambda p: p["pid"])

    @staticmethod
    def _proc_dict(pi) -> dict[str, Any]:
        pid = int(getattr(pi, "pid", 0) or 0)
        d: dict[str, Any] = {"pid": pid}
        used = getattr(pi, "usedGpuMemory", None)
        if used is not None:
            try:
                d["vram_mb"] = round(int(used) / 1024.0 / 1024.0, 1)
            except (TypeError, ValueError):
                pass
        return d

    # ---------------------------------------------------------- nvidia-smi path

    def _sample_smi(self, with_processes: bool) -> list[dict[str, Any]]:
        query = ",".join(tok for _, tok in _SMI_QUERY)
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            rows = [ln for ln in r.stdout.splitlines() if ln.strip()]
        except Exception as exc:  # noqa: BLE001
            self.error = f"nvidia-smi failed: {exc}"
            return []
        out: list[dict[str, Any]] = []
        for line in rows:
            cells = [c.strip() for c in line.split(",")]
            g: dict[str, Any] = {}
            for (key, _tok), val in zip(_SMI_QUERY, cells):
                if key == "name":
                    if val and val.lower() != "n/a":
                        g[key] = val
                elif key == "index":
                    v = _num(val)
                    if v is not None:
                        g[key] = int(v)
                else:
                    v = _num(val)
                    if v is not None:
                        g[key] = int(v) if key in (
                            "utilization_percent", "temperature_c", "fan_percent",
                            "clock_graphics_mhz", "clock_memory_mhz",
                        ) else v
            if "index" not in g:
                g["index"] = len(out)
            if with_processes:
                g["processes"] = self._smi_processes()
            out.append(g)
        return out

    def _smi_processes(self) -> list[dict[str, Any]]:
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
        except Exception:  # noqa: BLE001
            return []
        procs: list[dict[str, Any]] = []
        for line in r.stdout.splitlines():
            cells = [c.strip() for c in line.split(",")]
            if len(cells) < 2:
                continue
            pid = _num(cells[0])
            if pid is None:
                continue
            d: dict[str, Any] = {"pid": int(pid)}
            vram = _num(cells[2]) if len(cells) > 2 else None
            if vram is not None and vram > 0:
                d["vram_mb"] = vram
            procs.append(d)
        return procs

    # --------------------------------------------------------- stale tracking

    def changed_pids(self, gpus: list[dict[str, Any]]) -> tuple[set[int], set[int]]:
        """(attached, detached) GPU pids vs the previous sample.

        The collector keeps the previous per-GPU pid sets so the caller can
        emit truthful GPU_PROCESS_ATTACHED/DETACHED events without spamming.
        The FIRST call only establishes the baseline (no attach storm on
        startup).
        """
        cur: dict[int, set[int]] = {
            g["index"]: {p["pid"] for p in g.get("processes", [])} for g in gpus
        }
        first = not self._pids_seen
        self._pids_seen = True
        prev = self._prev_pids
        self._prev_pids = cur
        if first:
            return set(), set()
        attached: set[int] = set()
        detached: set[int] = set()
        for idx, pids in cur.items():
            attached |= pids - prev.get(idx, set())
        for idx, pids in prev.items():
            detached |= pids - cur.get(idx, set())
        return attached, detached
