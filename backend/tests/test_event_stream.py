"""Event stream pub/sub tests."""
import asyncio

from app.services.event_stream import EventStream


def test_publish_subscribe_roundtrip():
    async def run():
        stream = EventStream()
        q = stream.subscribe()
        stream.publish([{"event_id": "1"}, {"event_id": "2"}, {"event_id": "3"}])
        got = []
        for _ in range(3):
            got.append(await asyncio.wait_for(q.get(), timeout=1.0))
        stream.unsubscribe(q)
        stream.publish([{"event_id": "4"}])  # after unsubscribe: no subscriber
        return got

    got = asyncio.run(run())
    assert [g["event_id"] for g in got] == ["1", "2", "3"]


def test_slow_subscriber_drops_oldest_not_crash():
    async def run():
        stream = EventStream(queue_size=2)
        q = stream.subscribe()
        # fill queue past capacity -> oldest dropped, newest kept
        for i in range(5):
            stream.publish([{"event_id": f"e{i}"}])
        got = []
        for _ in range(2):
            got.append(await asyncio.wait_for(q.get(), timeout=1.0))
        assert q.empty()
        return got

    got = asyncio.run(run())
    # cap 2 keeps only the two newest (e3, e4); e0..e2 were dropped
    assert [g["event_id"] for g in got] == ["e3", "e4"]


def test_unsubscribe_stops_delivery():
    async def run():
        stream = EventStream()
        q = stream.subscribe()
        stream.unsubscribe(q)
        stream.publish([{"event_id": "x"}])
        try:
            await asyncio.wait_for(q.get(), timeout=0.05)
            return "got"
        except asyncio.TimeoutError:
            return "empty"

    assert asyncio.run(run()) == "empty"
