from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from sensor_monitor.config import Settings
from sensor_monitor.models import DeviceState
from sensor_monitor.store import DeviceCapacityError, TelemetryStore

from .helpers import reading


def test_history_is_bounded_and_sequence_anomalies_are_detected() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings(history_size=2))
        assert await store.add_reading(reading(sequence=1))
        assert await store.add_reading(reading(sequence=2))
        assert not await store.add_reading(reading(sequence=2))
        assert await store.add_reading(reading(sequence=3))
        assert [item.sequence for item in await store.readings("stm32-sensor-hub", 10)] == [2, 3]

    asyncio.run(scenario())


@pytest.mark.parametrize("rejected_sequence", [100, 99, 100 + 0x80000000])
def test_rejected_sequence_cannot_refresh_health_or_replace_history(rejected_sequence) -> None:
    async def scenario() -> None:
        observed = datetime(2026, 1, 1, tzinfo=UTC)
        store = TelemetryStore(Settings(stale_after_seconds=2, offline_after_seconds=5))
        await store.add_reading(reading(sequence=100, received_at=observed))
        assert not await store.add_reading(reading(
            sequence=rejected_sequence, temperature_c=100,
            received_at=observed + timedelta(seconds=6),
        ))
        health = await store.health("stm32-sensor-hub", now=observed + timedelta(seconds=6))
        assert health and health.state is DeviceState.OFFLINE
        assert health.last_sequence == 100
        assert health.last_seen == observed
        assert len(await store.readings("stm32-sensor-hub", 10)) == 1
        assert await store.add_reading(reading(sequence=101))

    asyncio.run(scenario())


def test_uint32_rollover_is_fresh_but_old_pre_wrap_frame_is_rejected() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings())
        for sequence in [0xFFFFFFFE, 0xFFFFFFFF, 0, 1]:
            assert await store.add_reading(reading(sequence=sequence))
        assert not await store.add_reading(reading(sequence=0xFFFFFFFF))
        assert [item.sequence for item in await store.readings("stm32-sensor-hub", 10)] == [
            0xFFFFFFFE, 0xFFFFFFFF, 0, 1,
        ]

    asyncio.run(scenario())


def test_sequence_baselines_are_independent_per_device() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings())
        assert await store.add_reading(reading(device_id="first", sequence=100))
        assert await store.add_reading(reading(device_id="second", sequence=0))
        assert not await store.add_reading(reading(device_id="first", sequence=99))
        assert await store.add_reading(reading(device_id="second", sequence=1))

    asyncio.run(scenario())


def test_device_count_is_bounded_without_rejecting_existing_device() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings(max_devices=1))
        await store.add_reading(reading())
        with pytest.raises(DeviceCapacityError):
            await store.add_reading(reading(device_id="second-node"))
        assert await store.add_reading(reading(sequence=43))
        assert await store.device_ids() == ["stm32-sensor-hub"]

    asyncio.run(scenario())


def test_health_transitions_from_online_to_stale_to_offline() -> None:
    async def scenario() -> None:
        settings = Settings(stale_after_seconds=2, offline_after_seconds=5)
        store = TelemetryStore(settings)
        observed = datetime(2026, 1, 1, tzinfo=UTC)
        await store.add_reading(reading(received_at=observed))
        online = await store.health("stm32-sensor-hub", now=observed)
        stale = await store.health(
            "stm32-sensor-hub", now=observed + timedelta(seconds=3)
        )
        offline = await store.health(
            "stm32-sensor-hub", now=observed + timedelta(seconds=6)
        )
        assert online and online.state is DeviceState.ONLINE
        assert stale and stale.state is DeviceState.STALE
        assert offline and offline.state is DeviceState.OFFLINE

    asyncio.run(scenario())
