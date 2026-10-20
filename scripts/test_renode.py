"""Real ARM ELF -> I2C peripherals -> UART socket -> REST/WebSocket acceptance tests."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from renode_demo import free_port
from renode_runtime import DEFAULT_ELF, DEFAULT_RENODE, ROOT, Renode

sys.path.insert(0, str(ROOT / "gateway/src"))

import httpx
import uvicorn
from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.protocol import parse_frame
from sensor_monitor.tcp_source import TcpSource
from websockets.asyncio.client import connect

FIRST_BOOT = 2**53 + 101


async def acceptance(args):
    directory = args.output.resolve()
    uart_port, web_port = free_port(), free_port()
    emulator = Renode(directory, uart_port, binary=args.renode, elf=args.elf,
                      previous_boot=FIRST_BOOT - 1)
    source = TcpSource("127.0.0.1", uart_port)
    app = create_app(Settings(port=web_port, log_dir=directory / "gateway-log"), source)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=web_port,
                                         access_log=False, log_level="warning"))
    events, cases = [], []
    server_task = receiver_task = None
    report = {"started_at": datetime.now(UTC).isoformat(), "cases": cases,
              "elf_sha256": hashlib.sha256(Path(args.elf).read_bytes()).hexdigest()}

    async def receiver():
        async with connect(f"ws://127.0.0.1:{web_port}/ws/telemetry") as ws:
            async for raw in ws:
                event = json.loads(raw)
                if event["type"] == "telemetry":
                    events.append(event)

    async def settle():
        # TCP writes and WebSocket delivery complete after virtual time stops.
        for _ in range(40):
            if events and len(events) >= app.state.hub.stats().frames_accepted:
                return
            await asyncio.sleep(0.05)
        raise AssertionError("WebSocket receiver did not catch up")

    def record(name, detail):
        cases.append({"name": name, "passed": True, "detail": detail})
        print(f"PASS {name}: {detail}", flush=True)

    try:
        await emulator.start()
        server_task = asyncio.create_task(server.serve())
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("HTTP server did not start")
            await asyncio.sleep(0.05)
        await asyncio.wait_for(source.connected.wait(), 5)
        receiver_task = asyncio.create_task(receiver())
        for _ in range(100):
            if app.state.hub.stats().websocket_clients:
                break
            await asyncio.sleep(0.02)
        assert app.state.hub.stats().websocket_clients == 1
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{web_port}") as http:
            await emulator.run_for(3)
            await settle()
            reading = events[-1]["reading"]
            assert reading["boot_id"] == str(FIRST_BOOT)
            assert reading["temperature_c"] == 25.08 and reading["pressure_hpa"] == 1006.53
            assert reading["accel_g"] == [0, 0, 1]
            assert reading["firmware"]["imu"]["gyro_dps"] == [0, 0, 0]
            altitude = reading["firmware"]["baro"]["altitude_m"]
            expected_altitude = 44330 * (1 - (100653 / 101325) ** 0.19029495)
            assert math.isclose(altitude, expected_altitude, abs_tol=0.02)
            assert reading["firmware"]["sample_present"]
            for field in ("acquisition_us", "processing_us"):
                assert reading["firmware"]["timing"][field] >= 0
            for field in ("i2c_errors", "i2c_retries", "queue_drops", "deadline_misses"):
                assert reading["firmware"]["health"][field] == 0
            assert reading["firmware"]["health"]["watchdog_enabled"]
            assert reading["firmware"]["health"]["watchdog"] == "fed"
            assert not any(a["code"] == "task_health" for event in events for a in event["alerts"])
            device_id = reading["device_id"]
            result = (await http.get(f"/api/v1/devices/{device_id}/readings")).json()
            assert result[-1] == reading
            assert (await http.get("/healthz")).json()["source"] == "renode"
            assert (await http.get("/")).status_code == 200
            record("healthy ARM firmware and lossless uint64", f"{len(events)} events; boot {FIRST_BOOT}; sensors calibrated")
            record("complete diagnostics", f"all 6 IMU axes, altitude {altitude} m, timing, counters and watchdog present")

            await emulator.command("sysbus.i2c1.mpu6050 AccelerationX 8192")
            await emulator.command("sysbus.i2c1.mpu6050 AccelerationY -4096")
            await emulator.command("sysbus.i2c1.mpu6050 AccelerationZ 12288")
            await emulator.command("sysbus.i2c1.mpu6050 GyroscopeX 655")
            await emulator.command("sysbus.i2c1.mpu6050 GyroscopeY -2620")
            await emulator.command("sysbus.i2c1.mpu6050 GyroscopeZ 1310")
            await emulator.command("sysbus.i2c1.bmp280 RawTemperature 530000")
            await emulator.run_for(2)
            await settle()
            reading = events[-1]["reading"]
            assert reading["accel_g"] == [0.5, -0.25, 0.75]
            assert reading["firmware"]["imu"]["gyro_dps"] == [5, -20, 10]
            assert reading["temperature_c"] > 25.08
            record("changed sensor registers", f"0.5 g, 10 deg/s, {reading['temperature_c']} C reached WebSocket")

            await emulator.command("sysbus.i2c1.bmp280 RawTemperature 519888")
            await emulator.command("sysbus.i2c1.bmp280 RawPressure 450000")
            await emulator.run_for(2)
            await settle()
            changed = events[-1]["reading"]
            expected_altitude = 44330 * (1 - (changed["pressure_hpa"] * 100 / 101325) ** 0.19029495)
            assert changed["pressure_hpa"] < 1006.53
            assert math.isclose(changed["firmware"]["baro"]["altitude_m"], expected_altitude, abs_tol=0.03)
            assert changed["firmware"]["baro"]["altitude_m"] > altitude + 100
            record("pressure and derived altitude", f"{changed['pressure_hpa']} hPa; {changed['firmware']['baro']['altitude_m']} m")

            for missing in (("mpu6050",), ("bmp280",), ("mpu6050", "bmp280")):
                for sensor in missing:
                    await emulator.detach(sensor)
                await emulator.run_for(3)
                await settle()
                event = events[-1]
                frame = event["reading"]["firmware"]
                assert event["health"]["state"] == "degraded"
                for sensor in missing:
                    assert frame["sensors"][sensor] == "offline"
                    assert frame["imu" if sensor == "mpu6050" else "baro"] is None
                for sensor in {"mpu6050", "bmp280"} - set(missing):
                    assert frame["sensors"][sensor] == "online"
                    assert frame["imu" if sensor == "mpu6050" else "baro"] is not None
                assert event["reading"]["boot_id"] == str(FIRST_BOOT)
                assert frame["health"]["i2c_errors"] > 0
                for sensor in missing:
                    await emulator.attach(sensor)
                await emulator.run_for(4)
                await settle()
                assert events[-1]["health"]["state"] == "online"
                assert all(v == "online" for v in events[-1]["reading"]["firmware"]["sensors"].values())
                record("I2C NACK and recovery: " + "+".join(missing), "offline/null during fault; online after reattachment")

            for n in range(1, 4):
                before = len(events)
                await emulator.command("machine Reset")
                await emulator.run_for(2)
                await settle()
                new = events[before:]
                assert new and {r["reading"]["boot_id"] for r in new} == {str(FIRST_BOOT + n)}
                assert new[0]["reading"]["firmware"]["frame_seq"] == 0
                assert all(r["reading"]["device_id"] == device_id for r in new)
            record("three MCU resets", "same UID, flash journal increments, frame sequence restarts at zero")

            reconnects = source.reconnects
            for _ in range(5):
                await emulator.command("hub_uart Disconnect")
                await asyncio.sleep(0.5)
                await emulator.run_for(1)
                await settle()
            assert source.reconnects >= reconnects + 5
            record("five UART socket reconnects", "stream resumes without resetting the boot identity")

            # A stopped acquisition timer stops task health, then the hardware IWDG resets the MCU.
            before_boot = events[-1]["reading"]["boot_id"]
            await emulator.command("sysbus.timer2 Enabled false")
            await emulator.run_for(7)
            await settle()
            assert int(events[-1]["reading"]["boot_id"]) > int(before_boot)
            assert events[-1]["reading"]["firmware"]["health"]["watchdog"] == "fed"
            record("hardware watchdog recovery", "stalled acquisition caused IWDG reset; firmware resumed")

            before_soak = len(events)
            await emulator.run_for(args.soak_seconds)
            await settle()
            assert len(events) - before_soak >= args.soak_seconds * 9
            # New WebSocket client must receive snapshot then fresh telemetry.
            async with connect(f"ws://127.0.0.1:{web_port}/ws/telemetry") as ws:
                assert json.loads(await ws.recv())["type"] == "snapshot"
                await emulator.run_for(1)
                assert json.loads(await asyncio.wait_for(ws.recv(), 3))["type"] == "telemetry"
            stats = (await http.get("/api/v1/stats")).json()
            for key in ("parse_errors", "sequence_anomalies", "device_rejections", "websocket_drops"):
                assert stats[key] == 0, (key, stats)
            record("steady run and WebSocket reconnect", f"{args.soak_seconds}s virtual time; no decoder/order errors")
            rendered = subprocess.run(
                ["node", str(ROOT / "tests/dashboard.cjs")], input=json.dumps(events),
                text=True, capture_output=True, timeout=30, check=True,
            )
            record("production dashboard rendering", rendered.stdout.strip())
            report["stats"] = stats
            report["last_event"] = events[-1]
            report["passed"] = True
    except BaseException as error:
        report["passed"] = False
        report["error"] = repr(error)
        raise
    finally:
        if receiver_task:
            receiver_task.cancel()
            with suppress(asyncio.CancelledError):
                await receiver_task
        server.should_exit = True
        await source.close()
        if server_task:
            await server_task
        await emulator.close()
        uart = directory / "uart.jsonl"
        if uart.exists():
            frames = [parse_frame(line) for line in uart.read_bytes().splitlines()]
            report["raw_frames_validated"] = len(frames)
            report["raw_boot_ids"] = sorted({str(frame.boot_id) for frame in frames})
        report["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renode", default=DEFAULT_RENODE)
    parser.add_argument("--elf", default=DEFAULT_ELF)
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory")
    parser.add_argument("--soak-seconds", type=int, default=30)
    asyncio.run(acceptance(parser.parse_args()))
