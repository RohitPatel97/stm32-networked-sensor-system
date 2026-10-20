"""Exercise SerialSource with repeated port failures and actual C v3 boot captures."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway/src"))

import serial
from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.sources import SerialSource


class PortFactory:
    def __init__(self, binary, boots, loop, stop):
        self.binary, self.boots, self.loop, self.stop = binary, boots, loop, stop
        self.opened = self.closed = self.attempts = self.open_failures = 0
        self.frames = []
        self.previous = None

    def __call__(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts % 8 == 0:
            self.open_failures += 1
            raise serial.SerialException("scripted failed open")
        boot = self.opened // 2 + 1
        assert boot <= self.boots
        second_port = self.opened % 2 == 1
        if not second_port:
            result = subprocess.run(
                [str(self.binary), "--duration-ms", "400", "--boot-id", str(boot)],
                capture_output=True, check=True, timeout=10,
            )
            self.frames = result.stdout.splitlines(keepends=True)
            assert len(self.frames) == 4
            chunks = [self.frames[0], self.frames[0], self.frames[1][:13]]
        else:
            chunks = ([self.previous] if self.previous else []) + [
                self.frames[1][:19], self.frames[1][19:], b"", self.frames[2], self.frames[3],
                b"x" * 2049 if boot % 2 == 0 else b'{"incomplete":',
            ]
            self.previous = self.frames[-1]
        final = second_port and boot == self.boots
        self.opened += 1
        owner = self

        class Port:
            def __init__(self):
                self.chunks = deque(chunks)
                self.closed = False

            def read_until(self, delimiter, size):
                assert delimiter == b"\n" and size == 2049
                if self.chunks:
                    return self.chunks.popleft()
                if final:
                    owner.loop.call_soon_threadsafe(owner.stop.set)
                    return b""
                raise serial.SerialException("scripted disconnect")

            def close(self):
                assert not self.closed, "port closed more than once"
                self.closed = True
                owner.closed += 1

        return Port()


async def run(binary, boots):
    source = SerialSource("scripted-uart", 115200, reconnect_delay=0)
    hub = TelemetryHub(Settings(history_size=120, subscriber_queue_size=64), source)
    factory = PortFactory(binary, boots, asyncio.get_running_loop(), hub._stop)
    consumed = errors = 0

    async def consume(queue):
        nonlocal consumed, errors
        while True:
            event = await queue.get()
            reading = event["reading"]
            index = (int(reading["boot_id"]) - 1) * 4 + reading["sequence"]
            errors += int(index != consumed)
            consumed += 1
            queue.task_done()

    start = time.perf_counter()
    with patch.object(serial, "Serial", factory):
        async with hub.subscribe() as queue:
            consumer = asyncio.create_task(consume(queue))
            try:
                await asyncio.wait_for(hub.run(), timeout=max(60, boots / 2))
                await asyncio.wait_for(queue.join(), timeout=5)
            finally:
                await hub.stop()
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
    stats = hub.stats().model_dump(mode="json")
    checks = {
        "all_ports_closed_once": factory.closed == factory.opened == 2 * boots,
        "all_frames_accepted": stats["frames_accepted"] == 4 * boots,
        "all_frames_delivered_in_order": consumed == 4 * boots and errors == 0,
        "only_expected_rejections": stats["sequence_anomalies"] == 2 * boots - 1,
        "oversize_errors_accounted": stats["parse_errors"] == boots // 2,
        "boot_changes_accounted": stats["session_changes"] == boots - 1,
        "disconnect_and_failed_open_retries_accounted": source.reconnects == 2 * boots - 1 + factory.open_failures,
        "no_subscriber_drops": stats["websocket_drops"] == 0,
        "no_device_rejections": stats["device_rejections"] == 0,
        "source_released": source._serial is None and source._pending is None,
        "subscribers_released": stats["websocket_clients"] == 0,
    }
    return {"passed": all(checks.values()), "checks": checks, "stats": stats,
            "boots": boots, "ports_opened": factory.opened, "ports_closed": factory.closed,
            "injected_open_failures": factory.open_failures, "frames_consumed": consumed,
            "elapsed_seconds": time.perf_counter() - start,
            "scope": "real SerialSource open/read/close worker lifecycle; scripted ports, fresh C subprocess for each boot; no physical USB/UART"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boots", type=int, default=500)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/host/sensor_hub_sim.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/validation/reconnect-stress-20261017.json")
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("boots must be positive")
    logging.getLogger("sensor_monitor.sources").setLevel(logging.ERROR)
    result = asyncio.run(run(args.binary.resolve(), args.boots))
    result["binary_sha256"] = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    result["source_sha256"] = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [Path(__file__), *sorted((ROOT / "gateway/src/sensor_monitor").glob("*.py"))]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
