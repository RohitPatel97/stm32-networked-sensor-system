"""Loopback checks of the installed wheel using the freshly compiled simulator."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    import uvicorn
    from sensor_monitor.app import create_app
    from sensor_monitor.config import Settings
    from sensor_monitor.firmware_source import FirmwareSource

    source = FirmwareSource(
        ROOT / "build/host/sensor_hub_sim.exe",
        ("--duration-ms", "3000", "--disconnect", "bmp", "--disconnect-at-ms", "500",
         "--reconnect-at-ms", "1500"),
        repeat=True,
    )
    app = create_app(Settings(port=port, log_dir=ROOT / "var/log"), source)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, loop="asyncio")


def check():
    import httpx
    import sensor_monitor
    from websockets.sync.client import connect

    assert "site-packages" in Path(sensor_monitor.__file__).parts
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("SENSOR_MONITOR_") and k not in ("PYTHONPATH", "PYTHONHOME")}
    report = {"package_path": sensor_monitor.__file__, "port": port, "passed": False}
    events = []
    with (ROOT / "evidence/server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), "serve", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=log,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                deadline = time.monotonic() + 15
                while True:
                    assert process.poll() is None, "server failed; inspect server.log"
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    assert time.monotonic() < deadline, "server startup timed out"
                    time.sleep(0.02)
                assert "Firmware diagnostics" in client.get("/").text
                assert "function renderReading" in client.get("/static/app.js").text
                assert client.get("/static/styles.css").status_code == 200
                assert client.get("/api/v1/devices/missing").status_code == 404
                assert client.get("/api/v1/devices/sim-hub/readings?limit=0").status_code == 422
                assert client.post("/api/v1/simulator/fault", json={"mode": "normal"}).status_code == 409
                outage = False
                with connect(f"ws://127.0.0.1:{port}/ws/telemetry", open_timeout=3) as ws:
                    assert json.loads(ws.recv(timeout=3))["type"] == "snapshot"
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        event = json.loads(ws.recv(timeout=3))
                        if event["type"] != "telemetry":
                            continue
                        events.append(event)
                        assert event["reading"]["schema_version"] == 3
                        outage |= event["reading"]["pressure_hpa"] is None
                        if outage and event["health"]["state"] == "online":
                            break
                    assert outage and events[-1]["health"]["state"] == "online"
                report["stats"] = client.get("/api/v1/stats").json()
                report["health"] = client.get("/healthz").json()
                for key in ("parse_errors", "sequence_anomalies", "device_rejections", "websocket_drops"):
                    assert report["stats"][key] == 0
                report["events_received"] = len(events)
                renderer = subprocess.run(
                    ["node", str(ROOT / "tests/dashboard.cjs")], input=json.dumps(events),
                    capture_output=True, text=True, timeout=10,
                )
                report["dashboard"] = {"returncode": renderer.returncode,
                                       "stdout": renderer.stdout, "stderr": renderer.stderr}
                assert renderer.returncode == 0, renderer.stderr
                report["passed"] = True
        finally:
            process.terminate()
            process.wait(timeout=5)
            # Terminating a Windows parent doesn't terminate descendants. The C
            # simulator exits after its short capture; no serial source is opened.
            report["server_returncode_after_cleanup"] = process.returncode
            (ROOT / "evidence/live-runtime.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(int(sys.argv[2]))
    else:
        check()
