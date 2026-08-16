"""Windows network telemetry tests: ETW parsing, capability detection,
elevation fallback, adapter totals sampler."""
import time

import psutil

from app.telemetry.base import Capability, NetworkActivityEvent
from app.telemetry.windows_network import (
    AdapterTotalsSampler,
    EtwTcpipProvider,
    _norm_ip,
    _norm_port,
    _norm_size,
)


# ------------------------------------------------------------- normalization

def test_norm_ip_strings():
    assert _norm_ip("127.0.0.1") == "127.0.0.1"
    assert _norm_ip("[::1]") == "::1"
    assert _norm_ip("::ffff:1.2.3.4") == "1.2.3.4"
    assert _norm_ip("") == ""


def test_norm_ip_int():
    # 127.0.0.1 in network byte order as UInt32
    assert _norm_ip(0x7F000001) == "127.0.0.1"
    assert _norm_ip(0) == "0.0.0.0"


def test_norm_ip_bytes():
    assert _norm_ip(b"\x7f\x00\x00\x01") == "127.0.0.1"
    assert _norm_ip(b"\x00" * 16) == "::"
    assert _norm_ip(b"\xde\xad") == ""


def test_norm_port_and_size():
    assert _norm_port("443") == 443
    assert _norm_port(65536) == 65535
    assert _norm_port(-3) == 0
    assert _norm_port("junk") == 0
    assert _norm_size("4096") == 4096
    assert _norm_size(-1) == 0
    assert _norm_size("junk") == 0


# ------------------------------------------------------------ ETW callback

def _fake_event(task, **fields):
    base = {"Task Name": task, "PID": 1234, "size": 4096}
    base.update(fields)
    return (0, base)


def test_on_event_send_ipv4():
    prov = EtwTcpipProvider()
    prov._on_event(_fake_event(
        "SendIPv4", saddr="127.0.0.1", sport=5000, daddr="10.0.0.9", dport=443,
    ))
    evs = prov.drain()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.pid == 1234
    assert ev.direction == "OUT"
    assert ev.local_ip == "127.0.0.1" and ev.local_port == 5000
    assert ev.remote_ip == "10.0.0.9" and ev.remote_port == 443
    assert ev.size == 4096


def test_on_event_receive_ipv4_swaps_ends():
    prov = EtwTcpipProvider()
    prov._on_event(_fake_event(
        "ReceiveIPv4", daddr="10.0.0.2", dport=80, saddr="203.0.113.5", sport=9999,
    ))
    evs = prov.drain()
    assert len(evs) == 1
    ev = evs[0]
    assert ev.direction == "IN"
    assert ev.local_ip == "10.0.0.2" and ev.local_port == 80
    assert ev.remote_ip == "203.0.113.5" and ev.remote_port == 9999


def test_on_event_ipv6_and_size_zero_skipped():
    prov = EtwTcpipProvider()
    prov._on_event(_fake_event(
        "SendIPv6", size=0, saddr="::1", sport=1, daddr="::1", dport=2,
    ))
    prov._on_event(_fake_event("SomeOtherTask"))
    assert prov.drain() == []


def test_on_event_malformed_does_not_crash():
    prov = EtwTcpipProvider()
    prov._on_event((0, {"Task Name": "SendIPv4"}))          # no PID
    prov._on_event((0, {"Task Name": "ReceiveIPv4", "PID": 5, "size": 10}))  # no addrs
    prov._on_event(None)                                     # garbage
    assert prov.drain() == []


# ------------------------------------------------- capability / elevation

class _FakeEtw:
    def __init__(self, fail: Exception | None = None):
        self._fail = fail
        self.running = True
        self.consumer = _FakeConsumer()

    def start(self):
        if self._fail:
            raise self._fail
        self.running = True

    def stop(self):
        self.running = False


class _FakeThread:
    def is_alive(self):
        return True


class _FakeConsumer:
    process_thread = _FakeThread()


def test_start_denied_reports_elevation_required(monkeypatch):
    import etw as etw_mod

    monkeypatch.setattr(etw_mod, "ETW", lambda **kw: _FakeEtw(fail=PermissionError(5, "Access is denied.")))
    prov = EtwTcpipProvider()
    assert prov.start() is False
    cap = prov.capability()
    assert cap.level == "TIER0"
    assert cap.elevation_required is True
    assert "administrator" in cap.detail.lower()


def test_start_success_reports_tier2(monkeypatch):
    import etw as etw_mod

    monkeypatch.setattr(etw_mod, "ETW", lambda **kw: _FakeEtw())
    monkeypatch.setattr(etw_mod, "ProviderInfo", lambda *a, **kw: object())
    prov = EtwTcpipProvider()
    assert prov.start() is True
    cap = prov.capability()
    assert cap.level == "TIER2"
    assert cap.elevation_required is False
    assert prov.alive() is True
    prov.stop()
    assert prov.alive() is False


def test_pywintrace_missing_reports_honestly(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "etw":
            raise ModuleNotFoundError("No module named 'etw'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    prov = EtwTcpipProvider()
    assert prov.start() is False
    assert prov.capability().level == "TIER0"
    assert "pywintrace" in prov.capability().detail.lower()


# ------------------------------------------------------- adapter totals

def test_adapter_totals_sampler(monkeypatch):
    class _FakeIo:
        def __init__(self, sent, recv):
            self.bytes_sent = sent
            self.bytes_recv = recv

    fake = {
        "Ethernet": _FakeIo(1000, 2000),
        "Loopback Pseudo-Interface 1": _FakeIo(10 ** 9, 10 ** 9),  # excluded
    }
    clock = {"t": 1000.0}

    def fake_counters(pernic=True):
        return fake

    monkeypatch.setattr(psutil, "net_io_counters", fake_counters)
    sampler = AdapterTotalsSampler()

    def now():
        return clock["t"]

    monkeypatch.setattr("app.telemetry.windows_network.time.time", now)
    assert sampler.sample() is None  # first sample baselines only

    fake["Ethernet"] = _FakeIo(1500, 2600)  # +500 sent, +600 recv
    clock["t"] = 1000.5                     # 0.5 s later
    down, up = sampler.sample()
    assert down == 1200.0  # 600 / 0.5
    assert up == 1000.0    # 500 / 0.5
