from __future__ import annotations

import asyncio
import copy
import json
import subprocess
import time
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.firmware_source import FirmwareSource
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.protocol import FrameDecodeError, encode_frame, parse_frame
from sensor_monitor.sources import SourceReset, TelemetrySource
from sensor_monitor.store import TelemetryStore

ROOT = Path(__file__).resolve().parents[2]


class Capture(TelemetrySource):
    name = "capture"

    def __init__(self, chunks):
        self.data = chunks

    async def chunks(self, stop):
        for data in self.data:
            yield data


def raw(frame):
    return json.dumps(frame, allow_nan=True).encode() + b"\n"


def wait_finished(client):
    deadline = time.monotonic() + 5
    while client.get("/healthz").json()["acquisition_running"]:
        assert time.monotonic() < deadline, "firmware source did not finish"
        time.sleep(0.01)


class ReadyFirmwareSource(FirmwareSource):
    """Keep the real C process behind a test-controlled subscription barrier."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ready = asyncio.Event()

    async def chunks(self, stop):
        await self.ready.wait()
        async for chunk in super().chunks(stop):
            yield chunk


@pytest.mark.parametrize("sensor", ["mpu", "bmp", "all"])
@pytest.mark.parametrize("rate_hz", [100, 1000])
def test_compiled_c_to_http_websocket_and_production_dashboard(
    binary, tmp_path, sensor, rate_hz
):
    source = ReadyFirmwareSource(
        binary,
        (
            "--duration-ms",
            "3000",
            "--disconnect",
            sensor,
            "--disconnect-at-ms",
            "500",
            "--reconnect-at-ms",
            "1500",
        ),
        rate_hz=rate_hz,
    )
    events = []
    with TestClient(create_app(Settings(log_dir=tmp_path), source)) as client:
        assert "Firmware diagnostics" in client.get("/").text
        assert "function renderReading" in client.get("/static/app.js").text
        with client.websocket_connect("/ws/telemetry") as ws:
            assert ws.receive_json()["type"] == "snapshot"

            async def release_source():
                async def wait_for_subscription():
                    while client.app.state.hub.stats().websocket_clients != 1:
                        await asyncio.sleep(0)

                await asyncio.wait_for(wait_for_subscription(), timeout=5)
                source.ready.set()

            # Static-file startup and socket setup must not consume the brief outage.
            client.portal.call(release_source)
            while True:
                event = ws.receive_json()
                if event["type"] == "telemetry":
                    events.append(event)
                    if event["reading"]["sequence"] == 29:
                        break
        wait_finished(client)
        readings = client.get("/api/v1/devices/sim-hub/readings").json()
        stats = client.get("/api/v1/stats").json()
        assert stats["frames_accepted"] == 30 and stats["parse_errors"] == 0
        assert stats["sequence_anomalies"] == 0
        assert [e["reading"]["sequence"] for e in events] == list(range(30))
        assert len(readings) == 30
        for field in ("pressure_hpa", "accel_g"):
            missing = sensor == "all" or sensor == (
                "bmp" if field == "pressure_hpa" else "mpu"
            )
            assert any(r[field] is None for r in readings) == missing
            assert readings[-1][field] is not None
        assert readings[-1]["pressure_hpa"] is not None
        assert readings[-1]["i2c_errors"] > 0  # Cumulative totals survive recovery.
        assert client.get("/api/v1/devices/sim-hub").json()["state"] == "online"
        alerts = client.get("/api/v1/alerts").json()
        codes = ["i2c_errors"]
        if sensor in ("bmp", "all"):
            codes.append("barometer_unavailable")
        if sensor in ("mpu", "all"):
            codes.append("imu_unavailable")
        for code in codes:
            assert {a["active"] for a in alerts if a["code"] == code} == {True, False}
        assert (
            client.post("/api/v1/simulator/fault", json={"mode": "normal"}).status_code
            == 409
        )
    # Exercise the actual frontend script with actual received WebSocket events.
    result = subprocess.run(
        ["node", str(ROOT / "tests/dashboard.cjs")],
        check=False,
        input=json.dumps(events),
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dashboard recovery verified" in result.stdout


@pytest.mark.parametrize("sensor", ["mpu", "bmp", "all"])
def test_partial_samples_recover_without_fabrication(compiled_frames, sensor):
    frames = compiled_frames(
        "--duration-ms",
        "3000",
        "--disconnect",
        sensor,
        "--disconnect-at-ms",
        "500",
        "--reconnect-at-ms",
        "1500",
    )

    async def scenario():
        hub = TelemetryHub(Settings(), Capture([raw(f) for f in frames]))
        await hub.run()
        assert hub.stats().frames_accepted == 30
        assert hub.stats().parse_errors == 0
        readings = await hub.store.readings("sim-hub", 100)
        assert readings[-1].i2c_errors > 0
        assert (await hub.store.health("sim-hub")).state == "online"
        assert all(r.battery_v is None and r.watchdog_resets is None for r in readings)
        assert readings[-1].firmware.imu.gyro_dps is not None
        assert readings[-1].firmware.baro.altitude_m is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("from_boot", [False, True])
def test_telemetry_liveness_does_not_hide_stalled_acquisition(
    compiled_frames, from_boot
):
    frames = compiled_frames(
        "--duration-ms",
        "6000",
        "--stall-task",
        "acquisition",
        "--stall-at-ms",
        "0" if from_boot else "1200",
    )

    async def scenario():
        hub = TelemetryHub(Settings(), Capture([raw(f) for f in frames]))
        await hub.run()
        assert hub.stats().frames_accepted == 60
        health = await hub.store.health("sim-hub")
        assert health.state == "degraded"
        assert health.measurement_state == ("unavailable" if from_boot else "stale")
        if not from_boot:
            assert health.sample_age_seconds >= 4.8
        alerts = await hub.store.alerts(100)
        assert any(a.code == "task_health" and a.active for a in alerts)
        assert any(a.code == "sample_stale" and a.active for a in alerts)
        # Silence later ages the device itself to offline.
        health = await hub.store.health(
            "sim-hub", now=health.last_seen + timedelta(seconds=11)
        )
        assert health.state == "offline"

    asyncio.run(scenario())


def test_reboot_delayed_frames_and_reconnect_framing(compiled_frames):
    old = compiled_frames("--duration-ms", "200", "--boot-id", "50")
    new = compiled_frames("--duration-ms", "200", "--boot-id", "51")
    chunks = [
        raw(old[0]),
        b"x" * 2050,
        SourceReset(),
        raw(new[0])[:17],
        raw(new[0])[17:],
        raw(old[1]),
        raw(new[0]),
        raw(new[1]),
    ]

    async def scenario():
        hub = TelemetryHub(Settings(), Capture(chunks))
        async with hub.subscribe() as queue:
            await hub.run()
            assert hub.stats().frames_accepted == 3
            assert hub.stats().session_changes == 1
            assert hub.stats().sequence_anomalies == 2
            assert hub.stats().parse_errors == 1
            assert queue.qsize() == 3
            assert (await hub.store.readings("sim-hub", 100))[-1].boot_id == 51

    asyncio.run(scenario())


def test_sample_and_frame_rollover_and_no_same_sample_mutation(compiled_frames):
    frames = compiled_frames("--duration-ms", "400")
    for i, f in enumerate(frames):
        f["frame_seq"] = f["seq"] = (2**32 - 2 + i) % 2**32

    async def scenario():
        store = TelemetryStore(Settings())
        for f in frames:
            assert await store.add_reading(parse_frame(raw(f)))
        mutated = copy.deepcopy(frames[-1])
        mutated["frame_seq"] += 1
        mutated["imu"]["accel_g"][0] += 1
        assert not await store.add_reading(parse_frame(raw(mutated)))
        assert not await store.add_reading(parse_frame(raw(frames[0])))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), 3.0),
        (("frame_seq",), True),
        (("boot_id",), -1),
        (("boot_id",), 2**64),
        (("frame_seq",), 2**32),
        (("imu", "accel_g"), [1, 2]),
        (("baro", "pressure_pa"), float("nan")),
        (("health", "i2c_errors"), "1"),
        (("health", "watchdog_enabled"), 1),
        (("type",), "unknown"),
        (("sample_present",), False),
        (("uptime_ms",), 0),
        (("device_id",), "invalid id"),
    ],
)
def test_shared_contract_rejects_invalid_frames(compiled_frames, path, value):
    frame = compiled_frames("--duration-ms", "100")[0]
    target = frame
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(FrameDecodeError):
        parse_frame(raw(frame))


def test_uint64_identity_roundtrip_and_schema_drift(compiled_frames):
    frame = compiled_frames("--duration-ms", "100", "--boot-id", str(2**64 - 1))[0]
    reading = parse_frame(raw(frame))
    assert reading.boot_id == 2**64 - 1
    assert reading.model_dump(mode="json")["boot_id"] == str(2**64 - 1)
    assert json.loads(encode_frame(reading)) == frame
    from sensor_monitor.shared_protocol import UnifiedFrame

    assert (
        json.loads((ROOT / "protocol/telemetry-v3.schema.json").read_text())
        == UnifiedFrame.model_json_schema()
    )


def test_missing_temperature_does_not_clear_active_alarm(compiled_frames):
    frames = compiled_frames("--duration-ms", "400")
    frames[0]["baro"]["temperature_c"] = 90
    frames[1]["baro"] = None
    frames[2]["baro"] = None

    async def scenario():
        hub = TelemetryHub(Settings(), Capture([raw(f) for f in frames]))
        await hub.run()
        alerts = [
            a for a in await hub.store.alerts(100) if a.code == "temperature_critical"
        ]
        assert [a.active for a in alerts] == [False, True]
        assert alerts[0].created_at >= alerts[1].created_at

    asyncio.run(scenario())


def test_source_repeats_are_new_boots_and_stop_reaps_child(binary):
    async def scenario():
        source = FirmwareSource(
            binary, ("--duration-ms", "100"), repeat=True, rate_hz=1000
        )
        stop = asyncio.Event()
        boots = []
        async for chunk in source.chunks(stop):
            if isinstance(chunk, bytes):
                boots.append(parse_frame(chunk).boot_id)
                if len(boots) == 3:
                    stop.set()
        assert boots == [1, 2, 3]
        assert source._process is None

    asyncio.run(scenario())


def test_source_pacing_survives_early_timer_wakeups(binary, monkeypatch):
    async def early_timeout(awaitable, timeout):
        # Model a Windows event-loop timeout firing before the high-resolution
        # deadline. Close the unawaited coroutine just as wait_for cancels it.
        awaitable.close()
        await asyncio.sleep(0)
        raise TimeoutError

    monkeypatch.setattr("sensor_monitor.firmware_source.asyncio.wait_for", early_timeout)

    async def scenario():
        source = FirmwareSource(binary, ("--duration-ms", "300"), rate_hz=20)
        times = []
        async for chunk in source.chunks(asyncio.Event()):
            if isinstance(chunk, bytes):
                times.append(time.perf_counter())
        assert len(times) == 3
        assert all(b - a >= 0.05 for a, b in pairwise(times))
        assert source._process is None

    asyncio.run(scenario())


def test_shutdown_reaps_a_child_with_a_full_stdout_pipe(binary):
    async def scenario():
        source = FirmwareSource(binary, ("--duration-ms", "3600000"))
        stream = source.chunks(asyncio.Event())
        assert isinstance(await anext(stream), SourceReset)
        assert isinstance(await anext(stream), bytes)
        process = source._process
        await asyncio.wait_for(source.close(), timeout=3)
        await stream.aclose()
        assert process.returncode is not None
        assert source._process is None

    asyncio.run(scenario())


def test_cancel_during_subprocess_start_does_not_leak_child(binary):
    async def scenario():
        source = FirmwareSource(binary, ("--duration-ms", "3600000"))
        hub = TelemetryHub(Settings(), source)
        task = asyncio.create_task(hub.run())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert source._process is None

    asyncio.run(scenario())


def test_counter_wrap_recovers_and_reboot_does_not_reuse_counter_baseline(
    compiled_frames,
):
    frames = compiled_frames("--duration-ms", "1400")
    frames[0]["health"]["i2c_errors"] = 2**32 - 1
    for f in frames[1:]:
        f["health"]["i2c_errors"] = 0

    async def scenario():
        hub = TelemetryHub(Settings(), Capture([raw(f) for f in frames]))
        await hub.run()
        alerts = [a for a in await hub.store.alerts(100) if a.code == "i2c_errors"]
        assert [a.active for a in alerts] == [False, True]
        assert (await hub.store.health("sim-hub")).state == "online"

    asyncio.run(scenario())


def test_duplicate_keys_unknown_fields_and_submillisecond_future_sample(
    compiled_frames,
):
    frame = compiled_frames("--duration-ms", "100")[0]
    data = raw(frame).replace(b'"boot_id": 1', b'"boot_id": 1, "boot_id": 2')
    with pytest.raises(FrameDecodeError):
        parse_frame(data)
    frame["unknown"] = "field"
    with pytest.raises(FrameDecodeError):
        parse_frame(raw(frame))
    del frame["unknown"]
    frame["ts_us"] += 1
    with pytest.raises(FrameDecodeError):
        parse_frame(raw(frame))
