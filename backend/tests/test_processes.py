"""Process collector + stable identity tests."""
import psutil
from types import SimpleNamespace

from app.collectors.processes import ProcessCollector
from app.models.entities import ProcessInfo

from conftest import make_proc


class FakeProc:
    def __init__(self, pid, name="test.exe", ct=1000.0, errors=None):
        self.pid = pid
        self._name = name
        self._ct = ct
        self.errors = errors or {}

    def create_time(self):
        if "create_time" in self.errors:
            raise self.errors["create_time"]
        return self._ct

    def name(self):
        if "name" in self.errors:
            raise self.errors["name"]
        return self._name

    def exe(self):
        if "exe" in self.errors:
            raise self.errors["exe"]
        return f"C:\\bin\\{self._name}"

    def username(self):
        if "username" in self.errors:
            raise self.errors["username"]
        return "DESKTOP\\tester"

    def status(self):
        if "status" in self.errors:
            raise self.errors["status"]
        return "running"

    def num_threads(self):
        if "num_threads" in self.errors:
            raise self.errors["num_threads"]
        return 8

    def ppid(self):
        if "ppid" in self.errors:
            raise self.errors["ppid"]
        return 1

    def memory_info(self):
        if "memory_info" in self.errors:
            raise self.errors["memory_info"]
        return SimpleNamespace(rss=128 * 1024 * 1024)

    def cmdline(self):
        if "cmdline" in self.errors:
            raise self.errors["cmdline"]
        return ["test.exe", "--flag"]

    def cpu_times(self):
        if "cpu_times" in self.errors:
            raise self.errors["cpu_times"]
        return SimpleNamespace(user=10.0, system=5.0)

    def as_dict(self, attrs=None, ad_value=None):
        if "memory_info" in self.errors:
            raise self.errors["memory_info"]
        if "as_dict" in self.errors:
            raise self.errors["as_dict"]
        return {
            "memory_info": SimpleNamespace(rss=128 * 1024 * 1024),
            "cpu_times": SimpleNamespace(user=10.0, system=5.0),
        }


def test_stable_id_is_pid_plus_creation_time():
    p1 = make_proc(pid=18424, ct=1786912230.0)
    p2 = make_proc(pid=18424, ct=1786912230.0)
    p3 = make_proc(pid=18424, ct=1786912231.0)
    assert p1.stable_id == "proc:18424:1786912230000"
    assert p1.stable_id == p2.stable_id          # same PID + same birth time
    assert p1.stable_id != p3.stable_id          # same PID, different birth -> distinct


def test_collector_builds_snapshot_and_pid_map(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([FakeProc(10), FakeProc(20)]))
    col = ProcessCollector()
    procs, pid_map = col.collect()
    assert set(procs) == {"proc:10:1000000", "proc:20:1000000"}
    assert pid_map == {10: "proc:10:1000000", 20: "proc:20:1000000"}
    p = procs["proc:10:1000000"]
    assert p.name == "test.exe" and p.pid == 10
    assert p.memory_mb == 128.0
    assert p.username == "DESKTOP\\tester"
    assert p.cmdline == ["test.exe", "--flag"]


def test_cpu_percent_requires_two_ticks(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([FakeProc(10)]))
    col = ProcessCollector()
    procs1, _ = col.collect()
    assert procs1["proc:10:1000000"].cpu_percent == 0.0   # first tick: no baseline
    # second tick with different cpu_times -> real delta
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([FakeProc(10)]))
    procs2, _ = col.collect()
    assert procs2["proc:10:1000000"].cpu_percent >= 0.0


def test_cpu_delta_is_measured_between_ticks(monkeypatch):
    import app.collectors.processes as proc_module

    # advance monotonic clock between ticks so the 50ms delta guard passes
    clock = [1000.0]

    def fake_monotonic():
        clock[0] += 1.0
        return clock[0]

    monkeypatch.setattr(proc_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([FakeProc(10)]))
    col = ProcessCollector()
    col.collect()
    # cpu went from 15s total to 35s total across one tick
    fake = FakeProc(10)
    fake.as_dict = lambda attrs=None, ad_value=None: {
        "memory_info": SimpleNamespace(rss=128 * 1024 * 1024),
        "cpu_times": SimpleNamespace(user=30.0, system=5.0),
    }
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([fake]))
    procs2, _ = col.collect()
    assert procs2["proc:10:1000000"].cpu_percent > 0.0


def test_inaccessible_fields_do_not_crash(monkeypatch):
    fp = FakeProc(10, errors={"exe": psutil.AccessDenied(10),
                              "username": psutil.AccessDenied(10),
                              "memory_info": psutil.NoSuchProcess(10)})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([fp]))
    col = ProcessCollector()
    procs, _ = col.collect()
    p = procs["proc:10:1000000"]
    assert p.exe is None
    assert p.username is None
    assert p.memory_mb == 0.0
    assert p.status == "running"


def test_disappearing_process_is_skipped(monkeypatch):
    # process vanishes before inspection -> None entry, others unaffected
    dead = FakeProc(10, errors={"create_time": psutil.NoSuchProcess(10)})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([dead, FakeProc(20)]))
    col = ProcessCollector()
    procs, pid_map = col.collect()
    assert "proc:10:1000000" not in procs
    assert "proc:20:1000000" in procs
    assert pid_map == {20: "proc:20:1000000"}


def test_zombie_process_skipped_not_fatal(monkeypatch):
    fp = FakeProc(30, errors={"status": psutil.ZombieProcess(30)})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([fp]))
    col = ProcessCollector()
    procs, _ = col.collect()
    assert procs["proc:30:1000000"].status == "unknown"
