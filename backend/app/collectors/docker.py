"""Docker Engine discovery — READ-ONLY (Phase 09).

Detection uses the local Docker Engine READ-ONLY API surface via the
installed ``docker`` CLI (Docker Desktop's local npipe connection on Windows
— never TCP 2375, never insecure API access). Only list/version/inspect
queries run; there are NO control paths (no start/stop/restart/exec).

SECURITY: container environment is NEVER collected or serialized. PID
lookups use a targeted ``docker inspect --format '{{.State.Pid}}'`` — the
full inspect JSON (which carries ``Config.Env`` and secrets) is never
parsed or stored. Labels are intentionally not collected.

Engine-down degrades to a truthful ``engine_status: NOT_RUNNING`` with an
empty container list — the app keeps running.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

log = logging.getLogger("esw")

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PORT_RE = re.compile(
    r"^(?:(\[[0-9a-fA-F:]+\]|\d+\.\d+\.\d+\.\d+|::|0\.0\.0\.0):)?"
    r"(\d+)->(\d+)/(tcp|udp|sctp)$"
)

_MAX_PID_INSPECTS = 20  # bound per-poll subprocess cost


@dataclass
class DockerContainer:
    id: str                        # short 12-char id
    name: str
    image: str
    state: str = "unknown"         # running | exited | created | paused | ...
    status: str = ""
    created: Optional[float] = None
    ports: list[dict[str, Any]] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    pid: Optional[int] = None      # host PID — running containers only

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "image": self.image,
            "state": self.state,
            "status": self.status,
            "ports": self.ports,
            "networks": self.networks,
        }
        if self.created is not None:
            d["created"] = self.created
        if self.pid is not None:
            d["pid"] = self.pid
        return d


@dataclass
class DockerState:
    available: bool = False        # docker CLI reachable at all
    engine_status: str = "UNKNOWN" # RUNNING | NOT_RUNNING | UNKNOWN
    version: Optional[str] = None
    containers: list[DockerContainer] = field(default_factory=list)
    source: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "engine_status": self.engine_status,
            "version": self.version,
            "containers": [c.to_dict() for c in self.containers],
            "source": self.source,
            "error": self.error,
        }


def _parse_ports(ports_str: str) -> list[dict[str, Any]]:
    """Parse ``docker ps`` Ports field: "0.0.0.0:8080->80/tcp, 5432/tcp"."""
    out: list[dict[str, Any]] = []
    for piece in (ports_str or "").split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "->" not in piece:
            # no host mapping (container port only) — still evidence of exposure
            m = re.match(r"^(\d+)/(tcp|udp|sctp)$", piece)
            if m:
                out.append({
                    "host_ip": None, "host_port": None,
                    "container_port": int(m.group(1)), "proto": m.group(2),
                })
            continue
        m = _PORT_RE.match(piece)
        if m:
            host_ip, host_port, cport, proto = m.groups()
            out.append({
                "host_ip": host_ip.strip("[]") if host_ip else None,
                "host_port": int(host_port),
                "container_port": int(cport),
                "proto": proto,
            })
    return out


def _parse_created(raw: str) -> Optional[float]:
    """Best-effort parse of docker's "2026-08-19 12:34:56 +0300 EEST"."""
    if not raw or len(raw) < 19:
        return None
    try:
        return datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _engine_down(output: str, err: str) -> bool:
    low = (output + " " + err).lower()
    return ("cannot find the file" in low or "error during connect" in low
            or "is not running" in low or "daemon is running" in low
            or "the docker daemon" in low and "not" in low)


class DockerCollector:
    """Read-only Docker discovery via the local engine CLI (npipe)."""

    def __init__(self, cli: str = "docker",
                 runner: Optional[Callable[[list[str], float], Any]] = None) -> None:
        self._cli = cli
        self._runner = runner or self._default_runner

    # ------------------------------------------------------------- internals

    def _default_runner(self, args: list[str], timeout: float = 10.0):
        return subprocess.run(
            [self._cli, *args], capture_output=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )

    def _run(self, args: list[str], timeout: float = 10.0):
        try:
            return self._runner(args, timeout)
        except FileNotFoundError:
            return None  # docker CLI not installed
        except subprocess.TimeoutExpired:
            return None

    def _stdout(self, r) -> str:
        return r.stdout.decode("utf-8", errors="replace") if r and r.stdout else ""

    def _stderr(self, r) -> str:
        return r.stderr.decode("utf-8", errors="replace") if r and r.stderr else ""

    # -------------------------------------------------------------- public

    def collect(self) -> DockerState:
        state = DockerState(source=f"CLI ({self._cli})")
        # 1) engine presence + version (client info is not engine evidence)
        r = self._run(["version", "--format", "{{json .}}"])
        if r is None:
            state.engine_status = "UNKNOWN"
            state.error = "docker CLI not found"
            return state
        state.available = True
        out = self._stdout(r)
        server_version = None
        try:
            parsed = json.loads(out)
            server = parsed.get("Server")
            if isinstance(server, dict):
                server_version = server.get("Version")
        except (ValueError, AttributeError):
            server = None
        # RUNNING only when the SERVER section actually carries a version —
        # `"Server": null` (client-only output) means the engine is down
        if server_version:
            state.engine_status = "RUNNING"
            state.version = server_version
        else:
            state.engine_status = "NOT_RUNNING"
            state.error = self._stderr(r).strip()[:200] or "engine not reachable"
            return state

        # 2) container list (all states) — one JSON object per line
        r = self._run(["ps", "-a", "--format", "{{json .}}"])
        if r is None:
            state.error = "docker ps unavailable"
            return state
        for line in self._stdout(r).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except ValueError:
                continue
            cid = str(j.get("ID", ""))[:12]
            if not cid:
                continue
            names = j.get("Names", "") or ""
            name = str(names).split(",")[0].lstrip("/") if names else cid
            ports = _parse_ports(str(j.get("Ports", "") or ""))
            nets = [n for n in str(j.get("Networks", "") or "").split(",") if n]
            state.containers.append(DockerContainer(
                id=cid,
                name=name,
                image=str(j.get("Image", "") or ""),
                state=str(j.get("State", "") or "unknown").lower(),
                status=str(j.get("Status", "") or ""),
                created=_parse_created(str(j.get("CreatedAt", "") or "")),
                ports=ports,
                networks=nets,
            ))

        # 3) host PIDs for RUNNING containers (bounded, targeted inspect —
        #    never the full JSON, which would expose container environment
        #    and any credentials inside it)
        running = [c for c in state.containers if c.state == "running"]
        for c in running[:_MAX_PID_INSPECTS]:
            r = self._run(["inspect", "--format", "{{.State.Pid}}", c.id])
            pid_raw = self._stdout(r).strip()
            if pid_raw.isdigit():
                c.pid = int(pid_raw)
        return state
