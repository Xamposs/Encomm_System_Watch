"""WSL discovery — READ-ONLY (Phase 09).

Only read-only ``wsl.exe`` commands are ever used:

    wsl --list --verbose     installed distributions + state + version
    wsl --list --running     running distributions
    wsl --status             default distro + default WSL version

A STOPPED distribution is NEVER started (no ``wsl -d <stopped> ...``), and it
is never inspected internally. Deep internal snapshots are attempted ONLY
for distributions already confirmed RUNNING, via a single bounded read-only
command (process count, top process names, kernel, memory summary); any
failure degrades to ``summary=None`` — omitted, never fabricated.

``wsl.exe`` writes UTF-16-LE to pipes; all parsing goes through a resilient
decoder (BOM-aware, then utf-16-le, then utf-8) so weird locales or future
format changes degrade to missing fields instead of crashes.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("esw")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_WSL_EXE = "wsl.exe"
_HEADER_RE = re.compile(r"^\s*NAME\b")
_LINE_RE = re.compile(r"^\s*(\*)?\s*(\S.*?)\s{2,}(\S+)\s+(\S+)\s*$")
_MSG_NO_DISTROS = (
    "no installed distributions",
    "no distributions installed",
    "is not supported",
)


@dataclass
class WslDistro:
    name: str
    state: str = "Unknown"            # Running | Stopped | Unknown
    version: Optional[int] = None     # 1 or 2 when exposed
    is_default: bool = False
    summary: Optional[dict[str, Any]] = None   # bounded internal snapshot (RUNNING only)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "state": self.state,
            "is_default": self.is_default,
        }
        if self.version is not None:
            d["version"] = self.version
        if self.summary is not None:
            d["summary"] = self.summary
        return d


@dataclass
class WslState:
    installed: bool = False
    distributions: list[WslDistro] = field(default_factory=list)
    running: list[str] = field(default_factory=list)
    default: Optional[str] = None
    default_version: Optional[int] = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "distributions": [d.to_dict() for d in self.distributions],
            "running": self.running,
            "default": self.default,
            "default_version": self.default_version,
            "error": self.error,
        }


def _decode(data: bytes) -> str:
    """wsl.exe writes UTF-16-LE; decode defensively."""
    if not data:
        return ""
    for enc in ("utf-16", "utf-16-le", "utf-8"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a read-only wsl.exe command; never shows a console window."""
    return subprocess.run(
        [_WSL_EXE, *args],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )


def _parse_list_verbose(raw: str, running: set[str], default: Optional[str]) -> list[WslDistro]:
    """Parse ``wsl --list --verbose`` output (UTF-16 decoded).

    Format::

        NAME              STATE           VERSION
      * Ubuntu            Stopped         2
        docker-desktop    Stopped         2

    Resilient: header variants, trailing spaces, version cell missing or
    non-numeric -> None, unknown rows skipped.
    """
    distros: list[WslDistro] = []
    for line in raw.splitlines():
        line = line.replace("\r", "").replace("\ufeff", "")
        if not line.strip():
            continue
        if _HEADER_RE.match(line):
            continue
        if any(msg in line.lower() for msg in _MSG_NO_DISTROS):
            continue
        m = _LINE_RE.match(line)
        if not m:
            # fall back to whitespace split for unusual spacing (the default
            # marker `*` may be its own token)
            parts = [p for p in line.strip().split() if p.strip()]
            if len(parts) < 2:
                continue
            if parts[0] == "*":
                is_default = True
                rest = parts[1:]
            else:
                is_default = False
                rest = parts
            name = rest[0]
            state = rest[1] if len(rest) >= 2 else "Unknown"
            ver = _parse_version(rest[2]) if len(rest) >= 3 else None
        else:
            star, name, state, ver_raw = m.groups()
            name = name.strip()
            is_default = bool(star) or (default is not None and name == default)
            ver = _parse_version(ver_raw)
        if not name or name.lower() in ("name", "distributions"):
            continue
        state_norm = "Running" if state.lower() in ("running",) else (
            "Stopped" if state.lower() in ("stopped", "not running") else state
        )
        distros.append(WslDistro(
            name=name,
            state=state_norm,
            version=ver,
            is_default=is_default,
        ))
    # state truth comes from the authoritative running list
    for d in distros:
        if d.name in running:
            d.state = "Running"
    return distros


def _parse_version(raw: str) -> Optional[int]:
    try:
        v = int(raw.strip())
        return v if v in (1, 2) else None
    except (TypeError, ValueError):
        return None


def _parse_plain_list(raw: str) -> list[WslDistro]:
    """Parse ``wsl --list`` (plain) output.

    Format::

        Windows Subsystem for Linux Distributions:
        Ubuntu (Default)
        docker-desktop

    Distribution names only (state/version are not exposed in this format —
    the caller uses it purely as a fallback probe). UTF-16 decoded upstream.
    """
    distros: list[WslDistro] = []
    for line in raw.splitlines():
        line = line.replace("\r", "").replace("\ufeff", "").strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("windows subsystem for linux") or low == "name":
            continue
        if any(msg in low for msg in _MSG_NO_DISTROS):
            continue
        is_default = " (default)" in low
        name = line.replace("(Default)", "").replace("(default)", "").strip()
        if not name:
            continue
        distros.append(WslDistro(name=name, state="Unknown", version=None,
                                 is_default=is_default))
    return distros


def _parse_running(raw: str) -> list[str]:
    """``wsl --list --running`` -> list of running distribution names."""
    out: list[str] = []
    for line in raw.splitlines():
        line = line.replace("\r", "").strip()
        if not line:
            continue
        low = line.lower()
        if "name" == low or low.startswith("there are no running") or "windows subsystem" in low:
            continue
        out.append(line)
    return out


def _parse_status(raw: str) -> tuple[Optional[str], Optional[int]]:
    """``wsl --status`` -> (default distro, default version)."""
    default: Optional[str] = None
    ver: Optional[int] = None
    for line in raw.splitlines():
        line = line.replace("\r", "").strip()
        low = line.lower()
        if low.startswith("default distribution"):
            _, _, v = line.partition(":")
            default = v.strip() or None
        elif low.startswith("default version"):
            _, _, v = line.partition(":")
            ver = _parse_version(v)
    return default, ver


def _bounded_summary(name: str, timeout: float = 8.0) -> Optional[dict[str, Any]]:
    """Bounded internal snapshot for an ALREADY RUNNING distro.

    READ-ONLY commands only, single invocation, hard timeout, output capped.
    Any failure (distro died, command missing, timeout) -> None (omitted).
    """
    script = (
        "printf 'PROC_COUNT='; ps -e --no-headers 2>/dev/null | wc -l; "
        "printf 'KERNEL='; uname -r 2>/dev/null; "
        "printf 'TOPS\\n'; ps -e --no-headers -o comm= 2>/dev/null | sort | uniq -c | sort -rn | head -8; "
        "printf 'MEM\\n'; free -m 2>/dev/null | head -2; "
        "printf 'LISTEN\\n'; cat /proc/net/tcp 2>/dev/null | awk 'NR>1 {split($2,a,\":\"); print strtonum(\"0x\" a[2])}' | sort -n | uniq | head -12"
    )
    try:
        r = subprocess.run(
            [_WSL_EXE, "-d", name, "--", "sh", "-c", script],
            capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except Exception as exc:  # noqa: BLE001 — summary must never break discovery
        log.debug("wsl summary %s failed: %s", name, exc)
        return None
    if r.returncode != 0:
        return None
    text = _decode(r.stdout)
    if len(text) > 4000:
        text = text[:4000]
    summary: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("PROC_COUNT="):
            v = line.split("=", 1)[1].strip()
            if v.isdigit():
                summary["process_count"] = int(v)
        elif line.startswith("KERNEL="):
            v = line.split("=", 1)[1].strip()
            if v:
                summary["kernel"] = v
    # top process names: lines after TOPS header until MEM header
    tops: list[str] = []
    in_tops = False
    for line in text.splitlines():
        line = line.rstrip()
        if line == "TOPS":
            in_tops = True
            continue
        if line == "MEM":
            in_tops = False
            continue
        if in_tops:
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                tops.append(f"{parts[1]} ({parts[0]})")
    if tops:
        summary["top_processes"] = tops
    # memory: free -m "Mem:" line -> total/used
    for line in text.splitlines():
        line = line.rstrip()
        if line.startswith("Mem:"):
            cells = line.split()
            if len(cells) >= 3:
                try:
                    total_mb, used_mb = int(cells[1]), int(cells[2])
                    summary["memory_total_mb"] = total_mb
                    summary["memory_used_mb"] = used_mb
                except (TypeError, ValueError):
                    pass
            break
    # listening ports (host-view hex parsing of /proc/net/tcp)
    listen: list[int] = []
    in_listen = False
    for line in text.splitlines():
        line = line.rstrip()
        if line == "LISTEN":
            in_listen = True
            continue
        if in_listen:
            p = line.strip()
            if p.isdigit() and int(p) > 0:
                listen.append(int(p))
    if listen:
        summary["listening_tcp_ports"] = listen[:12]
    if not summary:
        return None
    return summary


class WslCollector:
    """Read-only WSL discovery. Failure-isolated: any command error degrades
    to an unavailable WSL section, never a crash.
    """

    def __init__(self, wsl_exe: str = _WSL_EXE, deep: bool = True) -> None:
        self._wsl_exe = wsl_exe
        self._deep = deep

    def collect(self) -> WslState:
        state = WslState()
        # 1) status (defaults) — failure is non-fatal
        try:
            r = _run(["--status"])
            default, ver = _parse_status(_decode(r.stdout))
            state.default, state.default_version = default, ver
        except Exception as exc:  # noqa: BLE001
            state.error = f"wsl --status failed: {exc}"
        # 2) running list — authoritative for running state
        running: set[str] = set()
        try:
            r = _run(["--list", "--running"])
            running = {d for d in _parse_running(_decode(r.stdout)) if d}
        except Exception as exc:  # noqa: BLE001
            state.error = f"wsl --list --running failed: {exc}"
        state.running = sorted(running)
        # 3) installed list
        try:
            r = _run(["--list", "--verbose"])
            raw = _decode(r.stdout)
            if "no installed distributions" in raw.lower():
                state.installed = False
            elif not raw.strip():
                # EMPTY verbose output is NOT evidence of "no WSL" — the WSL
                # service (LxssManager) can return empty enumeration while
                # --status/--running still answer. Fall back to the plain
                # `wsl --list` format; only report not-installed when BOTH
                # probes agree. An unparseable/empty result is surfaced
                # truthfully as an error, never as a silent "not installed".
                r2 = _run(["--list"])
                raw2 = _decode(r2.stdout)
                distros2 = _parse_plain_list(raw2)
                if distros2:
                    state.installed = True
                    state.distributions = distros2
                else:
                    state.installed = False
                    state.error = (
                        "wsl --list --verbose returned empty output (WSL "
                        "service enumeration unresponsive)"
                    )
            else:
                state.installed = True
                state.distributions = _parse_list_verbose(raw, running, state.default)
        except Exception as exc:  # noqa: BLE001
            state.error = f"wsl --list --verbose failed: {exc}"
            return state
        # 4) bounded internal summary — RUNNING distributions only, never stopped
        if self._deep and state.installed:
            for d in state.distributions:
                if d.state == "Running":
                    d.summary = _bounded_summary(d.name)
        return state
