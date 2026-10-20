"""Console plus bounded rotating-file logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings


def configure_logging(settings: Settings) -> None:
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    for handler in tuple(root.handlers):
        if getattr(handler, "_sensor_monitor_handler", False):
            root.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console._sensor_monitor_handler = True  # type: ignore[attr-defined]

    rotating = RotatingFileHandler(
        settings.log_dir / "sensor-monitor.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    rotating.setFormatter(formatter)
    rotating._sensor_monitor_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)
    root.addHandler(rotating)
