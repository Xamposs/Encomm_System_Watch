#!/usr/bin/env python3
"""ENCOMM SYSTEM WATCH — local deterministic traffic server (test utility).

Listens on 127.0.0.1, accepts ONE client, receives exactly ``to-server``
megabytes, then sends exactly ``to-client`` megabytes back and closes.
Everything is loopback-only and metadata-only; it never touches system
networking configuration.

Used by tools/network_activity_test/run.py and the acceptance suite to
verify: PID attribution, connection tuple mapping, direction, byte rates,
edge activity and pulse animation.

Usage:
    python server.py [--port 19734] [--to-server-mb 25] [--to-client-mb 10] [--duration 0]

--duration N: instead of fixed volumes, stream both directions continuously
for N seconds (used to watch live animation). Volumes are ignored then.
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

CHUNK = 65536


class _ResultThread(threading.Thread):
    """Thread that captures its target's return value."""

    def run(self) -> None:
        self.result = self._target(*self._args, **self._kwargs)  # type: ignore[attr-defined]


def _recv_all(conn: socket.socket) -> int:
    total = 0
    while True:
        data = conn.recv(CHUNK)
        if not data:
            return total
        total += len(data)


def _send_loop(conn: socket.socket, chunk: bytes) -> int:
    total = 0
    try:
        while True:
            conn.sendall(chunk)
            total += len(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    return total


def serve(port: int, to_server_mb: float, to_client_mb: float, duration: float) -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    conn, addr = srv.accept()
    t0 = time.time()
    recv_total = 0
    send_total = 0
    if duration > 0:
        chunk = b"S" * CHUNK
        stop = time.time() + duration
        thread = _ResultThread(target=_send_loop, args=(conn, chunk))
        thread.start()
        while time.time() < stop:
            data = conn.recv(CHUNK)
            if not data:
                break
            recv_total += len(data)
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        thread.join(timeout=5)
        send_total = thread.result or 0
    else:
        recv_total = _recv_all(conn)  # client half-closes after sending
        payload = b"R" * CHUNK
        remaining = int(to_client_mb * 1024 * 1024)
        while remaining > 0:
            n = min(CHUNK, remaining)
            conn.sendall(payload[:n])
            remaining -= n
            send_total += n
        conn.close()
    elapsed = time.time() - t0
    print(f"SERVER_OK received={recv_total} sent={send_total} elapsed={elapsed:.3f}s "
          f"rate_kbs={((recv_total + send_total) / max(elapsed, 1e-6)) / 1024:.1f}")
    sys.stdout.flush()
    srv.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19734)
    ap.add_argument("--to-server-mb", type=float, default=25.0)
    ap.add_argument("--to-client-mb", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0.0)
    args = ap.parse_args()
    return serve(args.port, args.to_server_mb, args.to_client_mb, args.duration)


if __name__ == "__main__":
    sys.exit(main())
