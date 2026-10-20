#!/usr/bin/env python3
"""Timed host-only stress of the real telemetry hub; no server or hardware required."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.models import WireTelemetry
from sensor_monitor.protocol import encode_frame
from sensor_monitor.sources import SourceReset, TelemetrySource


@dataclass(frozen=True)
class SoakConfig:
    duration_seconds: float = 600
    rate_hz: float = 100
    devices: int = 4
    history_size: int = 120
    subscriber_queue_size: int = 8
    warmup_seconds: float = 30
    memory_sample_seconds: float = 0.5
    reconnect_every: int = 1000
    samples_per_boot: int = 2500
    max_p95_latency_ms: float = 50
    max_memory_growth_bytes: int = 5 * 1024 * 1024
    max_duration_overrun_fraction: float = 0.05

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (
            self.duration_seconds, self.rate_hz, self.memory_sample_seconds,
        )):
            raise ValueError("duration, rate, and sampling interval must be positive and finite")
        if not 0 <= self.warmup_seconds < self.duration_seconds:
            raise ValueError("warmup must be nonnegative and shorter than the run")
        if min(self.devices, self.history_size, self.subscriber_queue_size,
               self.reconnect_every, self.samples_per_boot, self.target_frames) < 1:
            raise ValueError("device, queue, history, interval, and frame counts must be positive")

    @property
    def target_frames(self) -> int:
        return int(self.duration_seconds * self.rate_hz)


class LatencyHistogram:
    """Fixed 1 ms bins through 1000 ms, plus a separate overflow count."""

    def __init__(self) -> None:
        self.bins = [0] * 1001
        self.overflow = 0
        self.count = 0
        self.maximum_ms = 0.0

    def add(self, milliseconds: float) -> None:
        self.count += 1
        self.maximum_ms = max(self.maximum_ms, milliseconds)
        bucket = math.ceil(milliseconds)
        if bucket < len(self.bins):
            self.bins[bucket] += 1
        else:
            self.overflow += 1

    def p95_upper_bound(self) -> int | None:
        if not self.count:
            return None
        remaining = math.ceil(self.count * 0.95)
        for boundary, count in enumerate(self.bins):
            remaining -= count
            if remaining <= 0:
                return boundary
        return None  # percentile lies above the tracked 1000 ms range


class SoakSource(TelemetrySource):
    name = "scripted-host-soak"

    def __init__(self, config: SoakConfig) -> None:
        self.config = config
        self.generated = 0
        self.reconnects = 0
        self.timestamps: list[tuple[int, float] | None] = [None] * max(
            16, config.subscriber_queue_size * 2, config.devices * 2,
        )

    async def chunks(self, stop: asyncio.Event):
        started = time.perf_counter()
        for index in range(self.config.target_frames):
            await sleep_until(started + index / self.config.rate_hz)
            await asyncio.sleep(0)  # Let subscribers run even when catching up to a deadline.
            if stop.is_set():
                return
            if index and index % self.config.reconnect_every == 0:
                yield b'{"schema_version":2,"device_id":"interrupted'
                self.reconnects += 1
                yield SourceReset()
            device = index % self.config.devices
            sample = index // self.config.devices
            boot, sequence = divmod(sample, self.config.samples_per_boot)
            frame = encode_frame(WireTelemetry(
                schema_version=2,
                boot_id=boot + 1,
                device_id=f"soak-{device}",
                sequence=sequence,
                uptime_ms=int(sequence * 1000 * self.config.devices / self.config.rate_hz),
                temperature_c=24.5,
                pressure_hpa=1013.25,
                accel_g=(0.0, 0.0, 1.0),
                battery_v=12.4,
            ))
            self.timestamps[index % len(self.timestamps)] = (index, time.perf_counter())
            self.generated += 1
            yield frame
        await sleep_until(started + self.config.duration_seconds)

    def index_for(self, event: dict) -> int:
        reading = event["reading"]
        device = int(reading["device_id"].removeprefix("soak-"))
        sample = (reading["boot_id"] - 1) * self.config.samples_per_boot + reading["sequence"]
        return sample * self.config.devices + device


async def sleep_until(deadline: float) -> None:
    # Event-loop timer resolution can wake a sleep early on Windows. Check the
    # high-resolution clock again so elapsed duration and pacing stay honest.
    while (remaining := deadline - time.perf_counter()) > 0:
        await asyncio.sleep(remaining)


def acceptance_checks(metrics: dict, config: SoakConfig) -> dict[str, bool]:
    n = metrics["generated"]
    expected_sessions = sum(
        max(0, ((n - 1 - device) // config.devices) // config.samples_per_boot)
        for device in range(config.devices)
    )
    p95 = metrics["fast_latency_p95_upper_bound_ms"]
    growth = metrics["python_memory_growth_bytes"]
    return {
        "generated_target": n == config.target_frames,
        "received_matches_generated": metrics["frames_received"] == n,
        "accepted_matches_generated": metrics["frames_accepted"] == n,
        "no_unexpected_rejections": all(metrics[key] == 0 for key in (
            "parse_errors", "sequence_anomalies", "device_rejections",
        )),
        "fast_consumer_complete": metrics["fast_consumed"] == n,
        "fast_consumer_ordered": metrics["fast_order_errors"] == 0,
        "all_fast_latencies_recorded": metrics["fast_latency_samples"] == n,
        "fast_p95_within_limit": p95 is not None and p95 <= config.max_p95_latency_ms,
        "slow_drops_fully_accounted": metrics["websocket_drops"] == max(
            0, n - config.subscriber_queue_size,
        ),
        "slow_queue_retains_latest": metrics["slow_retained_indices"] == list(range(
            max(0, n - config.subscriber_queue_size), n,
        )),
        "scripted_reconnects_observed": metrics["source_reconnects"] == (
            (n - 1) // config.reconnect_every
        ),
        "boot_changes_observed": metrics["session_changes"] == expected_sessions,
        "history_bounded": metrics["history_sizes"] == {
            f"soak-{device}": min(
                config.history_size, max(0, (n - 1 - device) // config.devices + 1),
            ) for device in range(config.devices)
        },
        "memory_warmup_completed": metrics["memory_baseline_bytes"] is not None,
        "python_memory_growth_within_limit": growth is not None and (
            growth <= config.max_memory_growth_bytes
        ),
        "ran_for_scheduled_duration": metrics["elapsed_seconds"] >= config.duration_seconds,
        "scheduled_rate_sustained": metrics["elapsed_seconds"] <= (
            config.duration_seconds * (1 + config.max_duration_overrun_fraction)
        ),
    }


async def run_soak(config: SoakConfig) -> dict:
    if tracemalloc.is_tracing():
        raise RuntimeError("run the soak in its own process without existing tracemalloc tracing")
    tracemalloc.start()
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    source = SoakSource(config)
    hub = TelemetryHub(Settings(
        history_size=config.history_size,
        subscriber_queue_size=config.subscriber_queue_size,
        max_devices=config.devices,
    ), source)
    latency = LatencyHistogram()
    consumed = 0
    order_errors = 0
    next_expected = 0
    memory = {"baseline": None, "maximum": None, "final": None, "samples": 0}
    done = asyncio.Event()

    async def sample_memory() -> None:
        async def wait_until(deadline: float) -> None:
            while not done.is_set() and (remaining := deadline - time.perf_counter()) > 0:
                try:
                    await asyncio.wait_for(done.wait(), timeout=remaining)
                except TimeoutError:
                    continue

        await wait_until(started + config.warmup_seconds)
        if done.is_set():
            return
        memory["baseline"] = tracemalloc.get_traced_memory()[0]
        memory["maximum"] = memory["baseline"]
        while True:
            current = tracemalloc.get_traced_memory()[0]
            memory["final"] = current
            memory["maximum"] = max(memory["maximum"], current)
            memory["samples"] += 1
            if done.is_set():
                return
            await wait_until(time.perf_counter() + config.memory_sample_seconds)

    async def consume(queue: asyncio.Queue) -> None:
        nonlocal consumed, order_errors, next_expected
        while True:
            event = await queue.get()
            index = source.index_for(event)
            order_errors += int(index != next_expected)
            next_expected = index + 1
            consumed += 1
            timestamp = source.timestamps[index % len(source.timestamps)]
            if timestamp is not None and timestamp[0] == index:
                latency.add(max(0, (time.perf_counter() - timestamp[1]) * 1000))
            queue.task_done()

    sampler = asyncio.create_task(sample_memory())
    consumer = None
    try:
        async with hub.subscribe() as fast, hub.subscribe() as slow:
            consumer = asyncio.create_task(consume(fast))
            await hub.run()
            await asyncio.wait_for(fast.join(), timeout=2)
            elapsed = time.perf_counter() - started
            done.set()
            await sampler
            _, peak = tracemalloc.get_traced_memory()
            retained = []
            while not slow.empty():
                retained.append(source.index_for(slow.get_nowait()))
            stats = hub.stats().model_dump(exclude={"started_at", "websocket_clients"})
            metrics = {
                **stats,
                "generated": source.generated,
                "elapsed_seconds": elapsed,
                "achieved_frames_per_second": source.generated / elapsed,
                "fast_consumed": consumed,
                "fast_order_errors": order_errors,
                "fast_latency_samples": latency.count,
                "fast_latency_p95_upper_bound_ms": latency.p95_upper_bound(),
                "fast_latency_max_ms": latency.maximum_ms,
                "fast_latency_overflow_above_1000ms": latency.overflow,
                "slow_retained_indices": retained,
                "history_sizes": {
                    f"soak-{device}": len(await hub.store.readings(
                        f"soak-{device}", config.history_size + 1,
                    )) for device in range(config.devices)
                },
                "memory_baseline_bytes": memory["baseline"],
                "memory_maximum_sampled_after_warmup_bytes": memory["maximum"],
                "memory_final_sample_bytes": memory["final"],
                "python_memory_growth_bytes": None if memory["baseline"] is None else max(
                    0, memory["maximum"] - memory["baseline"],
                ),
                "python_traced_peak_bytes": peak,
                "memory_samples_after_warmup": memory["samples"],
            }
            checks = acceptance_checks(metrics, config)
            return {
                "profile": "host-only-scripted-soak",
                "started_at_utc": started_at,
                "python": sys.version,
                "platform": platform.platform(),
                "config": asdict(config),
                "measurement_scope": {
                    "latency": (
                        "encode-complete to fast consumption; 1 ms histogram upper bound"
                    ),
                    "memory": (
                        "tracemalloc Python allocations; maximum sampled current minus warmup "
                        "baseline; not RSS"
                    ),
                    "transport": (
                        "scripted SourceReset events with partial prefixes; no serial driver "
                        "or hardware"
                    ),
                    "timestamp_ring_capacity": len(source.timestamps),
                    "latency_histogram_bins": len(latency.bins),
                },
                "metrics": metrics,
                "checks": checks,
                "passed": all(checks.values()),
            }
    finally:
        done.set()
        if consumer is not None:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await asyncio.gather(sampler, return_exceptions=True)
        await hub.stop()
        tracemalloc.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--rate", type=float, default=100)
    parser.add_argument("--warmup", type=float, default=30)
    parser.add_argument(
        "--output", type=Path, default=Path("docs/validation/host-soak-20261012.json"),
    )
    args = parser.parse_args(argv)
    config = SoakConfig(
        duration_seconds=args.duration, rate_hz=args.rate, warmup_seconds=args.warmup,
    )
    repository = Path(__file__).resolve().parents[1]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True,
    ).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository,
        capture_output=True, text=True, check=True,
    ).stdout.strip())
    measured_files = ["scripts/soak_test.py"] + [
        f"src/sensor_monitor/{name}.py" for name in (
            "__init__", "capture", "sources", "protocol", "models", "hub", "store",
            "alerts", "config",
        )
    ]
    source_hashes = {
        name: hashlib.sha256((repository / name).read_bytes()).hexdigest()
        for name in measured_files
    }
    print(json.dumps({"status": "starting", "config": asdict(config)}), flush=True)
    result = asyncio.run(run_soak(config))
    result["git_head_at_start"] = revision
    result["working_tree_dirty_at_start"] = dirty
    result["source_sha256_at_start"] = source_hashes
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": result["passed"], "output": str(args.output),
        "failed_checks": [key for key, value in result["checks"].items() if not value],
    }))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
