"""Networked embedded sensor monitoring service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("stm32-sensor-gateway")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0+local"

__all__ = ["__version__"]
