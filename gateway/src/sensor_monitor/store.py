"""Bounded, concurrency-safe in-memory telemetry and alert store."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime

from .config import Settings
from .models import Alert, DeviceHealth, DeviceState, TelemetryReading, utc_now


class DeviceCapacityError(ValueError):
    """The configured device-count memory limit has been reached."""


class TelemetryStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._readings: dict[str, deque[TelemetryReading]] = defaultdict(
            lambda: deque(maxlen=settings.history_size)
        )
        self._alerts: deque[Alert] = deque(maxlen=settings.alert_history_size)
        self._lock = asyncio.Lock()
        self._session_changes = 0
        self._counter_activity: dict[str, dict[str, int]] = {}

    @property
    def session_changes(self) -> int:
        return self._session_changes

    async def add_reading(self, reading: TelemetryReading) -> bool:
        """Store fresh samples using an ordered boot counter and uint32 sequence.

        Duplicate, backward, and ambiguous half-range jumps do not refresh health,
        replace history, or change the accepted sequence baseline.
        """
        async with self._lock:
            if (
                reading.device_id not in self._readings
                and len(self._readings) >= self.settings.max_devices
            ):
                raise DeviceCapacityError("device limit reached")
            history = self._readings[reading.device_id]
            if history:
                previous = history[-1]
                if previous.schema_version == 3 and reading.schema_version != 3:
                    return False
                if reading.boot_id == previous.boot_id:
                    delta = (reading.sequence - previous.sequence) & 0xFFFFFFFF
                    if not 0 < delta < 0x80000000:
                        return False
                    if reading.firmware and previous.firmware:
                        current, prior = reading.firmware, previous.firmware
                        if current.uptime_ms < prior.uptime_ms:
                            return False
                        if prior.sample_present:
                            if not current.sample_present or current.ts_us < prior.ts_us:
                                return False
                            sample_delta = (current.seq - prior.seq) & 0xFFFFFFFF
                            if sample_delta == 0:
                                if (
                                    current.ts_us != prior.ts_us
                                    or current.imu != prior.imu
                                    or current.baro != prior.baro
                                    or current.timing != prior.timing
                                ):
                                    return False
                            elif sample_delta >= 0x80000000:
                                return False
                else:
                    # A legacy downgrade or older boot can never replace a v2
                    # baseline. A newer boot (or first v2 frame) starts a sequence.
                    if reading.boot_id is None or (
                        previous.boot_id is not None and reading.boot_id < previous.boot_id
                    ):
                        return False
                    self._session_changes += 1
            if reading.firmware:
                self._update_current_faults(reading, history[-1] if history else None)
            history.append(reading)
            return True

    def _update_current_faults(
        self, reading: TelemetryReading, previous: TelemetryReading | None
    ) -> None:
        frame = reading.firmware
        assert frame is not None
        prior = previous.firmware if previous and previous.boot_id == reading.boot_id else None
        if prior is None:
            self._counter_activity[reading.device_id] = {}
        activity = self._counter_activity[reading.device_id]
        faults = []
        # One second of *producer time* after a new event, independent of replay speed.
        # An initial nonzero lifetime total is historical, not proof of a current fault.
        for name in ("i2c_errors", "queue_drops", "deadline_misses"):
            if prior is not None:
                delta = (getattr(frame.health, name) - getattr(prior.health, name)) & 0xFFFFFFFF
                if 0 < delta < 0x80000000:
                    activity[name] = frame.uptime_ms
            if name in activity and frame.uptime_ms - activity[name] < 1000:
                faults.append(name)
        if frame.imu is None or frame.sensors.mpu6050 == "offline":
            faults.append("imu_unavailable")
        if frame.baro is None or frame.sensors.bmp280 == "offline":
            faults.append("barometer_unavailable")
        if (
            not frame.sample_present
            or (frame.uptime_ms * 1000 - frame.ts_us) / 1_000_000
            >= self.settings.stale_after_seconds
        ):
            faults.append("sample_stale")
        # The first 1 Hz health task runs below telemetry priority. A frame at
        # 1001 ms can precede that first check. Report an observed miss at once,
        # but allow two periods before treating an unobserved check as failure.
        if (
            frame.uptime_ms > 1000
            and frame.health.watchdog == "withheld"
            and (frame.health.deadline_misses > 0 or frame.uptime_ms > 2000)
        ):
            faults.append("task_health")
        reading.current_faults = faults

    async def add_alerts(self, alerts: list[Alert]) -> None:
        async with self._lock:
            self._alerts.extend(alerts)

    async def readings(self, device_id: str, limit: int) -> list[TelemetryReading]:
        async with self._lock:
            history = self._readings.get(device_id)
            if not history:
                return []
            return list(history)[-limit:]

    async def alerts(self, limit: int) -> list[Alert]:
        async with self._lock:
            return list(self._alerts)[-limit:][::-1]

    async def device_ids(self) -> list[str]:
        async with self._lock:
            return sorted(self._readings)

    async def health(self, device_id: str, *, now: datetime | None = None) -> DeviceHealth | None:
        async with self._lock:
            history = self._readings.get(device_id)
            if not history:
                return None
            latest = history[-1]
        return self._health_from(latest, now=now or utc_now())

    async def all_health(self, *, now: datetime | None = None) -> list[DeviceHealth]:
        async with self._lock:
            latest = [history[-1] for history in self._readings.values() if history]
        instant = now or utc_now()
        return sorted(
            (self._health_from(reading, now=instant) for reading in latest),
            key=lambda health: health.device_id,
        )

    def _health_from(self, reading: TelemetryReading, *, now: datetime) -> DeviceHealth:
        age = max(0.0, (now - reading.received_at).total_seconds())
        firmware = reading.firmware
        sample_age = (
            age + (firmware.uptime_ms * 1000 - firmware.ts_us) / 1_000_000
            if firmware and firmware.sample_present
            else None
        )
        measurement_state = (
            "unavailable"
            if firmware
            and (not firmware.sample_present or (firmware.imu is None and firmware.baro is None))
            else "stale"
            if sample_age is not None and sample_age >= self.settings.stale_after_seconds
            else "current"
        )
        if age >= self.settings.offline_after_seconds:
            state, detail = DeviceState.OFFLINE, "telemetry timeout exceeded"
        elif age >= self.settings.stale_after_seconds:
            state, detail = DeviceState.STALE, "telemetry is stale"
        elif (
            (
                bool(reading.current_faults) or measurement_state != "current"
                if firmware
                else bool(reading.watchdog_resets or reading.i2c_errors or reading.queue_drops)
            )
            or (
                reading.temperature_c is not None
                and reading.temperature_c >= self.settings.warning_temperature_c
            )
            or (reading.battery_v is not None and reading.battery_v < self.settings.low_battery_v)
        ):
            state, detail = DeviceState.DEGRADED, "device reports a fault condition"
        else:
            state, detail = DeviceState.ONLINE, "telemetry is current"
        return DeviceHealth(
            device_id=reading.device_id,
            state=state,
            last_seen=reading.received_at,
            age_seconds=round(age, 3),
            last_sequence=reading.sequence,
            boot_id=reading.boot_id,
            i2c_errors=reading.i2c_errors,
            queue_drops=reading.queue_drops,
            watchdog_resets=reading.watchdog_resets,
            detail=detail,
            boot_id_decimal=str(reading.boot_id) if reading.boot_id is not None else None,
            sample_age_seconds=round(sample_age, 3) if sample_age is not None else None,
            measurement_state=measurement_state,
            mpu6050=firmware.sensors.mpu6050 if firmware else None,
            bmp280=firmware.sensors.bmp280 if firmware else None,
        )
