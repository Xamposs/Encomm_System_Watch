"""Network collector tests: classification, pid=None, AccessDenied fallback."""
import socket

import psutil
import pytest
from types import SimpleNamespace

from app.collectors.network import NetworkCollector, is_loopback


def mk(pid, laddr, raddr, state="ESTABLISHED", sock_type=socket.SOCK_STREAM):
    return SimpleNamespace(
        pid=pid,
        type=sock_type,
        laddr=SimpleNamespace(ip=laddr[0], port=laddr[1]) if laddr else None,
        raddr=SimpleNamespace(ip=raddr[0], port=raddr[1]) if raddr else None,
        status=state,
    )


def test_loopback_detection():
    assert is_loopback("127.0.0.1")
    assert is_loopback("127.8.9.10")
    assert is_loopback("::1")
    assert not is_loopback("104.18.22.44")
    assert not is_loopback("")


def test_classification_and_owner_map(monkeypatch):
    conns = [
        mk(100, ("0.0.0.0", 8080), None, "LISTEN"),                 # listening
        mk(101, ("127.0.0.1", 5000), ("127.0.0.1", 3000)),          # localhost
        mk(102, ("192.168.1.5", 51000), ("104.18.22.44", 443)),     # external
        mk(None, ("0.0.0.0", 445), None, "LISTEN"),                 # system-owned
        mk(103, ("192.168.1.5", 53000), ("8.8.8.8", 53), None, socket.SOCK_DGRAM),  # udp
    ]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="inet": conns)
    pid_map = {100: "proc:100:0", 101: "proc:101:0", 102: "proc:102:0", 103: "proc:103:0"}
    col = NetworkCollector()
    out, owner = col.collect(pid_map)

    by_kind = {}
    for c in out.values():
        by_kind.setdefault(c.kind, []).append(c)
    assert len(by_kind["listening"]) == 2
    assert len(by_kind["localhost"]) == 1
    assert len(by_kind["external"]) == 2
    assert by_kind["external"][0].remote_ip == "104.18.22.44"
    assert by_kind["external"][0].remote_port == 443
    # pid=None socket maps to owner None (SYSTEM node downstream)
    sys_listen = [c for c in by_kind["listening"] if c.pid is None][0]
    assert owner[sys_listen.key] is None
    # localhost conn owned by its process
    loc = by_kind["localhost"][0]
    assert owner[loc.key] == "proc:101:0"


def test_accessdenied_fallback_to_per_family(monkeypatch):
    tcp_only = [mk(100, ("0.0.0.0", 8080), None, "LISTEN")]
    calls = []

    def flaky(kind="inet"):
        calls.append(kind)
        if kind == "inet":
            raise psutil.AccessDenied()
        if kind == "tcp":
            return tcp_only
        return []

    monkeypatch.setattr(psutil, "net_connections", flaky)
    col = NetworkCollector()
    out, owner = col.collect({})
    assert len(out) == 1
    assert calls == ["inet", "tcp", "udp"]


def test_enumeration_race_returns_empty(monkeypatch):
    def boom(kind="inet"):
        raise RuntimeError("table changed during enumeration")
    monkeypatch.setattr(psutil, "net_connections", boom)
    col = NetworkCollector()
    out, owner = col.collect({})
    assert out == {}
