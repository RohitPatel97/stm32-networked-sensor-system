"""Lifecycle regressions for the native app's private stdin channel."""
import asyncio
import queue
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from desktop_control import owned_run


class Pipe:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


@pytest.mark.parametrize("ending", ["shutdown\n", None])
def test_parent_shutdown_and_eof_await_cleanup(ending):
    async def check():
        pipe = Pipe()
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def service():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.02)
                cleaned.set()

        task = asyncio.create_task(owned_run(service, pipe))
        await asyncio.sleep(0.02)
        assert not started.is_set()
        pipe.lines.put("start\n")
        await asyncio.wait_for(started.wait(), 1)
        pipe.lines.put(ending)
        await asyncio.wait_for(task, 1)
        assert cleaned.is_set()
    asyncio.run(check())


def test_close_before_start_never_launches_service():
    async def unexpected():
        raise AssertionError("must not launch")
    asyncio.run(owned_run(unexpected, iter(["shutdown\n"])))


def test_startup_failure_is_propagated():
    async def check():
        pipe = Pipe()
        pipe.lines.put("start\n")

        async def broken():
            raise RuntimeError("missing firmware")

        try:
            with pytest.raises(RuntimeError, match="missing firmware"):
                await asyncio.wait_for(owned_run(broken, pipe), 1)
        finally:
            pipe.lines.put(None)
    asyncio.run(check())
