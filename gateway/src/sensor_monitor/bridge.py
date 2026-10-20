"""Explicit conversion from FreeRTOS Sensor Hub schema 1 to gateway schema 2."""

from __future__ import annotations

import json
from math import isfinite
from typing import Any

from pydantic import ValidationError

from .models import WireTelemetry
from .protocol import MAX_FRAME_BYTES, ProtocolError

UINT32_MAX = 2**32 - 1
UINT64_MAX = 2**64 - 1


class BridgeError(ProtocolError):
    """A source frame cannot be faithfully represented by the gateway schema."""


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BridgeError(f"{name} must be an object")
    return value


def _uint(value: Any, name: str, maximum: int = UINT32_MAX) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise BridgeError(f"{name} must be an integer between 0 and {maximum}")
    return value


def _number(value: Any, name: str) -> float:
    if type(value) not in (int, float):
        raise BridgeError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise BridgeError(f"{name} is too large to represent") from exc
    if not isfinite(number):
        raise BridgeError(f"{name} must be a finite number")
    return number


def _vector3(value: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        raise BridgeError(f"{name} must be a three-element array")
    return (
        _number(value[0], f"{name}[0]"),
        _number(value[1], f"{name}[1]"),
        _number(value[2], f"{name}[2]"),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise BridgeError(f"duplicate JSON field: {name}")
        result[name] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise BridgeError(f"non-finite JSON number: {value}")


def convert_freertos_frame(raw: bytes, *, device_id: str, boot_id: int) -> WireTelemetry:
    """Convert one bounded UTF-8 JSON record using caller-supplied boot identity.

    The source contains no device ID or reliable boot marker. Callers must split
    captures at known boot boundaries and retain their assigned monotonic boot
    IDs when replaying. This function neither infers reboots nor tracks freshness.
    Missing sensor payloads are valid upstream but cannot form a gateway reading.
    """
    if not isinstance(raw, bytes):
        raise BridgeError("frame must be bytes")
    frame_length = len(raw) - (1 if raw.endswith(b"\n") else 0)
    if frame_length > MAX_FRAME_BYTES:
        raise BridgeError(f"FreeRTOS frame exceeded {MAX_FRAME_BYTES}-byte receive limit")
    raw = raw.removesuffix(b"\n")
    if b"\n" in raw:
        raise BridgeError("expected one LF-delimited source record")
    try:
        payload = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except BridgeError:
        raise
    except (ValueError, RecursionError) as exc:
        raise BridgeError(f"invalid FreeRTOS JSON: {exc}") from exc

    root = _object(payload, "frame")
    if _uint(root.get("schema"), "schema") != 1 or root.get("type") != "telemetry":
        raise BridgeError("expected a FreeRTOS schema-1 telemetry frame")
    sequence = _uint(root.get("seq"), "seq")
    timestamp_us = _uint(root.get("ts_us"), "ts_us", UINT64_MAX)
    sensors = _object(root.get("sensors"), "sensors")
    health = _object(root.get("health"), "health")
    timing = _object(root.get("timing"), "timing")
    for sensor in ("mpu6050", "bmp280"):
        if sensors.get(sensor) not in ("online", "offline"):
            raise BridgeError(f"sensors.{sensor} must be online or offline")
    for counter in ("i2c_retries", "i2c_errors", "queue_drops", "deadline_misses"):
        _uint(health.get(counter), f"health.{counter}")
    if health.get("watchdog") not in ("fed", "withheld"):
        raise BridgeError("health.watchdog must be fed or withheld")
    for duration in ("acquisition_us", "processing_us"):
        _uint(timing.get(duration), f"timing.{duration}")

    for name in ("imu", "baro"):
        if name not in root:
            raise BridgeError(f"missing required field: {name}")
    missing = [name for name in ("imu", "baro") if root[name] is None]
    if missing:
        raise BridgeError(
            f"incomplete sensor frame: {', '.join(missing)} is null; "
            "gateway requires temperature, pressure, and acceleration"
        )
    imu = _object(root["imu"], "imu")
    baro = _object(root["baro"], "baro")
    accel_g = _vector3(imu.get("accel_g"), "imu.accel_g")
    _vector3(imu.get("gyro_dps"), "imu.gyro_dps")
    _number(imu.get("temperature_c"), "imu.temperature_c")
    temperature_c = _number(baro.get("temperature_c"), "baro.temperature_c")
    pressure_pa = _number(baro.get("pressure_pa"), "baro.pressure_pa")
    _number(baro.get("altitude_m"), "baro.altitude_m")
    boot_id = _uint(boot_id, "boot_id", UINT64_MAX)
    try:
        return WireTelemetry.model_validate(
            {
                "schema_version": 2,
                "device_id": device_id,
                "boot_id": boot_id,
                "sequence": sequence,
                "uptime_ms": timestamp_us // 1000,
                "temperature_c": temperature_c,
                "pressure_hpa": pressure_pa / 100,
                "accel_g": accel_g,
                "battery_v": None,
                "i2c_errors": health["i2c_errors"],
                "queue_drops": health["queue_drops"],
                "watchdog_resets": None,
            },
            strict=True,
        )
    except ValidationError as exc:
        raise BridgeError(f"frame is outside gateway schema: {exc}") from exc
