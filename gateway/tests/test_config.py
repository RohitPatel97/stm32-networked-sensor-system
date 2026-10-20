from __future__ import annotations

import pytest

from sensor_monitor.config import Settings


@pytest.mark.parametrize(
    "values",
    [
        {"source": "network"},
        {"port": 0},
        {"simulator_rate_hz": 0},
        {"history_size": 0},
        {"max_devices": 0},
        {"alert_history_size": 0},
        {"simulator_rate_hz": float("nan")},
        {"warning_temperature_c": 90, "critical_temperature_c": 80},
        {"baud_rate": 0},
        {"stale_after_seconds": 5, "offline_after_seconds": 2},
        {"source": "replay"},
    ],
)
def test_invalid_settings_fail_early(values: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Settings(**values)


def test_environment_overrides_are_typed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SENSOR_MONITOR_PORT", "8123")
    monkeypatch.setenv("SENSOR_MONITOR_REPLAY_LOOP", "false")
    monkeypatch.setenv("SENSOR_MONITOR_LOG_DIR", str(tmp_path))
    settings = Settings.from_env()
    assert settings.port == 8123
    assert settings.replay_loop is False
    assert settings.log_dir == tmp_path
