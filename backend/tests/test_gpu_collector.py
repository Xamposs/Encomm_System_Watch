"""GPU collector tests: no-NVML fallback, single/multi GPU, metrics,
PID mapping, stale PID removal. All NVML paths are faked — no real
hardware required."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from app.collectors.gpu import GpuCollector


# --------------------------------------------------------------- fake NVML

class FakeNvml:
    """In-memory NVML module standing in for pynvml."""

    NVML_TEMPERATURE_GPU = 0
    NVML_CLOCK_GRAPHICS = 0
    NVML_CLOCK_MEM = 1

    gpus: list[dict] = []

    @classmethod
    def reset(cls, gpus):
        cls.gpus = gpus

    @classmethod
    def nvmlInit(cls):
        pass

    @classmethod
    def nvmlShutdown(cls):
        pass

    @classmethod
    def nvmlDeviceGetCount(cls):
        return len(cls.gpus)

    @classmethod
    def nvmlDeviceGetHandleByIndex(cls, idx):
        return idx

    @classmethod
    def nvmlDeviceGetName(cls, _h):
        return cls.gpus[_h]["name"]

    @classmethod
    def nvmlDeviceGetUtilizationRates(cls, h):
        return SimpleNamespace(gpu=cls.gpus[h]["util"])

    @classmethod
    def nvmlDeviceGetMemoryInfo(cls, h):
        return SimpleNamespace(
            used=cls.gpus[h]["vram_used"] * 1024 * 1024,
            total=cls.gpus[h]["vram_total"] * 1024 * 1024,
        )

    @classmethod
    def nvmlDeviceGetTemperature(cls, h, _kind):
        return cls.gpus[h]["temp"]

    @classmethod
    def nvmlDeviceGetPowerUsage(cls, h):
        return cls.gpus[h]["power_w"] * 1000

    @classmethod
    def nvmlSystemGetDriverVersion(cls):
        return "560.94"

    @classmethod
    def nvmlDeviceGetFanSpeed(cls, h):
        return cls.gpus[h].get("fan", 40)

    @classmethod
    def nvmlDeviceGetClockInfo(cls, h, kind):
        return 1500 if kind == cls.NVML_CLOCK_GRAPHICS else 7000

    @classmethod
    def nvmlDeviceGetComputeRunningProcesses(cls, h):
        return cls.gpus[h].get("compute", [])

    @classmethod
    def nvmlDeviceGetGraphicsRunningProcesses(cls, h):
        return cls.gpus[h].get("graphics", [])


def _proc(pid, vram=None):
    return SimpleNamespace(pid=pid, usedGpuMemory=vram)


@pytest.fixture
def fake_nvml(monkeypatch):
    FakeNvml.reset([{
        "name": "NVIDIA GeForce GTX 1660 Ti", "util": 74, "vram_used": 5.2,
        "vram_total": 6.0, "temp": 67, "power_w": 92.0, "fan": 50,
        "compute": [_proc(1234, 500 * 1024 * 1024)],
        "graphics": [_proc(2132), _proc(1234)],
    }])
    monkeypatch.setitem(sys.modules, "pynvml", FakeNvml)
    return FakeNvml


@pytest.fixture
def no_nvml(monkeypatch):
    """pynvml import fails AND nvidia-smi is 'not installed'."""
    monkeypatch.setitem(sys.modules, "pynvml", None)
    monkeypatch.setattr("app.collectors.gpu.shutil.which", lambda _: None)
    return FakeNvml


# ------------------------------------------------------------------ tests

def test_no_nvml_no_smi_degrades_to_none(no_nvml):
    c = GpuCollector()
    assert c.source == "NONE"
    assert c.sample(with_processes=True) == []
    # never raises


def test_single_gpu_metrics(fake_nvml):
    c = GpuCollector()
    assert c.source == "NVML"
    gpus = c.sample(with_processes=True)
    assert len(gpus) == 1
    g = gpus[0]
    assert g["name"] == "NVIDIA GeForce GTX 1660 Ti"
    assert g["utilization_percent"] == 74
    assert g["vram_used_mb"] == pytest.approx(5.2, abs=0.1)
    assert g["vram_total_mb"] == pytest.approx(6.0, abs=0.1)
    assert g["temperature_c"] == 67
    assert g["power_w"] == pytest.approx(92.0)
    assert g["driver"] == "560.94"


def test_multi_gpu(fake_nvml):
    FakeNvml.reset([
        {"name": "GPU A", "util": 10, "vram_used": 1.0, "vram_total": 8.0,
         "temp": 40, "power_w": 20.0, "compute": [], "graphics": []},
        {"name": "GPU B", "util": 90, "vram_used": 6.0, "vram_total": 24.0,
         "temp": 75, "power_w": 180.0, "compute": [], "graphics": []},
    ])
    c = GpuCollector()
    gpus = c.sample()
    assert [g["index"] for g in gpus] == [0, 1]
    assert gpus[1]["utilization_percent"] == 90
    assert gpus[1]["vram_total_mb"] == pytest.approx(24.0, abs=0.1)


def test_pid_mapping_and_dedupe(fake_nvml):
    c = GpuCollector()
    gpus = c.sample(with_processes=True)
    pids = {p["pid"] for p in gpus[0]["processes"]}
    # 1234 appears in both compute+graphics lists -> deduped to one entry
    assert pids == {1234, 2132}
    # per-process VRAM exposed only when the API really provides it
    by_pid = {p["pid"]: p for p in gpus[0]["processes"]}
    assert by_pid[1234]["vram_mb"] == pytest.approx(500, abs=1)
    assert "vram_mb" not in by_pid[2132]


def test_stale_pid_removal(fake_nvml):
    c = GpuCollector()
    gpus = c.sample(with_processes=True)
    # first diff establishes the baseline
    assert c.changed_pids(gpus) == (set(), set())
    # process 2132 leaves, 7777 joins
    FakeNvml.reset([{
        "name": "NVIDIA GeForce GTX 1660 Ti", "util": 10, "vram_used": 2.0,
        "vram_total": 6.0, "temp": 50, "power_w": 30.0,
        "compute": [_proc(1234, 100 * 1024 * 1024)],
        "graphics": [_proc(7777)],
    }])
    gpus2 = c.sample(with_processes=True)
    attached, detached = c.changed_pids(gpus2)
    assert attached == {7777}
    assert detached == {2132}


def test_nvidia_smi_fallback_parser(monkeypatch):
    """Fallback path parses nvidia-smi CSV correctly (faked subprocess)."""
    monkeypatch.setitem(sys.modules, "pynvml", None)
    monkeypatch.setattr("app.collectors.gpu.shutil.which", lambda _: "nvidia-smi")
    out = (
        "0, NVIDIA GeForce GTX 1660 Ti, 17, 1598, 6144, 49, 14.1, 560.94, "
        "[N/A], 1500, 7000\n"
    )
    monkeypatch.setattr(
        "app.collectors.gpu.subprocess.run",
        lambda *a, **k: SimpleNamespace(stdout=out, stderr=""),
    )
    c = GpuCollector()
    assert c.source == "NVIDIA_SMI"
    gpus = c.sample(with_processes=False)
    assert len(gpus) == 1
    g = gpus[0]
    assert g["name"] == "NVIDIA GeForce GTX 1660 Ti"
    assert g["utilization_percent"] == 17
    assert g["vram_used_mb"] == 1598
    # unavailable fields are omitted, never fabricated
    assert "fan_percent" not in g
