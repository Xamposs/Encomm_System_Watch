"""WSL collector tests (Phase 09).

Fixture-driven: fake wsl.exe subprocess outputs (including the real UTF-16
encoding) exercise parsing, resilience, and the strict rule that a STOPPED
distribution is never inspected internally.
"""
import types

import pytest

from app.collectors import wsl as wsl_mod
from app.collectors.wsl import WslCollector, WslState, _parse_list_verbose


def _utf16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _fake_run(results):
    """results: dict arg-tuple -> (returncode, stdout_bytes)"""
    def runner(args, timeout=10.0):
        key = tuple(args)
        code, out = results.get(key, (0, b""))
        return types.SimpleNamespace(returncode=code, stdout=out, stderr=b"")
    return runner


VERBOSE_UTF16 = _utf16(
    "  NAME              STATE           VERSION\r\n"
    "* Ubuntu            Stopped         2\r\n"
    "  docker-desktop    Stopped         2\r\n"
)
RUNNING_EMPTY = _utf16("There are no running distributions.")
STATUS = _utf16("Default Distribution: Ubuntu\r\nDefault Version: 2\r\n")


@pytest.fixture
def no_wsl(monkeypatch):
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, _utf16("")),
        ("--list", "--running"): (4294967295, RUNNING_EMPTY),
        ("--list", "--verbose"): (0, _utf16("Windows Subsystem for Linux has no installed distributions.")),
    }))


@pytest.fixture
def distro_fixture(monkeypatch):
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, STATUS),
        ("--list", "--running"): (4294967295, RUNNING_EMPTY),
        ("--list", "--verbose"): (0, VERBOSE_UTF16),
    }))


def test_no_wsl(no_wsl):
    st = WslCollector(deep=False).collect()
    assert st.installed is False
    assert st.distributions == []
    assert st.running == []


def test_distro_list_utf16(distro_fixture):
    st = WslCollector(deep=False).collect()
    assert st.installed is True
    assert st.default == "Ubuntu"
    assert st.default_version == 2
    by_name = {d.name: d for d in st.distributions}
    assert set(by_name) == {"Ubuntu", "docker-desktop"}
    assert by_name["Ubuntu"].state == "Stopped"
    assert by_name["Ubuntu"].version == 2
    assert by_name["Ubuntu"].is_default is True
    assert by_name["docker-desktop"].is_default is False


def test_stopped_distro_never_inspected(monkeypatch):
    """A stopped distro must never trigger an internal command."""
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, STATUS),
        ("--list", "--running"): (4294967295, RUNNING_EMPTY),
        ("--list", "--verbose"): (0, VERBOSE_UTF16),
    }))
    calls = []
    monkeypatch.setattr(wsl_mod, "_bounded_summary", lambda name: calls.append(name) or {})
    st = WslCollector(deep=True).collect()
    assert calls == []  # nothing running -> nothing inspected
    assert all(d.summary is None for d in st.distributions)


def test_running_distro_bounded_summary(monkeypatch):
    """A RUNNING distro gets exactly one bounded internal snapshot."""
    running_utf16 = _utf16("  Ubuntu\r\n")
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, STATUS),
        ("--list", "--running"): (0, running_utf16),
        ("--list", "--verbose"): (0, VERBOSE_UTF16),
    }))
    seen = []
    monkeypatch.setattr(wsl_mod, "_bounded_summary",
                        lambda name: seen.append(name) or {"process_count": 42})
    st = WslCollector(deep=True).collect()
    assert seen == ["Ubuntu"]
    ubuntu = next(d for d in st.distributions if d.name == "Ubuntu")
    assert ubuntu.state == "Running"
    assert ubuntu.summary == {"process_count": 42}
    # docker-desktop is stopped -> still no summary
    dd = next(d for d in st.distributions if d.name == "docker-desktop")
    assert dd.summary is None


def test_parser_resilience(monkeypatch):
    """Garbage/truncated/oddly-spaced output degrades, never crashes."""
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, b"\x00\xff broken \x00"),
        ("--list", "--running"): (1, b""),
        ("--list", "--verbose"): (0, b"garbage without columns\r\nUbuntu Running\r\n"),
    }))
    st = WslCollector(deep=False).collect()
    assert st.installed is True  # raw output non-empty -> installed attempt
    names = {d.name for d in st.distributions}
    assert "Ubuntu" in names
    assert st.running == []


def test_parse_list_verbose_running_overrides():
    """The authoritative --list --running set wins over the verbose table."""
    distros = _parse_list_verbose(
        "  NAME STATE VERSION\n* Ubuntu Stopped 2\n  X Stopped 1\n",
        running={"Ubuntu"}, default="Ubuntu",
    )
    by_name = {d.name: d for d in distros}
    assert by_name["Ubuntu"].state == "Running"
    assert by_name["X"].state == "Stopped"


def test_bounded_summary_parse(monkeypatch):
    out = (
        "PROC_COUNT=137\nKERNEL=5.15.153.1-microsoft-standard-WSL2\nTOPS\n"
        " 12 bash\n  8 sshd\n  3 node\nMEM\n              total        used        free\n"
        "Mem:          16000        3200       12800\n"
        "Swap:         4096           0        4096\nLISTEN\n22\n8080\n"
    ).encode("utf-8")
    monkeypatch.setattr(
        wsl_mod, "subprocess",
        types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out),
            CREATE_NO_WINDOW=0,
        ),
    )
    s = wsl_mod._bounded_summary("Ubuntu")
    assert s["process_count"] == 137
    assert s["kernel"].startswith("5.15")
    assert s["top_processes"] == ["bash (12)", "sshd (8)", "node (3)"]
    assert s["memory_total_mb"] == 16000
    assert s["memory_used_mb"] == 3200
    assert s["listening_tcp_ports"] == [22, 8080]


def test_bounded_summary_failure_is_none(monkeypatch):
    monkeypatch.setattr(
        wsl_mod, "subprocess",
        types.SimpleNamespace(
            run=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=b""),
            CREATE_NO_WINDOW=0,
        ),
    )
    assert wsl_mod._bounded_summary("Ubuntu") is None


def test_collect_never_crashes_on_missing_wsl(monkeypatch):
    def boom(args, timeout=10.0, **kwargs):
        raise FileNotFoundError("wsl.exe")
    monkeypatch.setattr(wsl_mod, "_run", boom)
    st = WslCollector(deep=False).collect()
    assert isinstance(st, WslState)
    assert st.error != ""
    assert st.installed is False


def test_parse_plain_list():
    raw = (
        "Windows Subsystem for Linux Distributions:\r\n"
        "Ubuntu (Default)\r\n"
        "docker-desktop\r\n"
    )
    distros = wsl_mod._parse_plain_list(raw)
    assert [d.name for d in distros] == ["Ubuntu", "docker-desktop"]
    assert distros[0].is_default is True
    assert distros[1].is_default is False
    assert wsl_mod._parse_plain_list("no installed distributions") == []


def test_empty_verbose_falls_back_to_plain_list(monkeypatch):
    """Empty --list --verbose output must NOT silently mean 'no WSL': the
    plain --list probe recovers the distributions (WSL-service enumeration
    can return empty while status/running still answer)."""
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, STATUS),
        ("--list", "--running"): (4294967295, RUNNING_EMPTY),
        ("--list", "--verbose"): (0, b""),
        ("--list",): (0, _utf16(
            "Windows Subsystem for Linux Distributions:\r\n"
            "Ubuntu (Default)\r\n"
            "docker-desktop\r\n")),
    }))
    st = WslCollector(deep=False).collect()
    assert st.installed is True
    assert [d.name for d in st.distributions] == ["Ubuntu", "docker-desktop"]
    assert st.error == ""


def test_empty_verbose_and_empty_plain_is_truthful_error(monkeypatch):
    """Both probes empty -> NOT silently 'not installed': the failure is
    surfaced truthfully as an error."""
    monkeypatch.setattr(wsl_mod, "_run", _fake_run({
        ("--status",): (0, STATUS),
        ("--list", "--running"): (4294967295, RUNNING_EMPTY),
        ("--list", "--verbose"): (0, b""),
        ("--list",): (0, b""),
    }))
    st = WslCollector(deep=False).collect()
    assert st.installed is False
    assert st.error != ""
    assert "empty output" in st.error
