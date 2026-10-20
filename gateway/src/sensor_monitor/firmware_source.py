"""Stream the compiled C firmware simulator directly into the production decoder."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from pathlib import Path

from .protocol import MAX_FRAME_BYTES
from .sources import SourceReset, TelemetrySource


class FirmwareSource(TelemetrySource):
    name = "firmware"

    def __init__(
        self,
        binary: Path,
        arguments: tuple[str, ...] = (),
        *,
        boot_id: int = 1,
        rate_hz: float = 10,
        repeat: bool = False,
    ) -> None:
        self.binary = binary.resolve()
        self.arguments = arguments
        self.boot_id = boot_id
        self.rate_hz = rate_hz
        self.repeat = repeat
        self.reconnects = 0
        self._process: asyncio.subprocess.Process | None = None
        self._pending: asyncio.Task | None = None
        self._closing = False
        self._close_lock = asyncio.Lock()
        if not self.binary.is_file():
            raise ValueError(f"compiled simulator not found: {self.binary}")
        if not 0 <= boot_id < 2**64 or not 0 < rate_hz <= 10_000:
            raise ValueError("invalid simulator boot identity or rate")
        if "--boot-id" in arguments or "--device-id" in arguments:
            raise ValueError("source owns simulator identity")

    async def chunks(self, stop: asyncio.Event):
        try:
            while not stop.is_set() and not self._closing:

                async def start():
                    self._process = await asyncio.create_subprocess_exec(
                        str(self.binary),
                        *self.arguments,
                        "--boot-id",
                        str(self.boot_id),
                        "--device-id",
                        "sim-hub",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                        limit=MAX_FRAME_BYTES + 2,
                    )

                self._pending = asyncio.create_task(start())
                await asyncio.shield(self._pending)
                if self._closing:
                    return
                process = self._process
                assert process is not None
                yield SourceReset()
                assert process.stdout is not None
                while not stop.is_set() and not self._closing:
                    self._pending = asyncio.create_task(process.stdout.readline())
                    line = await asyncio.shield(self._pending)
                    if self._closing or stop.is_set():
                        return
                    if not line:
                        break
                    yield line
                    # Windows event-loop timers may wake early. Verify the real
                    # deadline rather than treating a timeout as elapsed time.
                    deadline = time.perf_counter() + 1 / self.rate_hz
                    while not stop.is_set() and not self._closing:
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        with suppress(TimeoutError):
                            await asyncio.wait_for(stop.wait(), remaining)
                if stop.is_set() or self._closing:
                    return
                result = await process.wait()
                if result:
                    raise RuntimeError(f"C simulator exited with status {result}")
                if not self.repeat:
                    return
                if self.boot_id == 2**64 - 1:
                    raise RuntimeError("simulator boot counter exhausted")
                # This is a newly executed simulator boot, never relabelled old capture data.
                self.boot_id += 1
                self.reconnects += 1
        finally:
            await self.close()

    async def close(self) -> None:
        self._closing = True
        async with self._close_lock:
            if self._process is not None and self._process.returncode is None:
                with suppress(ProcessLookupError):
                    self._process.kill()
            if self._pending is not None:
                with suppress(Exception):
                    await asyncio.shield(self._pending)
                self._pending = None
            process, self._process = self._process, None
            if process is not None:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                # A paused PIPE reader can prevent wait() finishing even after kill.
                # Drain in bounded chunks after the sole pending read completes.
                if process.stdout:
                    while await process.stdout.read(4096):
                        pass
                await process.wait()
