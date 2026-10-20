"""Shared STM32 wire contract. JSON Schema is exported to protocol/ by the tools.

The nested wire record preserves firmware diagnostics. The API uses an explicit
normalized projection; no offline conversion is involved in v3 ingestion.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

U32 = Annotated[int, Field(ge=0, le=2**32 - 1, strict=True)]
U64 = Annotated[int, Field(ge=0, le=2**64 - 1, strict=True)]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Acceleration = Annotated[float, Field(ge=-16, le=16, allow_inf_nan=False)]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SensorStatus(Record):
    mpu6050: Literal["online", "offline"]
    bmp280: Literal["online", "offline"]


class Imu(Record):
    accel_g: tuple[Acceleration, Acceleration, Acceleration]
    gyro_dps: tuple[Finite, Finite, Finite]
    temperature_c: Finite


class Barometer(Record):
    temperature_c: Annotated[float, Field(ge=-50, le=150, allow_inf_nan=False)]
    pressure_pa: Annotated[float, Field(ge=30_000, le=120_000, allow_inf_nan=False)]
    altitude_m: Finite


class FirmwareHealth(Record):
    i2c_retries: U32
    i2c_errors: U32
    queue_drops: U32
    deadline_misses: U32
    watchdog: Literal["fed", "withheld"]
    watchdog_enabled: bool
    reset_flags: U32


class Timing(Record):
    acquisition_us: U32
    processing_us: U32


class UnifiedFrame(Record):
    schema_version: Literal[3]
    type: Literal["telemetry"]
    device_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")]
    boot_id: U64
    frame_seq: U32
    uptime_ms: U64
    seq: U32
    ts_us: U64
    sample_present: bool
    sensors: SensorStatus
    imu: Imu | None
    baro: Barometer | None
    health: FirmwareHealth
    timing: Timing

    @field_validator("schema_version", mode="before")
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("schema_version must be an integer")
        return value

    @model_validator(mode="after")
    def sample_is_possible(self) -> Self:
        if self.ts_us > self.uptime_ms * 1000:
            raise ValueError("sample timestamp cannot exceed transmission uptime")
        if not self.sample_present and (
            self.imu is not None or self.baro is not None or self.seq != 0 or self.ts_us != 0
        ):
            raise ValueError("absent sample requires null payloads and zero sample identity")
        return self

    def normalized(self) -> dict:
        return {
            "schema_version": 3,
            "device_id": self.device_id,
            "boot_id": self.boot_id,
            "sequence": self.frame_seq,
            "uptime_ms": self.uptime_ms,
            "temperature_c": self.baro.temperature_c if self.baro else None,
            "pressure_hpa": self.baro.pressure_pa / 100 if self.baro else None,
            "accel_g": self.imu.accel_g if self.imu else None,
            "battery_v": None,
            "watchdog_resets": None,
            "i2c_errors": self.health.i2c_errors,
            "queue_drops": self.health.queue_drops,
            "firmware": self,
        }
