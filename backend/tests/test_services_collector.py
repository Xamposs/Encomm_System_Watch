"""Windows Services collector tests (Phase 08).

Fixture-driven: a fake psutil module (win_service_iter) exercises
enumeration, PID mapping, shared process hosting, inaccessible services and
state changes without touching the real machine.
"""
import sys
import types

import pytest

from app.collectors.services import ServiceInfo, ServicesCollector


class FakeService:
    def __init__(self, name, display="", status="running", start="automatic",
                 user="LocalSystem", binpath="C:\\svc.exe -k test",
                 desc="desc", pid=1234, fail_meta=False):
        self._n = name
        self._d = display or name
        self._s = status
        self._st = start
        self._u = user
        self._b = binpath
        self._de = desc
        self._p = pid
        self._fail_meta = fail_meta

    def name(self):
        return self._n

    def display_name(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._d

    def status(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._s

    def start_type(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._st

    def username(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._u

    def binpath(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._b

    def description(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._de

    def pid(self):
        if self._fail_meta:
            raise PermissionError("denied")
        return self._p


class FakePsutil:
    def __init__(self, services, fail_iter=False):
        self._services = services
        self._fail_iter = fail_iter
        self.AccessDenied = PermissionError
        self.NoSuchProcess = ProcessLookupError

    def win_service_iter(self):
        if self._fail_iter:
            raise PermissionError("iteration denied")
        return iter(self._services)


@pytest.fixture
def fake_psutil(monkeypatch):
    def _install(services, fail_iter=False):
        fake = FakePsutil(services, fail_iter)
        monkeypatch.setitem(sys.modules, "psutil", fake)
        return fake
    return _install


def test_enumeration(fake_psutil):
    fake_psutil([
        FakeService("One", display="Service One", status="running"),
        FakeService("Two", display="Service Two", status="stopped", pid=None),
        FakeService("Three", display="Service Three", status="paused"),
    ])
    svcs, skipped = ServicesCollector().collect()
    assert skipped == 0
    assert len(svcs) == 3
    by_name = {s.name: s for s in svcs}
    assert by_name["One"].display_name == "Service One"
    assert by_name["One"].status == "running"
    assert by_name["One"].start_type == "Auto"
    assert by_name["One"].account == "LocalSystem"
    assert by_name["One"].binpath == "C:\\svc.exe -k test"
    assert by_name["One"].pid == 1234
    assert by_name["Two"].status == "stopped"
    assert by_name["Two"].pid is None
    assert by_name["Three"].status == "paused"


def test_pid_mapping_and_shared_host(fake_psutil):
    """Two services sharing one svchost PID must produce two ServiceInfo
    entries with the SAME pid — never one fake process per service."""
    fake_psutil([
        FakeService("BFE", pid=3624, binpath="C:\\Windows\\system32\\svchost.exe -k n"),
        FakeService("mpssvc", pid=3624, binpath="C:\\Windows\\system32\\svchost.exe -k n"),
        FakeService("Lone", pid=999, binpath="C:\\Windows\\system32\\svchost.exe -k s"),
    ])
    svcs, _ = ServicesCollector().collect()
    by_name = {s.name: s for s in svcs}
    assert by_name["BFE"].pid == 3624
    assert by_name["mpssvc"].pid == 3624
    assert by_name["Lone"].pid == 999
    assert len(svcs) == 3  # no duplicate processes created


def test_inaccessible_service(fake_psutil):
    """A service whose metadata cannot be read degrades to an inaccessible
    entry with the name preserved — never a crash, never fabricated data."""
    fake_psutil([
        FakeService("Denied", fail_meta=True, pid=None),
        FakeService("Fine", status="running"),
    ])
    svcs, skipped = ServicesCollector().collect()
    assert skipped == 0  # per-field degradation, not a skipped iteration
    by_name = {s.name: s for s in svcs}
    assert by_name["Denied"].inaccessible is True
    assert by_name["Denied"].display_name == ""
    assert by_name["Denied"].pid is None
    assert by_name["Fine"].inaccessible is False
    assert by_name["Fine"].display_name == "Fine"


def test_iteration_denied(fake_psutil):
    fake_psutil([], fail_iter=True)
    svcs, skipped = ServicesCollector().collect()
    assert svcs == []
    assert skipped == 1


def test_metadata_ttl_cache(fake_psutil, monkeypatch):
    """Heavy metadata is TTL-cached; status+pid refresh every poll."""
    calls = {"meta": 0}

    class CountingService(FakeService):
        def display_name(self):
            calls["meta"] += 1
            return "Counted"

    fake_psutil([CountingService("Svc")])
    col = ServicesCollector(metadata_ttl_s=60.0)
    svcs, _ = col.collect()
    assert svcs[0].display_name == "Counted"
    svcs2, _ = col.collect()
    assert svcs2[0].display_name == "Counted"
    assert calls["meta"] == 1  # cached


def test_status_change_detection(fake_psutil):
    """State change is a collector-level observation: same name, new status."""
    fake_psutil([FakeService("WinUpd", status="stopped", pid=None)])
    col = ServicesCollector()
    svcs, _ = col.collect()
    assert svcs[0].status == "stopped"
    fake_psutil([FakeService("WinUpd", status="running", pid=777)])
    svcs2, _ = col.collect()
    assert svcs2[0].status == "running"
    assert svcs2[0].pid == 777


def test_start_type_labels(fake_psutil):
    fake_psutil([
        FakeService("A", start="automatic"),
        FakeService("B", start="automatic delayed"),
        FakeService("C", start="manual"),
        FakeService("D", start="disabled"),
    ])
    svcs, _ = ServicesCollector().collect()
    labels = {s.name: s.start_type for s in svcs}
    assert labels == {"A": "Auto", "B": "AutoDelay", "C": "Manual", "D": "Disabled"}
