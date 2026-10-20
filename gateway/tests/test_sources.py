from __future__ import annotations

import asyncio
import threading
from io import BytesIO
from pathlib import Path

import pytest

from sensor_monitor.config import Settings
from sensor_monitor.models import FaultMode
from sensor_monitor.protocol import parse_frame
from sensor_monitor.sources import (
    ReplaySource,
    SerialSource,
    SimulatorSource,
    TelemetrySource,
    build_source,
)


def test_simulator_is_deterministic_and_fault_controllable() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        source = SimulatorSource(rate_hz=1000, seed=4)
        source.set_fault(FaultMode.I2C_ERROR)
        stream = source.chunks(stop)
        first = parse_frame((await anext(stream)).rstrip())
        stop.set()
        await stream.aclose()
        assert first.sequence == 0
        assert first.i2c_errors == 1
        assert source.mode is FaultMode.I2C_ERROR

    asyncio.run(scenario())


def test_replay_source_emits_each_nonempty_line_once(tmp_path) -> None:
    async def scenario() -> None:
        capture = tmp_path / "capture.jsonl"
        capture.write_bytes(b'{"a":1}\n\n{"b":2}\n')
        source = ReplaySource(capture, rate_hz=1000, loop=False)
        chunks = [chunk async for chunk in source.chunks(asyncio.Event())]
        assert chunks == [b'{"a":1}\n', b'{"b":2}\n']

    asyncio.run(scenario())


def test_replay_reads_large_capture_in_bounded_pieces(tmp_path, monkeypatch) -> None:
    payload = b"x" * 100_000 + b'\n{"a":1}\n'
    read_sizes = []

    class BoundedFile(BytesIO):
        def readline(self, size=-1):
            assert 0 < size <= 2050, "capture reads must have a bounded byte limit"
            read_sizes.append(size)
            return super().readline(size)

        def read(self, size=-1):
            assert 0 < size <= 2050, "capture reads must have a bounded byte limit"
            read_sizes.append(size)
            return super().read(size)

    def open_capture(path, mode):
        assert mode == "rb"
        return BoundedFile(payload)

    def reject_read_bytes(path):
        raise AssertionError("capture replay must not load the entire file")

    monkeypatch.setattr(Path, "open", open_capture)
    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)

    async def scenario() -> None:
        source = ReplaySource(tmp_path / "large.jsonl", rate_hz=1000, loop=False)
        chunks = [chunk async for chunk in source.chunks(asyncio.Event())]
        assert len(chunks) == 2
        assert len(chunks[0]) <= 2050
        assert chunks[0].endswith(b"\n")
        assert chunks[1] == b'{"a":1}\n'

    asyncio.run(scenario())
    assert len(read_sizes) > 2


def test_replay_cancellation_finishes_read_and_closes_file_off_event_loop(
    tmp_path, monkeypatch,
) -> None:
    loop_thread = threading.get_ident()
    read_started = threading.Event()
    release_read = threading.Event()
    read_finished = threading.Event()
    operations = []

    class BlockingFile(BytesIO):
        def readline(self, size=-1):
            operations.append(("read", threading.get_ident()))
            read_started.set()
            assert release_read.wait(timeout=3), "event loop did not release the pending read"
            result = super().readline(size)
            read_finished.set()
            return result

        def close(self):
            if self.closed:
                return
            operations.append(("close", threading.get_ident()))
            try:
                assert read_finished.is_set(), "file closed while a worker was still reading"
                assert threading.get_ident() != loop_thread, "file close blocked the event loop"
            finally:
                super().close()

    def open_capture(path, mode):
        operations.append(("open", threading.get_ident()))
        assert threading.get_ident() != loop_thread, "file open blocked the event loop"
        assert mode == "rb"
        return BlockingFile(b'{"sequence":7}\n')

    monkeypatch.setattr(Path, "open", open_capture)

    async def scenario() -> None:
        source = ReplaySource(tmp_path / "slow.jsonl", rate_hz=1000, loop=False)
        stream = source.chunks(asyncio.Event())
        pending = asyncio.create_task(anext(stream))
        callback_ran = False
        release_handle = None

        async def wait_for_read() -> None:
            while not read_started.is_set():
                if pending.done():
                    await pending
                    raise AssertionError("source ended without reading the capture")
                await asyncio.sleep(0)

        def release_from_loop() -> None:
            nonlocal callback_ran
            callback_ran = True
            release_read.set()

        try:
            await asyncio.wait_for(wait_for_read(), timeout=2)
            pending.cancel()
            release_handle = asyncio.get_running_loop().call_later(0.01, release_from_loop)
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=2)
            assert callback_ran, "cancellation returned before the pending read was released"
            assert [operation for operation, _ in operations] == ["open", "read", "close"]
            assert all(thread_id != loop_thread for _, thread_id in operations)
        finally:
            release_read.set()
            if release_handle is not None:
                release_handle.cancel()
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()

    asyncio.run(scenario())


def test_replay_does_not_split_records_at_lone_carriage_return(tmp_path) -> None:
    async def scenario() -> None:
        capture = tmp_path / "carriage-return.jsonl"
        capture.write_bytes(b'{"a":1}\r{"b":2}\n')
        source = ReplaySource(capture, rate_hz=1000, loop=False)
        chunks = [chunk async for chunk in source.chunks(asyncio.Event())]
        assert chunks == [b'{"a":1}\r{"b":2}\n']

    asyncio.run(scenario())


def test_replay_loop_preserves_original_bytes_and_final_record(tmp_path) -> None:
    async def scenario() -> None:
        capture = tmp_path / "loop.jsonl"
        capture.write_bytes(b'{"sequence":7}\n\n{"sequence":8}')
        source = ReplaySource(capture, rate_hz=1000, loop=True)
        stream = source.chunks(asyncio.Event())
        try:
            chunks = [await anext(stream) for _ in range(4)]
        finally:
            await stream.aclose()
        assert chunks == [b'{"sequence":7}\n', b'{"sequence":8}\n'] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [b"", b"\n\r\n\n"])
def test_replay_empty_capture_ends_even_when_looping(tmp_path, payload) -> None:
    async def scenario() -> None:
        capture = tmp_path / "empty.jsonl"
        capture.write_bytes(payload)
        source = ReplaySource(capture, rate_hz=1000, loop=True)
        stream = source.chunks(asyncio.Event())
        try:
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(anext(stream), timeout=1)
        finally:
            await stream.aclose()

    asyncio.run(scenario())


def test_source_factory_selects_configured_adapter(tmp_path) -> None:
    assert isinstance(build_source(Settings()), SimulatorSource)
    assert isinstance(build_source(Settings(source="serial")), SerialSource)
    capture = tmp_path / "capture.jsonl"
    capture.write_text("", encoding="utf-8")
    assert isinstance(
        build_source(Settings(source="replay", replay_file=capture)), ReplaySource
    )


def test_base_source_requires_chunks_implementation() -> None:
    async def scenario() -> None:
        source = TelemetrySource()
        with pytest.raises(NotImplementedError):
            await anext(source.chunks(asyncio.Event()))
        assert await source.close() is None

    asyncio.run(scenario())
