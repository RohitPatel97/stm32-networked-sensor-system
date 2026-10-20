"""Offline interview demo: real ARM firmware in Renode, preflight, and dashboard controls."""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import socket
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import httpx
import uvicorn
from fastapi import APIRouter, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from hub_lab import WEB, Lab
from hub_lab import router as lab_router
from pydantic import BaseModel, ConfigDict
from renode_demo import free_port
from renode_runtime import DEFAULT_ELF, DEFAULT_RENODE, ROOT, Renode
from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.tcp_source import TcpSource
from starlette.middleware.trustedhost import TrustedHostMiddleware

DEMO_ID = "stm32-renode-lab-v2"
ACTIONS = {
    "rest": "Stationary / restore sensors",
    "motion": "Tilt and rotate",
    "altitude": "Change pressure / altitude",
    "imu_off": "Disconnect MPU6050",
    "baro_off": "Disconnect BMP280",
    "both_off": "Disconnect both sensors",
    "reboot": "Reboot MCU",
    "uart": "Reconnect UART",
    "watchdog": "Stall acquisition / watchdog reset",
}
router = APIRouter()


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str


class Demo:
    def __init__(self, emulator, app, source):
        self.emulator, self.app, self.source = emulator, app, source
        self.token = secrets.token_urlsafe(32)
        self.lock = asyncio.Lock()
        self.attached = {"mpu6050": True, "bmp280": True}
        self.ready = False
        self.message = "Starting ARM firmware and checking telemetry..."

    async def latest(self):
        store = self.app.state.hub.store
        devices = await store.all_health()
        if not devices:
            return None, None
        health = devices[0]
        readings = await store.readings(health.device_id, 1)
        return health, readings[-1] if readings else None

    async def wait_for(self, predicate, timeout=20):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.emulator.process.returncode is not None:
                raise RuntimeError("Renode stopped; see the run log")
            health, reading = await self.latest()
            if reading and predicate(health, reading):
                return health, reading
            await asyncio.sleep(0.1)
        raise TimeoutError("Expected telemetry did not arrive; use Restore or inspect the run log")

    async def restore(self):
        for sensor, connected in self.attached.items():
            if not connected:
                await self.emulator.attach(sensor)
                self.attached[sensor] = True
        # Allow firmware initialization (which resets each sensor) before setting registers.
        await self.wait_for(lambda h, r: h.mpu6050 == h.bmp280 == "online")
        for sensor, fields in {
            "mpu6050": {"AccelerationX": 0, "AccelerationY": 0, "AccelerationZ": 16384,
                        "GyroscopeX": 0, "GyroscopeY": 0, "GyroscopeZ": 0, "Temperature": 0},
            "bmp280": {"RawTemperature": 519888, "RawPressure": 415148},
        }.items():
            await self.emulator.registers([f"sysbus.i2c1.{sensor} {field} {value}" for field, value in fields.items()])

    async def perform(self, action):
        if action not in ACTIONS:
            raise ValueError("unknown demo action")
        if self.lock.locked():
            raise HTTPException(409, "Wait for the current demonstration to finish")
        async with self.lock:
            _, before = await self.latest()
            if before is None:
                raise HTTPException(503, "Telemetry is not ready")
            baseline = (before.boot_id, before.sequence)
            previous_reconnects = self.source.reconnects
            self.message = ACTIONS[action] + "..."
            if action in ("rest", "motion", "altitude", "reboot", "watchdog"):
                await self.restore()
            if action == "motion":
                values = {"AccelerationX": 8192, "AccelerationY": -4096,
                                     "AccelerationZ": 12288, "GyroscopeX": 655,
                          "GyroscopeY": -2620, "GyroscopeZ": 1310}
                await self.emulator.registers([f"sysbus.i2c1.mpu6050 {field} {value}" for field, value in values.items()])
            elif action == "altitude":
                await self.emulator.command("sysbus.i2c1.bmp280 RawPressure 450000")
            elif action in ("imu_off", "baro_off", "both_off"):
                missing = {"imu_off": ["mpu6050"], "baro_off": ["bmp280"],
                           "both_off": ["mpu6050", "bmp280"]}[action]
                for sensor in missing:
                    if self.attached[sensor]:
                        await self.emulator.detach(sensor)
                        self.attached[sensor] = False
            elif action == "reboot":
                await self.emulator.command("machine Reset")
            elif action == "uart":
                await self.emulator.command("hub_uart Disconnect")
            elif action == "watchdog":
                await self.emulator.command("sysbus.timer2 Enabled false")

            def observed(health, reading):
                if (reading.boot_id, reading.sequence) == baseline or health.age_seconds > 1:
                    return False
                if action in ("reboot", "watchdog"):
                    return (reading.boot_id > before.boot_id and health.state == "online"
                            and reading.firmware.health.watchdog == "fed")
                if action == "motion":
                    return (reading.accel_g == (0.5, -0.25, 0.75)
                            and reading.firmware.imu.gyro_dps == (5, -20, 10))
                if action == "altitude":
                    return reading.firmware.baro is not None and reading.firmware.baro.altitude_m > 100
                if action in ("imu_off", "baro_off", "both_off"):
                    return all(getattr(health, s) == "offline" for s in missing)
                if action == "uart":
                    return self.source.reconnects > previous_reconnects and health.state == "online"
                return (health.state == "online" and reading.accel_g == (0, 0, 1)
                        and reading.firmware.imu.gyro_dps == (0, 0, 0)
                        and reading.firmware.imu.temperature_c == 36.53
                        and reading.temperature_c == 25.08
                        and reading.pressure_hpa == 1006.53)

            health, reading = await self.wait_for(observed)
            self.message = f"Verified: {ACTIONS[action]}. Board {health.state}; boot {reading.boot_id}."
            return {"message": self.message, "health": health.model_dump(mode="json"),
                    "reading": reading.model_dump(mode="json")}


@router.get("/api/v1/demo")
async def status(request: Request):
    demo = request.app.state.demo
    return {"demo_id": DEMO_ID, "project": str(ROOT), "ready": demo.ready,
            "message": demo.message, "token": demo.token, "actions": ACTIONS}


@router.post("/api/v1/demo/action")
async def action(body: Action, request: Request):
    demo = request.app.state.demo
    if not secrets.compare_digest(request.headers.get("x-demo-token", ""), demo.token):
        raise HTTPException(403, "Open this demo's dashboard before using controls")
    if body.action not in ACTIONS:
        raise HTTPException(422, "Unknown demo action")
    if not demo.ready:
        raise HTTPException(503, "Preflight is still running")
    try:
        return await request.app.state.lab.manual(body.action)
    except (TimeoutError, RuntimeError) as error:
        demo.message = str(error)
        raise HTTPException(503, str(error)) from error


def make_app(emulator, source, directory, port):
    app = create_app(Settings(port=port, log_dir=directory / "gateway-log"), source)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.demo = Demo(emulator, app, source)
    app.state.lab = Lab(app.state.demo, directory, port)
    app.mount("/lab-assets", StaticFiles(directory=WEB), name="lab-assets")
    app.include_router(router)
    app.include_router(lab_router)
    return app


async def run(args):
    url = f"http://127.0.0.1:{args.port}"
    with socket.socket() as probe:
        occupied = probe.connect_ex(("127.0.0.1", args.port)) == 0
    if occupied:
        if getattr(args, "desktop_control", False):
            raise RuntimeError(f"Port {args.port} was taken during startup. Reopen Sensor Lab.")
        async with httpx.AsyncClient(timeout=2) as client:
            try:
                existing = (await client.get(url + "/api/v1/demo")).json()
            except (httpx.HTTPError, ValueError):
                existing = {}
        if existing.get("demo_id") == DEMO_ID and existing.get("project") == str(ROOT):
            async with httpx.AsyncClient(timeout=2) as client:
                health = (await client.get(url + "/api/v1/devices")).json()
            if args.check_only and (not existing.get("ready") or not health
                                    or health[0]["state"] != "online"):
                raise RuntimeError("Existing demo is not healthy. Restore sensors before preflight.")
            print(f"Demo already running: {url}", flush=True)
            if not args.no_browser:
                webbrowser.open(url + "/lab")
            if args.run_tests or args.test_and_exit:
                async with httpx.AsyncClient(timeout=25) as client:
                    lab = (await client.get(url + "/api/v1/lab")).json()
                    response = await client.post(url + "/api/v1/lab/run",
                                                 headers={"x-demo-token": existing["token"]},
                                                 json={"cases": [c["id"] for c in lab["catalog"]]})
                    response.raise_for_status()
                    run_id = response.json()["run_id"]
                    print(f"Started suite: {run_id}", flush=True)
                    if args.test_and_exit:
                        deadline = asyncio.get_running_loop().time() + 600
                        while asyncio.get_running_loop().time() < deadline:
                            result = (await client.get(url + f"/api/v1/lab/reports/{run_id}")).json()
                            if result["status"] != "running":
                                print(f"REPORT: {ROOT / 'var/lab-reports' / (run_id + '.json')}", flush=True)
                                if result["status"] != "passed":
                                    raise RuntimeError(f"Test suite {result['status']}; inspect its report")
                                break
                            await asyncio.sleep(1)
                        else:
                            raise TimeoutError("Suite is still running; inspect the existing console")
            return
        raise RuntimeError(f"Port {args.port} is used by another service. Close that service or use --port 8766.")

    directory = ROOT / "var/interview" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    emulator = Renode(directory, free_port(), binary=args.renode, elf=args.elf, capture=False)
    source = TcpSource("127.0.0.1", emulator.uart_port)
    app = make_app(emulator, source, directory, args.port)
    demo = app.state.demo
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                         loop="asyncio", access_log=False, log_level="warning"))
    task = None
    try:
        print("Starting Renode / STM32F401RE / FreeRTOS...", flush=True)
        await emulator.start()
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task
                raise RuntimeError("Dashboard did not start")
            await asyncio.sleep(0.05)
        await asyncio.wait_for(source.connected.wait(), 5)
        await emulator.command("start")
        health, reading = await demo.wait_for(
            lambda h, r: h.state == "online" and r.firmware.health.watchdog == "fed"
            and r.sequence >= 5 and r.accel_g == (0, 0, 1)
            and r.firmware.imu is not None and r.firmware.baro is not None
            and r.firmware.imu.gyro_dps == (0, 0, 0)
            and 55 < r.firmware.baro.altitude_m < 57)
        app.state.lab.start(["baseline"], kind="preflight")
        await app.state.lab.task
        if app.state.lab.current["status"] != "passed":
            raise RuntimeError("Preflight failed; open the saved report in var/lab-reports")
        demo.ready = True
        demo.message = "Preflight passed. All sensor readings, boot identity and watchdog health are live."
        (directory / "preflight.json").write_text(json.dumps({
            "passed": True, "health": health.model_dump(mode="json"),
            "reading": reading.model_dump(mode="json"),
        }, indent=2), encoding="utf-8")
        print(f"READY: {url}/lab\n{demo.message}\nLogs: {directory}\nReports: {app.state.lab.reports.directory}", flush=True)
        if args.check_only:
            return
        print("Close Sensor Lab to stop." if getattr(args, "desktop_control", False)
              else "Keep this window open. Press Ctrl+C to stop.", flush=True)
        if not args.no_browser:
            webbrowser.open(url + "/lab")
        if args.run_tests or args.test_and_exit:
            from hub_lab import BY_ID
            app.state.lab.start(list(BY_ID))
            if args.test_and_exit:
                await app.state.lab.task
                print(f"REPORT: {app.state.lab.reports.path(app.state.lab.current['id'])}", flush=True)
                if app.state.lab.current["status"] != "passed":
                    raise RuntimeError("One or more hub tests failed; inspect the report")
                return
        # Parent cancellation must reach this function's cleanup without cancelling
        # uvicorn in the middle of its application lifespan.
        await asyncio.shield(task)
    finally:
        demo.ready = False
        await app.state.lab.close()
        server.should_exit = True
        await source.close()
        if task and not task.done():
            await task
        await emulator.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--renode", type=Path, default=DEFAULT_RENODE)
    parser.add_argument("--elf", type=Path, default=DEFAULT_ELF)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check-only", action="store_true", help="boot, validate telemetry, then stop")
    parser.add_argument("--run-tests", action="store_true", help="start all documented Renode tests after launch")
    parser.add_argument("--test-and-exit", action="store_true", help="run all Renode tests, write reports and exit")
    parser.add_argument("--desktop-control", action="store_true", help=argparse.SUPPRESS)
    try:
        args = parser.parse_args()
        if args.desktop_control:
            from desktop_control import owned_run
            asyncio.run(owned_run(lambda: run(args), sys.stdin))
        else:
            asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Demo stopped.")
    except Exception as error:  # noqa: BLE001 - desktop launcher must show errors and return nonzero
        print(f"DEMO STARTUP FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
