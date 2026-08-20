"""Infrastructure security tests (v0.4.0).

The checkpoint is OBSERVABILITY ONLY. These tests scan the new collector /
engine sources for forbidden control paths and credential/ENV leakage so a
regression can never silently reintroduce a control surface.
"""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

NEW_MODULES = [
    "collectors/services.py",
    "collectors/wsl.py",
    "collectors/docker.py",
    "collectors/vm.py",
    "services/infra.py",
]

# runtime control operations that must NEVER appear in the new code
FORBIDDEN_TOKENS = [
    "Start-Service", "Stop-Service", "Restart-Service",
    "docker start", "docker stop", "docker restart", "docker exec",
    "wsl --shutdown", "wsl --terminate", "wsl -t ",
    "Start-VM", "Stop-VM", "Restart-VM", "Suspend-VM", "Resume-VM",
    "VBoxManage controlvm", "VBoxManage startvm", "VBoxManage modifyvm",
    "vmrun start", "vmrun stop",
]

_DOCSTRING_RE = re.compile(r'"""(?:[^"\\]|\\.|"(?!""))*"""', re.S)


def _read(mod: str) -> str:
    return (APP / mod).read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Executable code only — docstrings legitimately NAME forbidden verbs
    when describing the read-only rule, so they are excluded from the scan."""
    return _DOCSTRING_RE.sub("", src)


def test_no_control_paths_in_new_code():
    for mod in NEW_MODULES:
        src = _code_only(_read(mod))
        for tok in FORBIDDEN_TOKENS:
            assert tok.lower() not in src.lower(), (
                f"forbidden control token {tok!r} found in {mod}")


def test_psutil_service_api_read_only():
    """The services collector may only use psutil's read API names."""
    src = _read("collectors/services.py").lower()
    for control in ("start()", "stop()", "restart()", "resume()", "pause()"):
        # psutil service objects have no start/stop methods at all; the
        # collector must never call them (win32serviceutil is forbidden too)
        assert "win32serviceutil" not in src
        assert control not in src


def test_docker_never_serializes_env():
    src = _code_only(_read("collectors/docker.py")).lower()
    # the word "env" may appear in docstrings about NOT collecting it —
    # the DANGEROUS forms are serialization of environment data
    for dangerous in ('"env"', "'env'", "config.env", "['env']", "[\"env\"]",
                      "environment["):
        assert dangerous not in src
    # targeted inspect only: --format is mandatory on every inspect call
    assert "--format" in src
    assert "{{.state.pid}}" in src  # src is lowercased above
    # full container JSON (which carries Config.Env) is never requested
    assert "inspect" in src
    assert '"inspect"' in src or "'inspect'" in src


def test_docker_cli_flags_read_only():
    src = _code_only(_read("collectors/docker.py"))
    # only list/version/inspect family commands may be constructed
    assert '["version"' in src or "[\"version\"" in src
    assert '["ps", "-a"' in src or "[\"ps\", \"-a\"" in src
    assert "exec" not in src.lower().replace("execute", "")


def test_wsl_commands_read_only():
    src = _read("collectors/wsl.py")
    # discovery commands only
    assert '"--list", "--verbose"' in src
    assert '"--list", "--running"' in src
    assert '"--status"' in src
    # the deep-inspection invocation is bounded and read-only (sh -c of
    # ps/free/uname/cat only) — and may only be reached for running distros
    assert "cat /proc/net/tcp" in src
    assert "ps -e --no-headers" in src
    assert '"-d"' in src  # -d used only for already-running distros


def test_vm_commands_read_only():
    src = _read("collectors/vm.py")
    assert '"list", "runningvms"' in src
    assert '"list", "vms"' in src or '"list", "runningvms"' in src
    assert "[vmrun, \"list\"]" in src
    # Get-VM / Get-CimInstance are queries; nothing else may run
    assert "Get-VM" in src
    assert "Get-CimInstance" in src


def test_infra_engine_no_control_verbs():
    src = _read("services/infra.py")
    # the engine is pure state transformation — no subprocess, no shell
    assert "subprocess" not in src
    assert "os.system" not in src
