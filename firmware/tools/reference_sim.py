"""Dependency-free deterministic protocol generator for dashboard demos/tests.

This model exercises host tooling when a C toolchain is unavailable. The C
simulator remains the executable model of the firmware's drivers and counters.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterator
from typing import Any


def generate_frames(
    duration_ms: int = 3000,
    *,
    disconnect: str = "none",
    disconnect_at_ms: int = 2**32 - 1,
    i2c_nack_every: int = 0,
    queue_drop_every: int = 0,
) -> Iterator[dict[str, Any]]:
    """Yield deterministic 10 Hz frames representing a 100 Hz producer."""
    if duration_ms < 0:
        raise ValueError("duration_ms must be non-negative")
    if disconnect not in {"none", "mpu", "bmp", "all"}:
        raise ValueError("disconnect must be none, mpu, bmp, or all")
    retries = errors = drops = 0
    for timestamp_ms in range(100, duration_ms + 1, 100):
        sequence = timestamp_ms // 10 - 1
        disconnected = timestamp_ms >= disconnect_at_ms + 20
        mpu_online = not (disconnected and disconnect in {"mpu", "all"})
        bmp_online = not (disconnected and disconnect in {"bmp", "all"})
        if i2c_nack_every:
            retries += max(1, 20 // i2c_nack_every)
        if disconnected:
            errors += int(not mpu_online) * 10 + int(not bmp_online) * 10
        if queue_drop_every and sequence and sequence % queue_drop_every == 0:
            drops += 1
        phase = sequence % 200 - 100
        imu = None
        if mpu_online:
            imu = {
                "accel_g": [round(phase * 25 / 16384, 4), round(-phase * 18 / 16384, 4), round((16384 + phase * 2) / 16384, 4)],
                "gyro_dps": [round(phase * 3 / 131, 3), round(-phase * 2 / 131, 3), round(phase / 131, 3)],
                "temperature_c": round((-3920 + phase) / 340 + 36.53, 2),
            }
        baro = None
        if bmp_online:
            pressure = 100653.0 + (sequence % 7 - 3)
            baro = {
                "temperature_c": 25.08,
                "pressure_pa": pressure,
                "altitude_m": round(44330.0 * (1.0 - math.pow(pressure / 101325.0, 0.19029495)), 2),
            }
        yield {
            "schema": 1,
            "type": "telemetry",
            "seq": sequence,
            "ts_us": timestamp_ms * 1000,
            "sensors": {"mpu6050": "online" if mpu_online else "offline", "bmp280": "online" if bmp_online else "offline"},
            "imu": imu,
            "baro": baro,
            "health": {
                "i2c_retries": retries,
                "i2c_errors": errors,
                "queue_drops": drops,
                "deadline_misses": 0,
                "watchdog": "fed" if timestamp_ms >= 1000 else "withheld",
            },
            "timing": {"acquisition_us": 82 + sequence % 11, "processing_us": 31 + sequence % 7},
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-ms", type=int, default=3000)
    parser.add_argument("--disconnect", choices=("none", "mpu", "bmp", "all"), default="none")
    parser.add_argument("--disconnect-at-ms", type=int, default=2**32 - 1)
    parser.add_argument("--i2c-nack-every", type=int, default=0)
    parser.add_argument("--queue-drop-every", type=int, default=0)
    args = parser.parse_args()
    for frame in generate_frames(**vars(args)):
        print(json.dumps(frame, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
