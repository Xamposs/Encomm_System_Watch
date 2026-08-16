"""Async pub/sub event bus for WebSocket clients."""
from __future__ import annotations

import asyncio


class EventStream:
    def __init__(self, queue_size: int = 500) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, events: list[dict]) -> None:
        for q in list(self._subs):
            for ev in events:
                try:
                    q.put_nowait(ev)
                except asyncio.QueueFull:
                    # slow client: drop the oldest event, keep the newest
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        q.put_nowait(ev)
                    except asyncio.QueueFull:
                        break

    def publish_message(self, message: dict) -> None:
        """Publish a complete protocol message (e.g. network_activity batch).

        Same bounded-queue semantics as publish(); the ws endpoint routes
        these directly instead of wrapping them in an events batch.
        """
        for q in list(self._subs):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    break
