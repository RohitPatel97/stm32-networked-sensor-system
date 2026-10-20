"""Run compiled firmware -> shared UART decoder -> API -> browser dashboard."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway" / "src"))

from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.firmware_source import FirmwareSource

SCENARIOS = {
    "normal": ("--duration-ms", "10000"),
    "recovery": (
        "--duration-ms",
        "12000",
        "--disconnect",
        "mpu",
        "--disconnect-at-ms",
        "3000",
        "--reconnect-at-ms",
        "7000",
    ),
    "barometer": (
        "--duration-ms",
        "12000",
        "--disconnect",
        "bmp",
        "--disconnect-at-ms",
        "3000",
        "--reconnect-at-ms",
        "7000",
    ),
    "stalled": (
        "--duration-ms",
        "12000",
        "--stall-task",
        "acquisition",
        "--stall-at-ms",
        "3000",
    ),
}


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--binary",
        type=Path,
        default=ROOT
        / "build"
        / "host"
        / ("sensor_hub_sim.exe" if sys.platform == "win32" else "sensor_hub_sim"),
    )
    parser.add_argument("--scenario", choices=SCENARIOS, default="recovery")
    parser.add_argument("--boot-id", type=int, default=1)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    source = FirmwareSource(
        args.binary,
        SCENARIOS[args.scenario],
        boot_id=args.boot_id,
        repeat=not args.once,
    )
    print(
        f"Compiled C scenario: {args.scenario}; dashboard http://127.0.0.1:{args.port}"
    )
    print(
        "Each repeated scenario executes a new simulator boot with an increasing boot ID."
    )
    settings = Settings(port=args.port, log_dir=ROOT / "var" / "log")
    uvicorn.run(
        create_app(settings, source),
        host="127.0.0.1",
        port=args.port,
        loop="asyncio",
        access_log=False,
    )


if __name__ == "__main__":
    main()
