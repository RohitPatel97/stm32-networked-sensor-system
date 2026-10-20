"""Sustained real-C v3 -> gateway -> loopback WebSocket stress, with bounded metrics."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import gc
import hashlib
import json
import os
import platform
import socket
import statistics
import sys
import threading
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway/src"))

import uvicorn
from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.firmware_source import FirmwareSource
from sensor_monitor.sources import SourceReset, TelemetrySource
from websockets.asyncio.client import connect

FIRST_BOOT = 2**53 + 101
FRAMES_PER_BOOT = 60


def process_resources() -> dict:
    """Current-process working set/private commit and handles, not peak RSS."""
    result = {"threads": threading.active_count()}
    if sys.platform == "win32":
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "peak_ws", "working_set", "peak_paged", "paged", "peak_nonpaged",
                    "nonpaged", "pagefile", "peak_pagefile", "private_bytes",
                )
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        handle = kernel.GetCurrentProcess()
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        kernel.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        handles = wintypes.DWORD()
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.GetProcessHandleCount(handle, ctypes.byref(handles)):
            raise ctypes.WinError(ctypes.get_last_error())
        result.update(working_set=counters.working_set, private_bytes=counters.private_bytes,
                      handles=handles.value)
    elif sys.platform.startswith("linux"):
        pages = [int(n) for n in Path("/proc/self/statm").read_text().split()]
        result.update(working_set=pages[1] * os.sysconf("SC_PAGE_SIZE"),
                      handles=len(list(Path("/proc/self/fd").iterdir())))
    return result


class InterruptedFirmware(TelemetrySource):
    name = "firmware-stress"

    def __init__(self, binary: Path, rate: float):
        self.inner = FirmwareSource(binary, (
            "--duration-ms", "6000", "--disconnect", "all",
            "--disconnect-at-ms", "1500", "--reconnect-at-ms", "3000",
        ), boot_id=FIRST_BOOT, rate_hz=rate, repeat=True)
        self.frames = self.boots = self.reconnects = 0
        self.expected_rejections = self.expected_parse_errors = 0
        self.last: bytes | None = None
        self.current_boot = None
        self.saw_outage = self.saw_recovery = False

    async def boundary(self):
        if self.last is not None:
            self.reconnects += 1
            # A disconnected partial record must never poison the next stream.
            yield self.last[:37]
        yield SourceReset()

    async def chunks(self, stop):
        async for chunk in self.inner.chunks(stop):
            if isinstance(chunk, SourceReset):
                async for boundary in self.boundary():
                    yield boundary
                continue
            frame = json.loads(chunk)
            new_boot = frame["boot_id"] != self.current_boot
            if new_boot:
                self.boots += 1
                self.current_boot = frame["boot_id"]
            if self.frames and self.frames % 97 == 0:
                async for boundary in self.boundary():
                    yield boundary
            self.frames += 1
            # Split both the JSON and its delimiter to exercise the UART decoder.
            yield chunk[:19]
            yield chunk[19:-1]
            yield chunk[-1:]
            if new_boot and self.last is not None:
                self.expected_rejections += 1
                yield self.last  # delayed prior boot, after the newer boot was accepted
            if self.frames % 139 == 0:
                self.expected_rejections += 1
                yield chunk  # duplicate must not refresh history or reach subscribers
            if self.frames % 503 == 0:
                self.expected_parse_errors += 1
                yield b"x" * 2049
                async for boundary in self.boundary():
                    yield boundary
            self.last = chunk

    async def close(self):
        await self.inner.close()


def growth_metrics(samples: list[dict]) -> dict:
    """Compare equal windows and estimate a slope; retain a bounded sample series."""
    result = {}
    width = max(1, min(6, len(samples) // 3))
    times = [s["elapsed"] for s in samples]
    mean_t = statistics.mean(times)
    denominator = sum((t - mean_t) ** 2 for t in times)
    for key in ("python_bytes", "private_bytes", "working_set", "handles", "tasks", "threads"):
        if key not in samples[0]:
            continue
        values = [s[key] for s in samples]
        mean_v = statistics.mean(values)
        slope = (sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values))
                 / denominator * 60) if denominator else 0
        result[key] = {
            "first_window_median": statistics.median(values[:width]),
            "last_window_median": statistics.median(values[-width:]),
            "window_growth": statistics.median(values[-width:]) - statistics.median(values[:width]),
            "maximum": max(values), "minimum": min(values), "slope_per_minute": slope,
        }
    return result


async def run(args) -> dict:
    source = InterruptedFirmware(args.binary, args.rate)
    settings = Settings(history_size=120, subscriber_queue_size=64,
                        log_dir=args.output.parent / "stress-logs", log_level="ERROR")
    app = create_app(settings, source)
    hub = app.state.hub
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                         log_level="error", access_log=False))
    stop_clients = asyncio.Event()
    metrics = {"fast_consumed": 0, "fast_order_errors": 0, "ws_events": 0,
               "ws_order_errors": 0, "ws_connections": 0, "ws_failures": 0,
               "outages": 0, "recoveries": 0, "http_checks": 0}
    samples = []
    began = time.perf_counter()
    report = {"started_at": datetime.now(UTC).isoformat(), "python": sys.version,
              "platform": platform.platform(), "duration_requested": args.duration,
              "warmup_seconds": args.warmup, "requested_frame_pacing_hz": args.rate,
              "first_boot": str(FIRST_BOOT), "metrics": metrics}

    async def fast_consumer(queue):
        while True:
            event = await queue.get()
            reading = event["reading"]
            index = (int(reading["boot_id"]) - FIRST_BOOT) * FRAMES_PER_BOOT + reading["sequence"]
            metrics["fast_order_errors"] += int(index != metrics["fast_consumed"])
            metrics["fast_consumed"] += 1
            if reading["firmware"]["imu"] is None:
                metrics["outages"] += 1
            if reading["i2c_errors"] > 0 and event["health"]["state"] == "online":
                metrics["recoveries"] += 1
            queue.task_done()

    async def websocket_client(churn=False):
        previous = None
        while not stop_clients.is_set():
            try:
                async with connect(f"ws://127.0.0.1:{port}/ws/telemetry",
                                   open_timeout=3, close_timeout=2) as ws:
                    metrics["ws_connections"] += 1
                    assert json.loads(await ws.recv())["type"] == "snapshot"
                    count = 0
                    while not stop_clients.is_set():
                        event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if event["type"] != "telemetry":
                            continue
                        reading = event["reading"]
                        assert isinstance(reading["boot_id"], str)
                        index = (int(reading["boot_id"]) - FIRST_BOOT) * FRAMES_PER_BOOT + reading["sequence"]
                        if not churn and previous is not None:
                            metrics["ws_order_errors"] += int(index != previous + 1)
                        previous = index
                        metrics["ws_events"] += 1
                        count += 1
                        if churn and count >= 3:
                            break
                if churn:
                    await asyncio.sleep(0.2)
            except asyncio.CancelledError:
                raise
            except Exception:
                metrics["ws_failures"] += 1
                raise

    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    tasks = []
    tracemalloc.start()
    try:
        async with hub.subscribe() as fast, hub.subscribe() as slow:
            tasks.append(asyncio.create_task(fast_consumer(fast)))
            while not server.started:
                if server_task.done():
                    await server_task
                    raise RuntimeError("server stopped during startup")
                await asyncio.sleep(0.01)
            tasks += [asyncio.create_task(websocket_client()),
                      asyncio.create_task(websocket_client(churn=True))]
            import httpx

            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                next_sample = began + args.warmup
                next_progress = began
                while time.perf_counter() - began < args.duration:
                    for task in tasks:
                        if task.done():
                            await task
                            raise RuntimeError("stress worker stopped early")
                    assert not server_task.done()
                    if time.perf_counter() >= next_sample:
                        response = await client.get("/healthz")
                        assert response.status_code == 200
                        metrics["http_checks"] += 1
                        gc.collect()
                        samples.append({"elapsed": time.perf_counter() - began,
                                        "python_bytes": tracemalloc.get_traced_memory()[0],
                                        "tasks": len(asyncio.all_tasks()), **process_resources()})
                        next_sample += args.sample_interval
                    if time.perf_counter() >= next_progress:
                        progress = {"elapsed": round(time.perf_counter() - began, 1),
                                    "frames": source.frames, "boots": source.boots,
                                    "reconnects": source.reconnects, **metrics}
                        print(json.dumps(progress), flush=True)
                        args.output.with_suffix(".progress.json").write_text(json.dumps(progress, indent=2))
                        next_progress += 30
                    await asyncio.sleep(0.05)
            stop_clients.set()
            for task in tasks[1:]:
                task.cancel()
            await asyncio.gather(*tasks[1:], return_exceptions=True)
            await hub.stop()
            await asyncio.wait_for(app.state.acquisition_task, timeout=5)
            await asyncio.wait_for(fast.join(), timeout=5)
            elapsed = time.perf_counter() - began
            stats = hub.stats().model_dump(mode="json")
            retained = []
            while not slow.empty():
                retained.append(slow.get_nowait()["reading"])
                slow.task_done()
            try:
                await asyncio.wait_for(slow.join(), timeout=0.25)
                slow_joined = True
            except TimeoutError:
                slow_joined = False
            health = await hub.store.health("sim-hub")
            history = await hub.store.readings("sim-hub", 1000)
            growth = growth_metrics(samples)
            checks = {
                "scheduled_duration_completed": elapsed >= args.duration,
                "all_fresh_frames_accepted": stats["frames_accepted"] == source.frames,
                "fast_subscriber_complete_and_ordered": metrics["fast_consumed"] == source.frames and metrics["fast_order_errors"] == 0,
                "only_injected_sequence_rejections": stats["sequence_anomalies"] == source.expected_rejections,
                "only_injected_parse_errors": stats["parse_errors"] == source.expected_parse_errors,
                "all_reboots_observed": stats["session_changes"] == source.boots - 1,
                "reconnect_count_matches": stats["source_reconnects"] == source.reconnects,
                "no_device_rejections": stats["device_rejections"] == 0,
                "websocket_reconnects_succeeded": metrics["ws_connections"] >= 3 and metrics["ws_failures"] == 0,
                "persistent_websocket_ordered": metrics["ws_order_errors"] == 0,
                "history_bounded": len(history) == min(120, source.frames),
                "latest_boot_retained": health.boot_id == source.current_boot,
                "sensor_outage_and_recovery_seen": metrics["outages"] > 0 and metrics["recoveries"] > 0,
                "slow_drop_count_matches": stats["websocket_drops"] == source.frames - len(retained),
                "slow_queue_retains_latest": retained[-1]["sequence"] == history[-1].sequence,
                "slow_queue_join_completes": slow_joined,
                "python_growth_bounded": growth["python_bytes"]["window_growth"] < 2 * 1024**2,
                "python_slope_bounded": growth["python_bytes"]["slope_per_minute"] < 64 * 1024,
                "rss_growth_bounded": growth["working_set"]["window_growth"] < 32 * 1024**2,
                "handle_growth_bounded": growth["handles"]["window_growth"] <= 16,
                "task_growth_bounded": growth["tasks"]["window_growth"] <= 10,
                "simulator_child_reaped": source.inner._process is None,
            }
            if "private_bytes" in growth:
                checks["private_memory_growth_bounded"] = growth["private_bytes"]["window_growth"] < 16 * 1024**2
                checks["private_memory_slope_bounded"] = growth["private_bytes"]["slope_per_minute"] < 1024**2
            report.update(elapsed_seconds=elapsed, frames=source.frames, boots=source.boots,
                          effective_frames_per_second=source.frames / elapsed, stats=stats,
                          samples=samples, growth=growth, checks=checks,
                          passed=all(checks.values()))
    finally:
        stop_clients.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await hub.stop()
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        listener.close()
        report["subscribers_after_shutdown"] = hub.stats().websocket_clients
        report["resources_after_shutdown"] = process_resources()
        tracemalloc.stop()
    report["checks"]["subscribers_released"] = report["subscribers_after_shutdown"] == 0
    report["passed"] = all(report["checks"].values())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--warmup", type=float, default=60)
    parser.add_argument("--sample-interval", type=float, default=10)
    parser.add_argument("--rate", type=float, default=200)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/host/sensor_hub_sim.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/validation/stress-20261017.json")
    args = parser.parse_args()
    if not 0 < args.warmup < args.duration or args.sample_interval <= 0:
        parser.error("require 0 < warmup < duration and a positive sampling interval")
    args.binary = args.binary.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started_hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [Path(__file__), *sorted((ROOT / "gateway/src/sensor_monitor").glob("*.py"))]}
    report = asyncio.run(run(args))
    report["source_sha256"] = started_hashes
    report["binary_sha256"] = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    report["scope"] = "real C subprocess reboots, scripted stream interruptions, real loopback WebSockets; memory covers server and bounded test clients in one process; no physical UART"
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "frames": report["frames"],
                      "failed_checks": [k for k, v in report["checks"].items() if not v],
                      "output": str(args.output)}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
