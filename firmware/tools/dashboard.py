"""Live terminal dashboard for sensor-hub NDJSON over serial or a file."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

if __package__:
    from .reference_sim import generate_frames
    from .telemetry import TelemetryError, TelemetryFrame, decode_line
else:
    from reference_sim import generate_frames
    from telemetry import TelemetryError, TelemetryFrame, decode_line

# Deliberately ASCII-only: many Windows serial-console sessions still expose a
# legacy code page even when Python source and incoming telemetry are UTF-8.
SPARKS = "._-~=+#@"


def sparkline(values: Iterable[float]) -> str:
    points = list(values)
    if not points:
        return "-"
    low, high = min(points), max(points)
    if high == low:
        return SPARKS[3] * len(points)
    # Normalize before subtraction/multiplication so finite values near the
    # float limit cannot overflow the range calculation.
    scale = max(abs(low), abs(high))
    points = [value / scale for value in points]
    low, high = min(points), max(points)
    return "".join(SPARKS[min(7, int((value - low) * 7 / (high - low)))] for value in points)


def render(frame: TelemetryFrame, pressure_history: Iterable[float] = ()) -> str:
    imu = frame.imu or {}
    baro = frame.baro or {}
    accel = imu.get("accel_g")
    gyro = imu.get("gyro_dps")
    def vector(values: Any) -> str:
        return "  n/a" if not isinstance(values, list) else "  ".join(f"{value:8.3f}" for value in values)
    pressure = baro.get("pressure_pa")
    watchdog = "FED" if frame.watchdog_fed else "WITHHELD"
    return "\n".join(
        (
            f"FreeRTOS Sensor Hub - telemetry v{frame.raw.get('schema_version', frame.raw.get('schema'))}",
            f"seq {frame.sequence:8d}   uptime {frame.timestamp_us / 1_000_000:9.3f} s",
            f"MPU6050 {'ONLINE ' if frame.mpu6050_online else 'OFFLINE'}   BMP280 {'ONLINE ' if frame.bmp280_online else 'OFFLINE'}",
            f"accel g     {vector(accel)}",
            f"gyro dps    {vector(gyro)}",
            f"pressure    {pressure:10.1f} Pa" if isinstance(pressure, (int, float)) else "pressure           n/a",
            f"trend       {sparkline(pressure_history)}",
            f"I2C retries {frame.i2c_retries:6d}   errors {frame.i2c_errors:6d}   queue drops {frame.queue_drops:6d}",
            f"acquire {frame.acquisition_us:5d} us   process {frame.processing_us:5d} us   watchdog {watchdog}",
        )
    )


def serial_lines(port: str, baud: int) -> Iterator[bytes]:
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SystemExit("Serial mode requires: python -m pip install pyserial") from exc
    with serial.Serial(port, baudrate=baud, timeout=1.0) as connection:
        while True:
            line = connection.readline()
            if line:
                yield line


def demo_lines() -> Iterator[str]:
    while True:
        for payload in generate_frames(5000, disconnect="bmp", disconnect_at_ms=3500):
            yield json.dumps(payload)
            time.sleep(0.1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--port", help="serial device, for example COM5 or /dev/ttyACM0")
    source.add_argument("--file", type=Path, help="replay an NDJSON capture; use - for stdin")
    source.add_argument("--demo", action="store_true", help="run the dependency-free reference generator")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--no-ansi", action="store_true", help="print snapshots instead of redrawing")
    parser.add_argument("--max-frames", type=int, default=0, help="stop after N valid frames (0 is unlimited)")
    args = parser.parse_args()

    stream: Iterable[str | bytes]
    opened = None
    if args.port:
        stream = serial_lines(args.port, args.baud)
    elif args.file:
        if args.file == Path("-"):
            stream = sys.stdin.buffer
        else:
            # Decode each line inside decode_line so bad UTF-8 is handled just
            # like malformed JSON, without losing subsequent valid frames.
            opened = args.file.open("rb")
            stream = opened
    else:
        stream = demo_lines()
    history: deque[float] = deque(maxlen=48)
    count = invalid = 0
    try:
        for line in stream:
            try:
                frame = decode_line(line)
            except TelemetryError as exc:
                invalid += 1
                print(f"ignored malformed frame #{invalid}: {exc}", file=sys.stderr)
                continue
            if frame.baro and isinstance(frame.baro.get("pressure_pa"), (int, float)):
                history.append(float(frame.baro["pressure_pa"]))
            if not args.no_ansi:
                print("\x1b[2J\x1b[H", end="")
            print(render(frame, history), flush=True)
            count += 1
            if args.max_frames and count >= args.max_frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if opened is not None:
            opened.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
