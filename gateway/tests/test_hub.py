from __future__ import annotations

import asyncio

from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.models import WireTelemetry
from sensor_monitor.protocol import encode_frame
from sensor_monitor.sources import ReplaySource, TelemetrySource

from .helpers import reading


class CaptureSource(TelemetrySource):
    def __init__(self, chunks: list[bytes]) -> None:
        self.capture = chunks

    async def chunks(self, stop: asyncio.Event):
        for chunk in self.capture:
            yield chunk


def test_hub_preserves_valid_frames_and_counts_rejections() -> None:
    async def scenario() -> None:
        one = WireTelemetry(**reading().model_dump(exclude={"received_at"}))
        two = one.model_copy(update={"device_id": "second-node"})
        source = CaptureSource([encode_frame(one)[:20], encode_frame(one)[20:] +
                                b"invalid\n" + encode_frame(two)])
        hub = TelemetryHub(Settings(max_devices=1), source)
        await hub.run()
        assert hub.stats().frames_received == 3
        assert hub.stats().frames_accepted == 1
        assert hub.stats().parse_errors == 1
        assert hub.stats().device_rejections == 1

    asyncio.run(scenario())


def test_slow_subscriber_drops_oldest_without_blocking() -> None:
    async def scenario() -> None:
        hub = TelemetryHub(Settings(subscriber_queue_size=1), CaptureSource([]))
        async with hub.subscribe() as queue:
            await hub._broadcast({"sequence": 1})
            await hub._broadcast({"sequence": 2})
            assert queue.get_nowait() == {"sequence": 2}
            assert hub.stats().websocket_drops == 1
            queue.task_done()
            await asyncio.wait_for(queue.join(), timeout=0.2)
        assert hub.stats().websocket_clients == 0

    asyncio.run(scenario())


def test_duplicate_and_old_fault_frames_cannot_raise_alerts_or_reach_subscribers() -> None:
    async def scenario() -> None:
        sample = WireTelemetry(**reading().model_dump(exclude={"received_at"}))
        frames = [sample, sample.model_copy(update={"temperature_c": 100}),
                  sample.model_copy(update={"sequence": 41, "temperature_c": 100}),
                  sample.model_copy(update={"sequence": 43})]
        hub = TelemetryHub(Settings(), CaptureSource([b"".join(map(encode_frame, frames))]))
        async with hub.subscribe() as queue:
            await hub.run()
            assert hub.stats().frames_received == 4
            assert hub.stats().frames_accepted == 2
            assert hub.stats().sequence_anomalies == 2
            assert await hub.store.alerts(10) == []
            assert queue.qsize() == 2
            assert queue.get_nowait()["reading"]["sequence"] == 42
            assert queue.get_nowait()["reading"]["sequence"] == 43
            assert [item.sequence for item in await hub.store.readings(sample.device_id, 10)] == [
                42, 43,
            ]

    asyncio.run(scenario())


def test_replay_rejects_oversized_fault_without_alerts_and_recovers(tmp_path) -> None:
    async def scenario() -> None:
        sample = WireTelemetry(**reading().model_dump(exclude={"received_at"}))
        hot = sample.model_copy(update={"sequence": 43, "temperature_c": 100})
        recovered = sample.model_copy(update={"sequence": 44})
        capture = tmp_path / "oversized-fault.jsonl"
        capture.write_bytes(
            encode_frame(sample)
            + encode_frame(hot).removesuffix(b"\n").ljust(100_000, b" ")
            + b"\n"
            + encode_frame(recovered).removesuffix(b"\n")
        )
        hub = TelemetryHub(Settings(), ReplaySource(capture, rate_hz=1000, loop=False))
        async with hub.subscribe() as queue:
            await hub.run()
            assert hub.stats().frames_received == 3
            assert hub.stats().frames_accepted == 2
            assert hub.stats().parse_errors == 1
            assert hub.stats().sequence_anomalies == 0
            assert await hub.store.alerts(10) == []
            assert queue.qsize() == 2
            assert queue.get_nowait()["reading"]["sequence"] == 42
            assert queue.get_nowait()["reading"]["sequence"] == 44
            assert [item.sequence for item in await hub.store.readings(sample.device_id, 10)] == [
                42, 44,
            ]
            health = await hub.store.health(sample.device_id)
            assert health and health.last_sequence == 44

    asyncio.run(scenario())
