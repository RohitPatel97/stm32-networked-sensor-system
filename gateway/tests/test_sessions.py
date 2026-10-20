from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from sensor_monitor.config import Settings
from sensor_monitor.hub import TelemetryHub
from sensor_monitor.models import DeviceState
from sensor_monitor.protocol import FrameDecodeError, parse_frame
from sensor_monitor.store import TelemetryStore

from .helpers import reading
from .test_hub import CaptureSource


def frame(*, boot_id=10, sequence=0, **overrides):
    payload = reading(sequence=sequence).model_dump(exclude={"received_at"})
    payload.update(schema_version=2, boot_id=boot_id, **overrides)
    return json.dumps(payload).encode()


@pytest.mark.parametrize("boot_id", [0, 10, 2**64 - 1])
def test_v2_accepts_explicit_boot_counter(boot_id) -> None:
    sample = parse_frame(frame(boot_id=boot_id))
    assert sample.schema_version == 2
    assert sample.boot_id == boot_id


@pytest.mark.parametrize("boot_id", [None, True, "10", -1, 2**64, 1.5])
def test_v2_rejects_invalid_boot_counter(boot_id) -> None:
    with pytest.raises(FrameDecodeError):
        parse_frame(frame(boot_id=boot_id))


def test_v2_requires_boot_counter_and_v1_cannot_claim_one() -> None:
    payload = json.loads(frame())
    del payload["boot_id"]
    with pytest.raises(FrameDecodeError):
        parse_frame(json.dumps(payload).encode())
    payload.update(schema_version=1, boot_id=10)
    with pytest.raises(FrameDecodeError):
        parse_frame(json.dumps(payload).encode())


def test_unknown_watchdog_count_is_explicit_in_v2_only() -> None:
    assert parse_frame(frame(watchdog_resets=None)).watchdog_resets is None
    payload = json.loads(frame(watchdog_resets=None))
    payload.update(schema_version=1)
    payload.pop("boot_id")
    with pytest.raises(FrameDecodeError):
        parse_frame(json.dumps(payload).encode())


def test_reboot_accepts_new_baseline_but_rejects_old_sessions_and_downgrade() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings(history_size=2))
        assert await store.add_reading(parse_frame(frame(boot_id=10, sequence=100)))
        assert await store.add_reading(parse_frame(frame(boot_id=11, sequence=0)))
        assert not await store.add_reading(parse_frame(frame(boot_id=10, sequence=101)))
        assert not await store.add_reading(parse_frame(frame(boot_id=11, sequence=0)))
        assert not await store.add_reading(reading(sequence=1000))
        assert await store.add_reading(parse_frame(frame(boot_id=11, sequence=1)))
        assert [(r.boot_id, r.sequence) for r in await store.readings("stm32-sensor-hub", 10)] == [
            (11, 0), (11, 1),
        ]
        assert store.session_changes == 1

    asyncio.run(scenario())


def test_session_policy_preserves_rollover_and_device_independence() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings())
        for seq in [0xFFFFFFFE, 0xFFFFFFFF, 0, 1]:
            assert await store.add_reading(parse_frame(frame(sequence=seq)))
        for seq in [1, 0xFFFFFFFF, 1 + 0x80000000]:
            assert not await store.add_reading(parse_frame(frame(sequence=seq)))
        assert await store.add_reading(parse_frame(frame(boot_id=0, device_id="second")))
        assert store.session_changes == 0

    asyncio.run(scenario())


def test_legacy_migration_and_gateway_restart_have_explicit_baselines() -> None:
    async def scenario() -> None:
        store = TelemetryStore(Settings())
        assert await store.add_reading(reading(sequence=900))
        assert await store.add_reading(parse_frame(frame(boot_id=0, sequence=0)))
        assert store.session_changes == 1
        restarted = TelemetryStore(Settings())
        assert await restarted.add_reading(parse_frame(frame(boot_id=0, sequence=0)))
        assert restarted.session_changes == 0

    asyncio.run(scenario())


def test_stale_boot_cannot_refresh_health_or_raise_faults() -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 12, tzinfo=UTC)
        store = TelemetryStore(Settings())
        current = parse_frame(frame(boot_id=11), received_at=now)
        assert await store.add_reading(current)
        assert not await store.add_reading(parse_frame(
            frame(boot_id=10, sequence=100, temperature_c=100),
            received_at=now + timedelta(seconds=20),
        ))
        health = await store.health(current.device_id, now=now + timedelta(seconds=20))
        assert health and health.state is DeviceState.OFFLINE
        assert health.boot_id == 11
        assert health.last_seen == now
        hub = TelemetryHub(Settings(), CaptureSource([
            frame(boot_id=10, sequence=99, temperature_c=100) + b"\n",
            frame(boot_id=11, sequence=0, temperature_c=100) + b"\n",
            frame(boot_id=10, sequence=100, temperature_c=24) + b"\n",
            frame(boot_id=11, sequence=1, temperature_c=24) + b"\n",
        ]))
        async with hub.subscribe() as queue:
            await hub.run()
            assert hub.stats().frames_accepted == 3
            assert hub.stats().sequence_anomalies == 1
            assert hub.stats().session_changes == 1
            assert queue.qsize() == 3
            alerts = await hub.store.alerts(10)
            assert [a.active for a in alerts] == [False, True]
            assert all(a.code == "temperature_critical" for a in alerts)

    asyncio.run(scenario())
