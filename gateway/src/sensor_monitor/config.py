"""Typed configuration loaded from SENSOR_MONITOR_* environment variables."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from os import environ
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    source: str = "simulator"
    serial_port: str = "/dev/ttyUSB0"
    baud_rate: int = 115_200
    replay_file: Path | None = None
    simulator_rate_hz: float = 10.0
    simulator_seed: int = 7
    replay_loop: bool = True
    history_size: int = 600
    max_devices: int = 32
    alert_history_size: int = 200
    subscriber_queue_size: int = 64
    stale_after_seconds: float = 3.0
    offline_after_seconds: float = 10.0
    warning_temperature_c: float = 60.0
    critical_temperature_c: float = 80.0
    low_battery_v: float = 10.8
    log_dir: Path = Path("var/log")
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.source not in {"simulator", "serial", "replay"}:
            raise ValueError("source must be simulator, serial, or replay")
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if not isfinite(self.simulator_rate_hz) or self.simulator_rate_hz <= 0:
            raise ValueError("simulator_rate_hz must be positive")
        if min(
            self.history_size, self.alert_history_size,
            self.subscriber_queue_size, self.max_devices,
        ) <= 0:
            raise ValueError("queue and history sizes must be positive")
        if not 0 < self.stale_after_seconds < self.offline_after_seconds:
            raise ValueError("stale timeout must be positive and below offline timeout")
        if self.source == "replay" and self.replay_file is None:
            raise ValueError("replay_file is required when source=replay")
        if not (
            isfinite(self.warning_temperature_c)
            and isfinite(self.critical_temperature_c)
            and self.warning_temperature_c < self.critical_temperature_c
        ):
            raise ValueError("temperature thresholds must be finite and increasing")
        if self.baud_rate <= 0 or not 0 <= self.low_battery_v <= 30:
            raise ValueError("invalid baud rate or low-battery threshold")

    @classmethod
    def from_env(cls) -> Settings:
        defaults = cls()
        replay = environ.get("SENSOR_MONITOR_REPLAY_FILE")
        return cls(
            host=environ.get("SENSOR_MONITOR_HOST", defaults.host),
            port=int(environ.get("SENSOR_MONITOR_PORT", defaults.port)),
            source=environ.get("SENSOR_MONITOR_SOURCE", defaults.source).strip().lower(),
            serial_port=environ.get("SENSOR_MONITOR_SERIAL_PORT", defaults.serial_port),
            baud_rate=int(environ.get("SENSOR_MONITOR_BAUD_RATE", defaults.baud_rate)),
            replay_file=Path(replay) if replay else None,
            simulator_rate_hz=float(
                environ.get("SENSOR_MONITOR_SIMULATOR_RATE_HZ", defaults.simulator_rate_hz)
            ),
            simulator_seed=int(
                environ.get("SENSOR_MONITOR_SIMULATOR_SEED", defaults.simulator_seed)
            ),
            replay_loop=_env_bool("SENSOR_MONITOR_REPLAY_LOOP", defaults.replay_loop),
            history_size=int(environ.get("SENSOR_MONITOR_HISTORY_SIZE", defaults.history_size)),
            max_devices=int(environ.get("SENSOR_MONITOR_MAX_DEVICES", defaults.max_devices)),
            alert_history_size=int(
                environ.get("SENSOR_MONITOR_ALERT_HISTORY_SIZE", defaults.alert_history_size)
            ),
            subscriber_queue_size=int(
                environ.get(
                    "SENSOR_MONITOR_SUBSCRIBER_QUEUE_SIZE", defaults.subscriber_queue_size
                )
            ),
            stale_after_seconds=float(
                environ.get("SENSOR_MONITOR_STALE_AFTER_SECONDS", defaults.stale_after_seconds)
            ),
            offline_after_seconds=float(
                environ.get("SENSOR_MONITOR_OFFLINE_AFTER_SECONDS", defaults.offline_after_seconds)
            ),
            warning_temperature_c=float(
                environ.get(
                    "SENSOR_MONITOR_WARNING_TEMPERATURE_C",
                    defaults.warning_temperature_c,
                )
            ),
            critical_temperature_c=float(
                environ.get(
                    "SENSOR_MONITOR_CRITICAL_TEMPERATURE_C",
                    defaults.critical_temperature_c,
                )
            ),
            low_battery_v=float(
                environ.get("SENSOR_MONITOR_LOW_BATTERY_V", defaults.low_battery_v)
            ),
            log_dir=Path(environ.get("SENSOR_MONITOR_LOG_DIR", str(defaults.log_dir))),
            log_level=environ.get("SENSOR_MONITOR_LOG_LEVEL", defaults.log_level).upper(),
        )
