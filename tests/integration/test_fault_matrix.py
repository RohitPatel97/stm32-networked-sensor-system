"""Additional faults run through the compiled producer and production gateway."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.protocol import parse_frame
from sensor_monitor.sources import SourceReset, TelemetrySource
from sensor_monitor.store import TelemetryStore


class FragmentedCapture(TelemetrySource):
    name = "fragmented-c-firmware-capture"

    def __init__(self, frames):
        self.frames = frames

    async def chunks(self, stop):
        for i, frame in enumerate(self.frames):
            if i and i % 17 == 0:
                yield b'{"interrupted":'
                self.reconnects += 1
                yield SourceReset()
            data = json.dumps(frame).encode() + b"\r\n"
            # Split inside both the JSON fields and the CRLF delimiter.
            yield data[:13]
            yield data[13:-1]
            yield data[-1:]
            await asyncio.sleep(0)


async def ingest(frames, **settings):
    hub = TelemetryHub(Settings(**settings), FragmentedCapture(frames))
    await hub.run()
    stats = hub.stats()
    assert stats.frames_accepted == len(frames)
    assert stats.parse_errors == stats.sequence_anomalies == 0
    assert stats.device_rejections == 0
    return hub


@pytest.mark.parametrize("uptime,misses,expected", [
    (1001, 0, False), (1101, 0, False), (2001, 0, True), (1001, 1, True),
])
def test_first_health_task_has_time_to_run(compiled_frames, uptime, misses, expected):
    frame = compiled_frames("--duration-ms", "100")[0]
    frame["uptime_ms"] = uptime
    frame["ts_us"] = uptime * 1000
    frame["health"]["watchdog"] = "withheld"
    frame["health"]["deadline_misses"] = misses

    async def scenario():
        store = TelemetryStore(Settings())
        reading = parse_frame(json.dumps(frame).encode())
        assert await store.add_reading(reading)
        assert ("task_health" in reading.current_faults) == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("sensor", ["mpu", "bmp", "all"])
@pytest.mark.parametrize("recover", [False, True])
def test_sensors_absent_at_startup(compiled_frames, sensor, recover):
    arguments = [
        "--duration-ms", "4000", "--disconnect", sensor, "--disconnect-at-ms", "0"
    ]
    if recover:
        arguments += ["--reconnect-at-ms", "1500"]
    frames = compiled_frames(*arguments)

    async def scenario():
        hub = await ingest(frames)
        readings = await hub.store.readings("sim-hub", 100)
        health = await hub.store.health("sim-hub", now=readings[-1].received_at)
        assert health.state == ("online" if recover else "degraded")
        assert health.measurement_state == (
            "unavailable" if sensor == "all" and not recover else "current"
        )
        for field, target in (("accel_g", "mpu"), ("pressure_hpa", "bmp")):
            affected = sensor in (target, "all")
            assert (getattr(readings[0], field) is None) == affected
            assert (getattr(readings[-1], field) is None) == (affected and not recover)
        assert readings[-1].i2c_errors > 0
        alerts = await hub.store.alerts(100)
        for target, code in (
            ("mpu", "imu_unavailable"), ("bmp", "barometer_unavailable")
        ):
            if sensor in (target, "all"):
                assert {a.active for a in alerts if a.code == code} == (
                    {True, False} if recover else {True}
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("task", ["processing", "health"])
@pytest.mark.parametrize("stall_at", ["0", "1200"])
def test_other_task_stalls(compiled_frames, task, stall_at):
    frames = compiled_frames(
        "--duration-ms", "6000", "--stall-task", task, "--stall-at-ms", stall_at
    )

    async def scenario():
        hub = await ingest(frames)
        assert len(frames) == 60  # Communication stays alive despite the fault.
        latest = (await hub.store.readings("sim-hub", 1))[0]
        health = await hub.store.health("sim-hub", now=latest.received_at)
        assert health.state == "degraded"
        assert latest.firmware.health.watchdog == "withheld"
        assert "task_health" in latest.current_faults
        assert latest.firmware.health.deadline_misses > 0
        assert health.measurement_state == (
            "current" if task == "health" else "unavailable" if stall_at == "0" else "stale"
        )
        if task == "processing":
            assert latest.queue_drops > 0
            assert "sample_stale" in latest.current_faults

    asyncio.run(scenario())


@pytest.mark.parametrize("stall_at", ["0", "1200"])
def test_telemetry_stall_expires_device(compiled_frames, stall_at):
    frames = compiled_frames(
        "--duration-ms", "6000", "--stall-task", "telemetry", "--stall-at-ms", stall_at
    )

    async def scenario():
        hub = await ingest(frames)
        if stall_at == "0":
            assert not frames
            assert await hub.store.device_ids() == []
            return
        assert len(frames) == 11
        latest = (await hub.store.readings("sim-hub", 1))[0]
        for seconds, expected in ((0, "online"), (3, "stale"), (10, "offline")):
            health = await hub.store.health(
                "sim-hub", now=latest.received_at + timedelta(seconds=seconds)
            )
            assert health.state == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("capacity", [1, 2, 8])
def test_processing_queue_pressure(compiled_frames, capacity):
    frames = compiled_frames(
        "--duration-ms", "4000", "--processor-period-ms", "30",
        "--queue-capacity", str(capacity)
    )

    async def scenario():
        hub = await ingest(frames)
        latest = (await hub.store.readings("sim-hub", 1))[0]
        assert latest.queue_drops > 0
        assert "queue_drops" in latest.current_faults
        assert (await hub.store.health("sim-hub")).state == "degraded"
        assert any(a.code == "queue_drops" and a.active for a in await hub.store.alerts(100))

    asyncio.run(scenario())


@pytest.mark.parametrize("nack_every", [1, 7])
def test_i2c_retry_exhaustion_and_transient_recovery(compiled_frames, nack_every):
    frames = compiled_frames(
        "--duration-ms", "4000", "--i2c-nack-every", str(nack_every)
    )

    async def scenario():
        hub = await ingest(frames)
        latest = (await hub.store.readings("sim-hub", 1))[0]
        health = await hub.store.health("sim-hub", now=latest.received_at)
        assert latest.firmware.health.i2c_retries > 0
        if nack_every == 1:
            assert latest.i2c_errors > 0
            assert health.state == "degraded"
            assert latest.accel_g is latest.pressure_hpa is None
        else:
            assert latest.i2c_errors == 0
            assert health.state == "online"
            assert latest.accel_g is not None and latest.pressure_hpa is not None

    asyncio.run(scenario())


def test_three_devices_reboot_with_slow_subscriber_and_bounded_history(compiled_frames):
    frames = []
    for boot in range(1, 4):
        devices = [
            compiled_frames(
                "--duration-ms", "3000", "--boot-id", str(boot),
                "--device-id", f"hub-{device}"
            )
            for device in range(3)
        ]
        frames.extend(frame for samples in zip(*devices) for frame in samples)

    async def scenario():
        hub = TelemetryHub(
            Settings(history_size=20, subscriber_queue_size=8), FragmentedCapture(frames)
        )
        consumed = []
        async with hub.subscribe() as fast, hub.subscribe() as slow:
            async def drain():
                while True:
                    event = await fast.get()
                    consumed.append(event["reading"])
                    fast.task_done()

            consumer = asyncio.create_task(drain())
            try:
                await hub.run()
                await asyncio.wait_for(fast.join(), timeout=3)
            finally:
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
            stats = hub.stats()
            assert stats.frames_accepted == len(frames) == len(consumed) == 270
            assert stats.parse_errors == stats.sequence_anomalies == stats.device_rejections == 0
            assert stats.session_changes == 6
            assert stats.source_reconnects == 15
            assert stats.websocket_drops == 262
            assert slow.qsize() == 8
            assert [slow.get_nowait()["reading"] for _ in range(8)] == consumed[-8:]
            assert [(r["device_id"], r["boot_id"], r["sequence"]) for r in consumed] == [
                (f["device_id"], str(f["boot_id"]), f["frame_seq"]) for f in frames
            ]
            for device in range(3):
                readings = await hub.store.readings(f"hub-{device}", 100)
                assert len(readings) == 20
                assert [r.sequence for r in readings] == list(range(10, 30))
                assert all(r.boot_id == 3 for r in readings)
        assert hub.stats().websocket_clients == 0

    asyncio.run(scenario())
