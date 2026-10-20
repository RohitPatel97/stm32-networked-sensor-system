"""Bounded, reconnecting byte stream for the Renode UART socket."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from .protocol import MAX_FRAME_BYTES
from .sources import SourceReset, TelemetrySource


class TcpSource(TelemetrySource):
    name = "renode"

    def __init__(self, host: str, port: int, reconnect_delay: float = 0.25):
        if not 0 < port < 65536 or reconnect_delay <= 0:
            raise ValueError("invalid UART socket port or retry delay")
        self.host, self.port = host, port
        self.reconnect_delay = reconnect_delay
        self.reconnects = 0
        self.connected = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None
        self._closing = False

    async def chunks(self, stop: asyncio.Event):
        try:
            while not stop.is_set() and not self._closing:
                try:
                    reader, self._writer = await asyncio.wait_for(
                        asyncio.open_connection(self.host, self.port, limit=MAX_FRAME_BYTES * 2),
                        timeout=2,
                    )
                    if stop.is_set() or self._closing:
                        return
                    self.connected.set()
                    yield SourceReset()
                    while not stop.is_set() and not self._closing:
                        # Timeout permits a stop event even when the producer is paused.
                        try:
                            chunk = await asyncio.wait_for(reader.read(MAX_FRAME_BYTES), 0.5)
                        except TimeoutError:
                            continue
                        if not chunk:
                            break
                        yield chunk
                except (OSError, TimeoutError):
                    pass
                finally:
                    self.connected.clear()
                    await self._disconnect()
                if not stop.is_set() and not self._closing:
                    self.reconnects += 1
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), self.reconnect_delay)
        finally:
            await self.close()

    async def _disconnect(self):
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1)

    async def close(self):
        self._closing = True
        self.connected.clear()
        await self._disconnect()
