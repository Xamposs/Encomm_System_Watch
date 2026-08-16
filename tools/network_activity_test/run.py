#!/usr/bin/env python3
"""ENCOMM SYSTEM WATCH — deterministic localhost traffic test orchestrator.

Spawns the local server + client, verifies the exact byte volumes moved,
and reports PASS/FAIL. Loopback only; nothing touches system networking.

Usage:
    python run.py [--port 19734] [--to-server-mb 5] [--to-client-mb 2] [--watch 0]

--watch N: run continuous bidirectional traffic for N seconds instead of a
           fixed transfer (used to observe live animation in SYSTEM WATCH).
"""
from __future__ import annotations

import argparse
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MB = 1024 * 1024


def _port_free(port: int) -> bool:
    """True when nothing listens on the port (bind probe — never connects)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _wait_listen(port: int, timeout: float = 10.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not _port_free(port):
            return True
        time.sleep(0.1)
    return False


def run_transfer(port: int, to_server_mb: float, to_client_mb: float) -> int:
    srv = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", str(port),
         "--to-server-mb", str(to_server_mb), "--to-client-mb", str(to_client_mb)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        if not _wait_listen(port):
            print(f"FAIL server did not listen on {port}")
            srv.kill()
            return 1
        cli = subprocess.Popen(
            [sys.executable, str(HERE / "client.py"), "--port", str(port),
             "--to-server-mb", str(to_server_mb), "--to-client-mb", str(to_client_mb)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        out_cli, _ = cli.communicate(timeout=120)
        out_srv, _ = srv.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        srv.kill()
        print("FAIL transfer timed out")
        return 1

    print(out_srv.strip())
    print(out_cli.strip())

    m_srv = re.search(r"received=(\d+) sent=(\d+)", out_srv)
    m_cli = re.search(r"sent=(\d+) received=(\d+)", out_cli)
    if not m_srv or not m_cli:
        print("FAIL could not parse transfer reports")
        return 1
    srv_recv, srv_sent = map(int, m_srv.groups())
    cli_sent, cli_recv = map(int, m_cli.groups())

    exp_to_server = int(to_server_mb * MB)
    exp_to_client = int(to_client_mb * MB)
    ok = True
    checks = [
        ("server received client bytes", srv_recv, exp_to_server),
        ("client sent client bytes", cli_sent, exp_to_server),
        ("server sent client bytes", srv_sent, exp_to_client),
        ("client received server bytes", cli_recv, exp_to_client),
    ]
    for name, got, want in checks:
        status = "PASS" if got == want else "FAIL"
        if got != want:
            ok = False
        print(f"  {status}  {name}: {got} == {want}")

    print("RESULT:", "ALL BYTES VERIFIED" if ok else "MISMATCH")
    return 0 if ok else 1


def run_watch(port: int, seconds: float) -> int:
    srv = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", str(port),
         "--duration", str(seconds)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        if not _wait_listen(port):
            print(f"FAIL server did not listen on {port}")
            srv.kill()
            return 1
        cli = subprocess.Popen(
            [sys.executable, str(HERE / "client.py"), "--port", str(port),
             "--duration", str(seconds)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        out_cli, _ = cli.communicate(timeout=seconds + 60)
        out_srv, _ = srv.communicate(timeout=seconds + 60)
    except subprocess.TimeoutExpired:
        srv.kill()
        print("FAIL watch timed out")
        return 1
    print(out_srv.strip())
    print(out_cli.strip())
    print("RESULT: continuous traffic finished (check SYSTEM WATCH for live activity)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19734)
    ap.add_argument("--to-server-mb", type=float, default=5.0)
    ap.add_argument("--to-client-mb", type=float, default=2.0)
    ap.add_argument("--watch", type=float, default=0.0)
    args = ap.parse_args()
    if args.watch > 0:
        return run_watch(args.port, args.watch)
    return run_transfer(args.port, args.to_server_mb, args.to_client_mb)


if __name__ == "__main__":
    sys.exit(main())
