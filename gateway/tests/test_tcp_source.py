from __future__ import annotations

import asyncio
from contextlib import suppress

from sensor_monitor.sources import SourceReset
from sensor_monitor.tcp_source import TcpSource


def test_tcp_reconnect_marks_stream_boundary_and_preserves_fragment_bytes():
    async def scenario():
        connections = 0

        async def serve(reader, writer):
            nonlocal connections
            connections += 1
            writer.write(b'{"partial' if connections == 1 else b'{"complete":1}\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        source = TcpSource("127.0.0.1", server.sockets[0].getsockname()[1], 0.01)
        stop = asyncio.Event()
        stream = source.chunks(stop)
        try:
            assert isinstance(await anext(stream), SourceReset)
            assert await anext(stream) == b'{"partial'
            assert isinstance(await anext(stream), SourceReset)
            assert await anext(stream) == b'{"complete":1}\n'
            assert source.reconnects == 1
        finally:
            stop.set()
            await stream.aclose()
            server.close()
            await server.wait_closed()
        assert source._writer is None and not source.connected.is_set()

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_tcp_stopped_event_releases_a_silent_connection():
    async def scenario():
        released = asyncio.Event()

        async def serve(reader, writer):
            try:
                assert await reader.read() == b""
            finally:
                writer.close()
                await writer.wait_closed()
                released.set()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        source = TcpSource("127.0.0.1", server.sockets[0].getsockname()[1])
        stop = asyncio.Event()

        async def consume():
            async for _ in source.chunks(stop):
                pass

        task = asyncio.create_task(consume())
        try:
            await source.connected.wait()
            stop.set()
            await task
            await released.wait()
            assert source._writer is None
        finally:
            task.cancel()
            await source.close()
            server.close()
            await server.wait_closed()

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_tcp_cancel_and_close_during_read_do_not_leak_transport():
    async def scenario():
        released = asyncio.Event()

        async def serve(reader, writer):
            try:
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                released.set()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        source = TcpSource("127.0.0.1", server.sockets[0].getsockname()[1])

        async def consume():
            async for _ in source.chunks(asyncio.Event()):
                pass

        task = asyncio.create_task(consume())
        try:
            await source.connected.wait()
            task.cancel()
            await source.close()
            with suppress(asyncio.CancelledError):
                await task
            await released.wait()
            assert source._writer is None
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_tcp_retries_connection_refusal_until_stop():
    async def scenario():
        server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        source = TcpSource("127.0.0.1", port, 0.01)
        stop = asyncio.Event()

        async def consume():
            async for _ in source.chunks(stop):
                raise AssertionError("connection unexpectedly succeeded")

        task = asyncio.create_task(consume())
        try:
            while not source.reconnects:
                await asyncio.sleep(0.01)
            stop.set()
            await task
            assert source._writer is None
        finally:
            task.cancel()
            await source.close()

    asyncio.run(asyncio.wait_for(scenario(), 5))
