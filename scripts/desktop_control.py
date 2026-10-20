"""Private parent/child lifecycle channel; never exposed over HTTP."""
from __future__ import annotations

import asyncio
import threading
from contextlib import suppress


async def owned_run(factory, stream):
    """Wait for job assignment, then stop cleanly on shutdown or parent pipe EOF."""
    loop = asyncio.get_running_loop()
    start, stop = asyncio.Event(), asyncio.Event()

    def signal(event):
        with suppress(RuntimeError):  # The daemon reader can outlive the event loop.
            loop.call_soon_threadsafe(event.set)

    def read():
        try:
            for line in stream:
                if line.strip() == "start":
                    signal(start)
                elif line.strip() == "shutdown":
                    break
        finally:
            signal(stop)

    threading.Thread(target=read, name="desktop-control", daemon=True).start()
    started = asyncio.create_task(start.wait())
    stopped = asyncio.create_task(stop.wait())
    runner = None
    try:
        await asyncio.wait([started, stopped], return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            return
        runner = asyncio.create_task(factory())
        await asyncio.wait([runner, stopped], return_when=asyncio.FIRST_COMPLETED)
        if runner.done():
            await runner  # Preserve startup failures and their nonzero exit.
        else:
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner  # Includes report persistence and emulator cleanup.
    finally:
        for task in (started, stopped):
            task.cancel()
        await asyncio.gather(started, stopped, return_exceptions=True)
        if runner is not None and not runner.done():
            runner.cancel()
            with suppress(asyncio.CancelledError):
                await runner
