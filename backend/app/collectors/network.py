"""Real network socket collection via psutil (TCP + UDP).

Sockets are classified as:
  - listening  : no remote endpoint (bound socket)
  - localhost  : both endpoints on loopback
  - external   : remote endpoint outside this machine

On Windows some sockets are owned by elevated/system processes; psutil may
return pid=None for those. They are mapped to the SYSTEM node instead of
crashing the collector.
"""
from __future__ import annotations

import socket
from typing import Optional

import psutil

from ..models.entities import ConnectionInfo

LOCALHOST_IPS = {"127.0.0.1", "::1", "localhost"}
ANY_IPS = {"0.0.0.0", "::", "[::]"}


def is_loopback(ip: str) -> bool:
    if not ip:
        return False
    if ip in LOCALHOST_IPS:
        return True
    if ip.startswith("127."):
        return True
    return ip == "::1" or ip.lower().startswith("0:0:0:0:0:0:0:1")


def is_local_addr(ip: str) -> bool:
    return is_loopback(ip) or ip in ANY_IPS


def _raw_connections() -> list:
    try:
        return psutil.net_connections(kind="inet")
    except psutil.AccessDenied:
        # fall back to per-family enumeration
        out: list = []
        for kind in ("tcp", "udp"):
            try:
                out.extend(psutil.net_connections(kind=kind))
            except Exception:
                continue
        return out


class NetworkCollector:
    def collect(self, pid_to_sid: dict[int, str]) -> tuple[dict[str, ConnectionInfo], dict[str, Optional[str]]]:
        conns: dict[str, ConnectionInfo] = {}
        owner_map: dict[str, Optional[str]] = {}
        try:
            raw = _raw_connections()
        except Exception:
            return conns, owner_map

        for c in raw:
            try:
                pid = c.pid
                proto = "udp" if c.type == socket.SOCK_DGRAM else "tcp"
                lip = c.laddr.ip if c.laddr else ""
                lport = c.laddr.port if c.laddr else 0
                rip = c.raddr.ip if c.raddr else ""
                rport = c.raddr.port if c.raddr else 0
                if not lip:
                    continue
                if not rip:
                    kind = "listening"
                elif is_local_addr(lip) and is_loopback(rip):
                    kind = "localhost"
                else:
                    kind = "external"

                conn = ConnectionInfo(
                    pid=pid,
                    proto=proto,
                    local_ip=lip,
                    local_port=lport,
                    remote_ip=rip,
                    remote_port=rport,
                    state=c.status,
                    kind=kind,
                )
                conns[conn.key] = conn
                owner_map[conn.key] = pid_to_sid.get(pid) if pid is not None else None
            except Exception:
                continue
        return conns, owner_map
