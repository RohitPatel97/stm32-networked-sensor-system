from __future__ import annotations

import asyncio
import threading
from collections import deque

import pytest
import serial

from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.models import WireTelemetry
from sensor_monitor.protocol import MAX_FRAME_BYTES, encode_frame
from sensor_monitor.sources import SerialSource, SourceReset

from .helpers import reading


class MockPort:
    def __init__(self, chunks=()) -> None:
        self.chunks = deque(chunks)
        self.closed = 0

    def read_until(self, delimiter, size):
        assert delimiter == b"\n"
        assert size == MAX_FRAME_BYTES + 1
        item = self.chunks.popleft() if self.chunks else b""
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed += 1


def wire(**overrides) -> WireTelemetry:
    return WireTelemetry(**reading(**overrides).model_dump(exclude={"received_at"}))


@pytest.mark.parametrize("discarding", [False, True])
def test_reconnect_discards_partial_frame_without_resetting_sequence_state(
    monkeypatch, discarding,
) -> None:
    first = wire()
    tail = b"x" * (MAX_FRAME_BYTES + 1) if discarding else encode_frame(first)[:20]
    previous = MockPort([encode_frame(first), tail, serial.SerialException("unplugged")])
    current = MockPort([
        encode_frame(wire(temperature_c=100)),  # duplicate must stay rejected
        encode_frame(wire(sequence=43)),
    ])
    ports = deque([previous, current])
    monkeypatch.setattr(serial, "Serial", lambda *args, **kwargs: ports.popleft())

    async def scenario() -> None:
        source = SerialSource("mock-uart", 115200, reconnect_delay=0)
        hub = TelemetryHub(Settings(), source)
        async with hub.subscribe() as updates:
            acquisition = asyncio.create_task(hub.run())
            try:
                one = await asyncio.wait_for(updates.get(), timeout=2)
                two = await asyncio.wait_for(updates.get(), timeout=2)
                assert [one["reading"]["sequence"], two["reading"]["sequence"]] == [42, 43]
                assert hub.stats().frames_accepted == 2
                assert hub.stats().sequence_anomalies == 1
                assert hub.stats().parse_errors == int(discarding)
                assert hub.stats().source_reconnects == 1
                assert await hub.store.alerts(10) == []
                history = await hub.store.readings(first.device_id, 10)
                assert [sample.sequence for sample in history] == [42, 43]
            finally:
                await hub.stop()
                await asyncio.wait_for(acquisition, timeout=2)
        assert previous.closed == current.closed == 1

    asyncio.run(scenario())


def test_serial_idle_timeout_keeps_partial_frame_and_emits_no_reset(monkeypatch) -> None:
    frame = encode_frame(wire())
    port = MockPort([frame[:20], b"", b"", frame[20:]])
    monkeypatch.setattr(serial, "Serial", lambda *args, **kwargs: port)

    async def scenario() -> None:
        source = SerialSource("mock-uart", 115200)
        stop = asyncio.Event()
        stream = source.chunks(stop)
        try:
            assert isinstance(await anext(stream), SourceReset)
            assert await anext(stream) == frame[:20]
            assert await anext(stream) == frame[20:]
            assert source.reconnects == 0
        finally:
            stop.set()
            await stream.aclose()
        assert port.closed == 1

    asyncio.run(scenario())


def test_serial_retries_failed_open_then_emits_one_boundary(monkeypatch) -> None:
    port = MockPort([encode_frame(wire())])
    attempts = []

    def connect(*args, **kwargs):
        attempts.append((args, kwargs))
        if len(attempts) == 1:
            raise serial.SerialException("port unavailable")
        return port

    monkeypatch.setattr(serial, "Serial", connect)

    async def scenario() -> None:
        source = SerialSource("mock-uart", 115200, reconnect_delay=0)
        stream = source.chunks(asyncio.Event())
        try:
            assert isinstance(await asyncio.wait_for(anext(stream), timeout=2), SourceReset)
            assert await anext(stream) == encode_frame(wire())
            assert source.reconnects == 1
            assert len(attempts) == 2
            assert attempts[1] == (("mock-uart", 115200), {"timeout": 0.5, "exclusive": True})
        finally:
            await stream.aclose()
        assert port.closed == 1

    asyncio.run(scenario())


def test_stop_interrupts_serial_retry_delay(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise serial.SerialException("port unavailable")

    monkeypatch.setattr(serial, "Serial", unavailable)

    async def scenario() -> None:
        stop = asyncio.Event()
        source = SerialSource("mock-uart", 115200, reconnect_delay=30)
        stream = source.chunks(stop)
        pending = asyncio.create_task(anext(stream))
        try:
            async def wait_for_attempt() -> None:
                while source.reconnects == 0:
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_for_attempt(), timeout=2)
            stop.set()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending, timeout=1)
            assert source.reconnects == 1
        finally:
            stop.set()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("blocked_operation", ["open", "read"])
def test_cancelling_serial_operation_finishes_worker_and_closes_port(
    monkeypatch, blocked_operation,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    loop_thread = threading.get_ident()
    operations = []

    def block() -> None:
        started.set()
        assert release.wait(timeout=3), "event loop did not release the serial worker"
        finished.set()

    class BlockingPort(MockPort):
        def read_until(self, delimiter, size):
            operations.append(("read", threading.get_ident()))
            block()
            return encode_frame(wire())

        def close(self):
            operations.append(("close", threading.get_ident()))
            assert finished.is_set(), "closed port before pending operation finished"
            super().close()

    port = BlockingPort()

    def connect(*args, **kwargs):
        operations.append(("open", threading.get_ident()))
        if blocked_operation == "open":
            block()
        return port

    monkeypatch.setattr(serial, "Serial", connect)

    async def scenario() -> None:
        source = SerialSource("mock-uart", 115200)
        stream = source.chunks(asyncio.Event())
        if blocked_operation == "read":
            assert isinstance(await anext(stream), SourceReset)
        pending = asyncio.create_task(anext(stream))
        release_handle = None
        try:
            async def wait_for_worker() -> None:
                while not started.is_set():
                    if pending.done():
                        await pending
                        raise AssertionError("source ended before its worker started")
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_for_worker(), timeout=2)
            pending.cancel()
            release_handle = asyncio.get_running_loop().call_later(0.01, release.set)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=2)
            assert port.closed == 1
            assert source.reconnects == 0
            assert operations[-1][0] == "close"
            assert all(thread_id != loop_thread for _, thread_id in operations)
        finally:
            release.set()
            if release_handle is not None:
                release_handle.cancel()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()
            await source.close()
        assert port.closed == 1

    asyncio.run(scenario())


def test_stop_during_serial_read_closes_once_and_does_not_publish(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingPort(MockPort):
        def read_until(self, delimiter, size):
            started.set()
            assert release.wait(timeout=3)
            return encode_frame(wire())

    port = BlockingPort()
    monkeypatch.setattr(serial, "Serial", lambda *args, **kwargs: port)

    async def scenario() -> None:
        hub = TelemetryHub(Settings(), SerialSource("mock-uart", 115200))
        acquisition = asyncio.create_task(hub.run())
        release_handle = None
        try:
            async def wait_for_worker() -> None:
                while not started.is_set():
                    await asyncio.sleep(0)

            await asyncio.wait_for(wait_for_worker(), timeout=2)
            shutdown = asyncio.create_task(hub.stop())
            release_handle = asyncio.get_running_loop().call_later(0.01, release.set)
            await asyncio.wait_for(shutdown, timeout=2)
            await asyncio.wait_for(acquisition, timeout=2)
            assert hub.stats().frames_received == 0
            assert hub.stats().source_reconnects == 0
            assert port.closed == 1
        finally:
            release.set()
            if release_handle is not None:
                release_handle.cancel()
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)

    asyncio.run(scenario())
