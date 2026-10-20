"""Transition-based alert evaluation to avoid repeating identical alerts at 10 Hz."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .config import Settings
from .models import Alert, AlertSeverity, TelemetryReading


@dataclass(frozen=True, slots=True)
class _Condition:
    code: str
    severity: AlertSeverity
    active: bool
    value: float | int | None
    active_message: str
    recovery_message: str


class AlertEngine:
    """Emit alerts only when a condition changes state."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._active: dict[tuple[str, str], bool] = {}

    def evaluate(self, reading: TelemetryReading) -> list[Alert]:
        conditions = self._conditions(reading)
        emitted: list[Alert] = []
        for condition in conditions:
            key = (reading.device_id, condition.code)
            was_active = self._active.get(key, False)
            if condition.active == was_active:
                continue
            self._active[key] = condition.active
            if (
                condition.code == "temperature_warning"
                and was_active
                and not condition.active
                and reading.temperature_c is not None
                and reading.temperature_c >= self.settings.critical_temperature_c
            ):
                # Escalation is not recovery: emit only the critical transition.
                continue
            emitted.append(
                Alert(
                    id=str(uuid4()),
                    device_id=reading.device_id,
                    code=condition.code,
                    severity=(condition.severity if condition.active else AlertSeverity.INFO),
                    message=(
                        condition.active_message
                        if condition.active
                        else condition.recovery_message
                    ),
                    active=condition.active,
                    observed_value=condition.value,
                    created_at=reading.received_at,
                )
            )
        return emitted

    def _conditions(self, reading: TelemetryReading) -> tuple[_Condition, ...]:
        temperature = reading.temperature_c
        critical = temperature is not None and temperature >= self.settings.critical_temperature_c
        warning = (
            temperature is not None
            and temperature >= self.settings.warning_temperature_c
            and not critical
        )
        battery_value = reading.battery_v if reading.battery_v is not None else 30.0
        conditions = (
            _Condition(
                "temperature_critical",
                AlertSeverity.CRITICAL,
                critical,
                reading.temperature_c,
                f"Temperature reached {temperature} C",
                "Temperature returned below the critical threshold",
            ),
            _Condition(
                "temperature_warning",
                AlertSeverity.WARNING,
                warning,
                reading.temperature_c,
                f"Temperature reached {temperature} C",
                "Temperature returned below the warning threshold",
            ),
            _Condition(
                "battery_low",
                AlertSeverity.WARNING,
                reading.battery_v is not None and battery_value < self.settings.low_battery_v,
                battery_value,
                f"Battery voltage fell to {battery_value:.2f} V",
                "Battery voltage recovered",
            ),
            _Condition(
                "i2c_errors",
                AlertSeverity.WARNING,
                "i2c_errors" in reading.current_faults
                if reading.firmware
                else reading.i2c_errors > 0,
                reading.i2c_errors,
                f"Device reports {reading.i2c_errors} I2C errors",
                "No new I2C errors in the recovery window"
                if reading.firmware
                else "Device I2C error counter returned to zero",
            ),
            _Condition(
                "queue_drops",
                AlertSeverity.WARNING,
                "queue_drops" in reading.current_faults
                if reading.firmware
                else reading.queue_drops > 0,
                reading.queue_drops,
                f"Device reports {reading.queue_drops} telemetry queue drops",
                "No new queue drops in the recovery window"
                if reading.firmware
                else "Device queue-drop counter returned to zero",
            ),
        )
        if temperature is None or "sample_stale" in reading.current_faults:
            # Missing/stale measurements cannot clear an earlier temperature alarm.
            conditions = tuple(c for c in conditions if not c.code.startswith("temperature_"))
        if reading.firmware:
            descriptions = {
                "imu_unavailable": "IMU data unavailable",
                "barometer_unavailable": "Barometer data unavailable",
                "sample_stale": "Measurements stopped advancing",
                "task_health": "Firmware task health check failed",
                "deadline_misses": "New firmware deadline misses",
            }
            conditions += tuple(
                _Condition(
                    code,
                    AlertSeverity.WARNING,
                    code in reading.current_faults,
                    None,
                    description,
                    f"Recovered: {description}",
                )
                for code, description in descriptions.items()
                if code != "task_health" or reading.firmware.uptime_ms > 1000
            )
        return conditions
