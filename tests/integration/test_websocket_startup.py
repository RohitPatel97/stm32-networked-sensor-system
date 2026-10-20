"""Force a producer update while the initial socket snapshot is being sent."""

import asyncio
import json

from fastapi import WebSocketDisconnect
from sensor_monitor.app import create_app, telemetry_socket
from sensor_monitor.config import Settings
from sensor_monitor.sources import TelemetrySource


def test_frame_during_initial_snapshot_is_delivered(compiled_frames, tmp_path):
    frame = compiled_frames("--duration-ms", "100")[0]

    class SingleFrameSource(TelemetrySource):
        name = "socket-startup-test"

        async def chunks(self, stop):
            yield json.dumps(frame).encode() + b"\n"

    async def scenario():
        app = create_app(Settings(log_dir=tmp_path), SingleFrameSource())
        events = []
        delivery_queue = None
        snapshot_sent = asyncio.Event()
        telemetry_sent = asyncio.Event()

        class DelayedTransport:
            async def accept(self):
                pass

            async def send_json(self, event):
                nonlocal delivery_queue
                events.append(event)
                if event["type"] == "snapshot":
                    # Real ASGI sends can yield while the independent producer runs.
                    await app.state.hub.run()
                    snapshot_sent.set()
                elif event["type"] == "telemetry":
                    delivery_queue = next(iter(app.state.hub._subscribers))
                    telemetry_sent.set()
                    raise WebSocketDisconnect()

        transport = DelayedTransport()
        transport.app = app
        task = asyncio.create_task(telemetry_socket(transport))
        try:
            await asyncio.wait_for(snapshot_sent.wait(), timeout=2)
            assert events[0]["stats"]["frames_accepted"] == 0
            assert app.state.hub.stats().frames_accepted == 1
            await asyncio.wait_for(telemetry_sent.wait(), timeout=0.5)
            assert events[1]["reading"]["sequence"] == 0
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert app.state.hub.stats().websocket_clients == 0
        assert delivery_queue is not None
        await asyncio.wait_for(delivery_queue.join(), timeout=0.2)

    asyncio.run(scenario())


def test_disconnect_while_sending_snapshot_removes_subscription(tmp_path):
    class IdleSource(TelemetrySource):
        name = "idle-test"

        async def chunks(self, stop):
            return
            yield b""  # pragma: no cover

    async def scenario():
        app = create_app(Settings(log_dir=tmp_path), IdleSource())

        class DisconnectingTransport:
            async def accept(self):
                pass

            async def send_json(self, event):
                assert app.state.hub.stats().websocket_clients == 1
                raise WebSocketDisconnect()

        transport = DisconnectingTransport()
        transport.app = app
        await telemetry_socket(transport)
        assert app.state.hub.stats().websocket_clients == 0

    asyncio.run(scenario())
