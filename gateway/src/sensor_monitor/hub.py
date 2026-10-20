"""Acquisition orchestration, persistence, alerting, and WebSocket fan-out."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from .alerts import AlertEngine
from .config import Settings
from .models import ServiceStats, Snapshot, utc_now
from .protocol import NewlineJsonDecoder, ProtocolError
from .sources import SourceReset, TelemetrySource
from .store import DeviceCapacityError, TelemetryStore

LOGGER = logging.getLogger(__name__)


class TelemetryHub:
    def __init__(self, settings: Settings, source: TelemetrySource) -> None:
        self.settings = settings
        self.source = source
        self.store = TelemetryStore(settings)
        self.alert_engine = AlertEngine(settings)
        self._decoder = NewlineJsonDecoder()
        self._stop = asyncio.Event()
        self._subscribers: set[asyncio.Queue[dict[str, object]]] = set()
        self._started_at = utc_now()
        self._frames_received = 0
        self._frames_accepted = 0
        self._parse_errors = 0
        self._sequence_anomalies = 0
        self._device_rejections = 0
        self._websocket_drops = 0

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stats(self) -> ServiceStats:
        return ServiceStats(
            started_at=self._started_at,
            frames_received=self._frames_received,
            frames_accepted=self._frames_accepted,
            parse_errors=self._parse_errors,
            sequence_anomalies=self._sequence_anomalies,
            device_rejections=self._device_rejections,
            websocket_clients=len(self._subscribers),
            websocket_drops=self._websocket_drops,
            source_reconnects=self.source.reconnects,
            session_changes=self.store.session_changes,
        )

    async def run(self) -> None:
        LOGGER.info("telemetry acquisition started", extra={"source": self.source.name})
        try:
            async for chunk in self.source.chunks(self._stop):
                if isinstance(chunk, SourceReset):
                    self._decoder.reset()
                    continue
                for reading in self._decoder.feed_events(chunk):
                    self._frames_received += 1
                    if isinstance(reading, ProtocolError):
                        self._parse_errors += 1
                        LOGGER.warning("rejected telemetry frame: %s", reading)
                        continue
                    try:
                        in_order = await self.store.add_reading(reading)
                    except DeviceCapacityError:
                        self._device_rejections += 1
                        continue
                    if not in_order:
                        self._sequence_anomalies += 1
                        continue
                    self._frames_accepted += 1
                    alerts = self.alert_engine.evaluate(reading)
                    await self.store.add_alerts(alerts)
                    health = await self.store.health(reading.device_id)
                    await self._broadcast(
                        {
                            "type": "telemetry",
                            "reading": reading.model_dump(mode="json"),
                            "health": (
                                health.model_dump(mode="json") if health else None
                            ),
                            "alerts": [alert.model_dump(mode="json") for alert in alerts],
                        }
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("telemetry source stopped unexpectedly")
            raise
        finally:
            await self.source.close()
            LOGGER.info("telemetry acquisition stopped")

    async def stop(self) -> None:
        self._stop.set()
        await self.source.close()

    async def snapshot(self) -> Snapshot:
        return Snapshot(
            devices=await self.store.all_health(),
            alerts=await self.store.alerts(20),
            stats=self.stats(),
        )

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(
            maxsize=self.settings.subscriber_queue_size
        )
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def _broadcast(self, event: dict[str, object]) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):  # defensive race guard
                    queue.get_nowait()
                    queue.task_done()
                self._websocket_drops += 1
            queue.put_nowait(event)
