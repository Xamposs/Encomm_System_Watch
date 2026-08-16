"""Shared test fixtures and snapshot builders."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.models.entities import ConnectionInfo, ProcessInfo, Snapshot  # noqa: E402


def make_proc(pid, name="test.exe", ct=1000.0, cpu=5.0, mem=100.0, **kw) -> ProcessInfo:
    base = dict(
        pid=pid, create_time=ct, name=name, exe=f"C:\\bin\\{name}",
        username="DESKTOP\\tester", status="running", cpu_percent=cpu,
        memory_mb=mem, num_threads=8, ppid=1, cmdline=[name, "--x"],
    )
    base.update(kw)
    return ProcessInfo(**base)


def make_conn(pid, lip="127.0.0.1", lport=5000, rip="", rport=0,
              proto="tcp", state="ESTABLISHED", kind=None) -> ConnectionInfo:
    if kind is None:
        if not rip:
            kind = "listening"
        elif rip.startswith("127.") and lip.startswith("127."):
            kind = "localhost"
        else:
            kind = "external"
    return ConnectionInfo(pid=pid, proto=proto, local_ip=lip, local_port=lport,
                          remote_ip=rip, remote_port=rport, state=state, kind=kind)


def make_snap(procs=None, conns=None, owner_map=None, system=None) -> Snapshot:
    procs = procs or []
    conns = conns or []
    pd = {p.stable_id: p for p in procs}
    cd = {c.key: c for c in conns}
    if owner_map is None:
        pid_to_sid = {p.pid: p.stable_id for p in procs}
        owner_map = {c.key: pid_to_sid.get(c.pid) for c in conns}
    sysd = system or {
        "hostname": "TESTPC", "platform": "Windows 11", "cpu_count": 8,
        "cpu_percent": 10.0, "mem_percent": 40.0, "mem_used_gb": 8.0,
        "mem_total_gb": 32.0, "boot_ts": 1000.0, "ts": 2000.0,
    }
    return Snapshot(ts=100.0, processes=pd, connections=cd, owner_map=owner_map, system=sysd)


@pytest.fixture
def cfg() -> Settings:
    return Settings()


@pytest.fixture
def client():
    """TestClient WITHOUT lifespan (no collector loops, no telemetry probe).

    Use only for pure route tests; state is the module default.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture
def small_cfg() -> Settings:
    """Config with tiny node caps to exercise aggregation paths."""
    return Settings(max_external_nodes=2, max_listen_nodes=2, max_loc_nodes=2)
