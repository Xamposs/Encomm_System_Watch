#!/usr/bin/env python3
"""ENCOMM SYSTEM WATCH — local deterministic traffic client (test utility).

Connects to the local test server on 127.0.0.1, sends exactly
``to-server`` megabytes, half-closes, then reads ``to-client`` megabytes.

Usage:
    python client.py [--port 19734] [--to-server-mb 25] [--to-client-mb 10] [--duration 0]

--duration N: stream both directions continuously for N seconds.
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


def _recv_n(conn: socket.socket, n: int) -> int:
    total = 0
    while total < n:
        data = conn.recv(min(CHUNK, n - total))
        if not data:
            break
        total += len(data)
    return total


def _send_loop(conn: socket.socket, chunk: bytes) -> int:
    total = 0
    try:
        while True:
            conn.sendall(chunk)
            total += len(chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    return total


def run(port: int, to_server_mb: float, to_client_mb: float, duration: float) -> int:
    conn = socket.create_connection(("127.0.0.1", port), timeout=15)
    t0 = time.time()
    send_total = 0
    recv_total = 0
    if duration > 0:
        chunk = b"C" * CHUNK
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
        payload = b"C" * CHUNK
        remaining = int(to_server_mb * 1024 * 1024)
        while remaining > 0:
            n = min(CHUNK, remaining)
            conn.sendall(payload[:n])
            remaining -= n
            send_total += n
        try:
            conn.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        recv_total = _recv_n(conn, int(to_client_mb * 1024 * 1024))
        conn.close()
    elapsed = time.time() - t0
    print(f"CLIENT_OK sent={send_total} received={recv_total} elapsed={elapsed:.3f}s "
          f"rate_kbs={((send_total + recv_total) / max(elapsed, 1e-6)) / 1024:.1f}")
    sys.stdout.flush()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=19734)
    ap.add_argument("--to-server-mb", type=float, default=25.0)
    ap.add_argument("--to-client-mb", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=0.0)
    args = ap.parse_args()
    return run(args.port, args.to_server_mb, args.to_client_mb, args.duration)


if __name__ == "__main__":
    sys.exit(main())
