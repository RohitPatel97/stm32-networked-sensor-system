"""UART, deterministic simulator, and JSONL replay telemetry sources."""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .capture import iter_capture_file
from .config import Settings
from .models import FaultMode, WireTelemetry
from .protocol import MAX_FRAME_BYTES, encode_frame

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceReset:
    """A new byte stream has begun; discard framing state, not device history."""


class TelemetrySource:
    name = "unknown"
    reconnects = 0

    async def chunks(self, stop: asyncio.Event) -> AsyncIterator[bytes | SourceReset]:
        """Yield byte chunks or an explicit boundary between independent streams."""
        raise NotImplementedError
        yield b""  # pragma: no cover - keeps the base contract an async iterator

    async def close(self) -> None:
        """Release external resources; sources without resources need no override."""
        return None


class SimulatorSource(TelemetrySource):
    name = "simulator"

    def __init__(self, rate_hz: float = 10.0, seed: int = 7) -> None:
        self.rate_hz = rate_hz
        self._random = random.Random(seed)
        self._sequence = 0
        self._mode = FaultMode.NORMAL

    @property
    def mode(self) -> FaultMode:
        return self._mode

    def set_fault(self, mode: FaultMode) -> None:
        self._mode = mode

    async def chunks(self, stop: asyncio.Event) -> AsyncIterator[bytes]:
        interval = 1.0 / self.rate_hz
        start = asyncio.get_running_loop().time()
        while not stop.is_set():
            if self._mode == FaultMode.DISCONNECTED:
                await asyncio.sleep(interval)
                continue
            elapsed = asyncio.get_running_loop().time() - start
            jitter = self._random.uniform(-0.08, 0.08)
            temperature = 24.0 + 2.0 * math.sin(elapsed / 7.0) + jitter
            if self._mode == FaultMode.OVER_TEMPERATURE:
                temperature = 86.0 + jitter
            reading = WireTelemetry(
                device_id="stm32-sensor-hub",
                sequence=self._sequence,
                uptime_ms=int(elapsed * 1_000),
                temperature_c=round(temperature, 3),
                pressure_hpa=round(1013.25 + 1.8 * math.sin(elapsed / 11.0), 3),
                accel_g=(
                    round(0.02 * math.sin(elapsed), 4),
                    round(0.02 * math.cos(elapsed), 4),
                    round(1.0 + self._random.uniform(-0.006, 0.006), 4),
                ),
                battery_v=12.4,
                i2c_errors=1 if self._mode == FaultMode.I2C_ERROR else 0,
                queue_drops=5 if self._mode == FaultMode.QUEUE_PRESSURE else 0,
                watchdog_resets=0,
            )
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            yield encode_frame(reading)
            await asyncio.sleep(interval)


class ReplaySource(TelemetrySource):
    name = "replay"

    def __init__(self, path: Path, rate_hz: float = 10.0, loop: bool = True) -> None:
        self.path = path
        self.rate_hz = rate_hz
        self.loop = loop

    async def chunks(self, stop: asyncio.Event) -> AsyncIterator[bytes]:
        interval = 1.0 / self.rate_hz
        while not stop.is_set():
            emitted = False
            records = iter_capture_file(self.path)
            pending = None
            try:
                while not stop.is_set():
                    pending = asyncio.create_task(asyncio.to_thread(next, records, None))
                    record = await asyncio.shield(pending)
                    if record is None or stop.is_set():
                        break
                    _, raw = record
                    emitted = True
                    yield raw + b"\n"
                    await asyncio.sleep(interval)
            finally:
                # A cancelled await does not stop a worker thread. Let an in-flight
                # read finish before closing its generator, and keep close off-loop.
                if pending is not None:
                    with suppress(Exception):
                        await pending
                await asyncio.to_thread(records.close)
            if not self.loop or not emitted:
                return


class SerialSource(TelemetrySource):
    name = "serial"

    def __init__(self, port: str, baud_rate: int, reconnect_delay: float = 1.0) -> None:
        self.port = port
        self.baud_rate = baud_rate
        self.reconnect_delay = reconnect_delay
        self._serial: object | None = None
        self._pending: asyncio.Task | None = None
        self._close_lock = asyncio.Lock()
        self.reconnects = 0

    async def chunks(self, stop: asyncio.Event) -> AsyncIterator[bytes | SourceReset]:
        try:
            import serial
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError("serial source requires pyserial") from exc

        while not stop.is_set():
            try:
                async def connect() -> None:
                    self._serial = await asyncio.to_thread(
                        serial.Serial,
                        self.port,
                        self.baud_rate,
                        timeout=0.5,
                        exclusive=True,
                    )

                self._pending = asyncio.create_task(connect())
                await asyncio.shield(self._pending)
                if stop.is_set():
                    return
                yield SourceReset()
                while not stop.is_set():
                    self._pending = asyncio.create_task(
                        asyncio.to_thread(
                            self._serial.read_until, b"\n", MAX_FRAME_BYTES + 1
                        )
                    )
                    chunk = await asyncio.shield(self._pending)
                    if stop.is_set():
                        break
                    if chunk:
                        yield chunk
            except (serial.SerialException, OSError):
                if stop.is_set():
                    return
                self.reconnects += 1
                LOGGER.warning(
                    "serial source unavailable; retrying in %.1f s",
                    self.reconnect_delay,
                    extra={"port": self.port, "attempt": self.reconnects},
                )
            finally:
                await self.close()
            if not stop.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self.reconnect_delay)

    async def close(self) -> None:
        async with self._close_lock:
            # Worker threads cannot be cancelled by cancelling their awaiters.
            # Finish pending open/read calls before releasing the port; reads have
            # a 0.5-second driver timeout. Concurrent stop/finally calls close once.
            if self._pending is not None:
                with suppress(Exception):
                    await asyncio.shield(self._pending)
                self._pending = None
            serial_port, self._serial = self._serial, None
            if serial_port is not None:
                closing = asyncio.create_task(asyncio.to_thread(serial_port.close))
                try:
                    await asyncio.shield(closing)
                finally:
                    await closing


def build_source(settings: Settings) -> TelemetrySource:
    if settings.source == "simulator":
        return SimulatorSource(settings.simulator_rate_hz, settings.simulator_seed)
    if settings.source == "serial":
        return SerialSource(settings.serial_port, settings.baud_rate)
    assert settings.replay_file is not None
    return ReplaySource(
        settings.replay_file, settings.simulator_rate_hz, settings.replay_loop
    )
