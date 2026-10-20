"""Command line entry points for serving, simulating, and validating telemetry."""

from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

import uvicorn

from .bridge import BridgeError, convert_freertos_frame
from .capture import iter_capture_records
from .config import Settings
from .models import WireTelemetry
from .protocol import NewlineJsonDecoder, ProtocolError, encode_frame


def _boot_counter(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 <= int(value) < 2**64:
        raise argparse.ArgumentTypeError("boot-id must be an unsigned 64-bit decimal integer")
    return int(value)


def _device_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value) is None:
        raise argparse.ArgumentTypeError(
            "device-id must contain 1-64 letters, digits, '.', '_' or '-'"
        )
    return value


def _positive_count(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 2**32:
        raise argparse.ArgumentTypeError("count must be between 1 and 4294967296")
    return count


def _interval(value: str) -> float:
    interval = float(value)
    if not math.isfinite(interval) or not 0 <= interval <= 60:
        raise argparse.ArgumentTypeError("interval must be finite and between 0 and 60 seconds")
    return interval


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sensor-monitor")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="run the API and dashboard")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")

    simulate = subcommands.add_parser(
        "simulate", help="write firmware-shaped JSONL telemetry to stdout"
    )
    simulate.add_argument("--count", type=_positive_count, default=10)
    simulate.add_argument("--interval", type=_interval, default=0.1)
    simulate.add_argument(
        "--boot-id", type=_boot_counter, help="emit protocol v2 with this boot counter"
    )

    validate = subcommands.add_parser(
        "validate", help="validate a JSONL capture against the UART schema"
    )
    validate.add_argument("path", type=Path)

    convert = subcommands.add_parser(
        "convert-freertos", help="convert complete FreeRTOS schema-1 records to gateway v2 JSONL"
    )
    convert.add_argument("path", type=Path)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--device-id", type=_device_id, required=True)
    convert.add_argument("--boot-id", type=_boot_counter, required=True)
    return parser


def _simulate(count: int, interval: float, boot_id: int | None = None) -> int:
    for sequence in range(count):
        reading = WireTelemetry(
            schema_version=1 if boot_id is None else 2,
            boot_id=boot_id,
            device_id="stm32-sensor-hub",
            sequence=sequence,
            uptime_ms=sequence * int(interval * 1_000),
            temperature_c=24.5,
            pressure_hpa=1013.25,
            accel_g=(0.0, 0.0, 1.0),
            battery_v=12.4,
        )
        print(encode_frame(reading).decode().rstrip("\n"), flush=True)
        if sequence + 1 < count:
            time.sleep(interval)
    return 0


def _validate(path: Path) -> int:
    accepted = 0
    rejected = 0
    decoder = NewlineJsonDecoder()
    with path.open("rb") as stream:
        for line_number, raw in iter_capture_records(stream):
            for event in decoder.feed_events(raw + b"\n"):
                if isinstance(event, ProtocolError):
                    rejected += 1
                    print(f"line {line_number}: {event}", file=sys.stderr)
                else:
                    accepted += 1
    print(f"accepted={accepted} rejected={rejected}")
    return 1 if rejected else 0


def _convert(path: Path, output: Path, *, device_id: str, boot_id: int) -> int:
    if path.resolve() == output.resolve() or (output.exists() and path.samefile(output)):
        print("input and output must be different files", file=sys.stderr)
        return 2
    accepted = rejected = 0
    with path.open("rb") as stream, output.open("wb") as converted:
        for line_number, raw in iter_capture_records(stream):
            try:
                frame = convert_freertos_frame(raw, device_id=device_id, boot_id=boot_id)
            except BridgeError as exc:
                rejected += 1
                print(f"line {line_number}: {exc}", file=sys.stderr)
            else:
                converted.write(encode_frame(frame))
                accepted += 1
    print(f"converted={accepted} rejected={rejected}")
    return 1 if rejected else 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "simulate":
        return _simulate(args.count, args.interval, args.boot_id)
    try:
        if args.command == "validate":
            return _validate(args.path)
        if args.command == "convert-freertos":
            return _convert(args.path, args.output, device_id=args.device_id, boot_id=args.boot_id)
    except OSError as exc:
        print(f"capture I/O error: {exc}", file=sys.stderr)
        return 2

    settings = Settings.from_env()
    uvicorn.run(
        "sensor_monitor.app:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        reload=args.reload,
    )
    return 0
