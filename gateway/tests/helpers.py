from __future__ import annotations

from datetime import UTC, datetime

from sensor_monitor.models import TelemetryReading


def reading(**overrides: object) -> TelemetryReading:
    values: dict[str, object] = {
        "device_id": "stm32-sensor-hub",
        "sequence": 42,
        "uptime_ms": 4_200,
        "temperature_c": 24.5,
        "pressure_hpa": 1013.25,
        "accel_g": (0.0, 0.0, 1.0),
        "battery_v": 12.4,
        "i2c_errors": 0,
        "queue_drops": 0,
        "watchdog_resets": 0,
        "received_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return TelemetryReading(**values)
