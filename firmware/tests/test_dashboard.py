"""Regressions for a dashboard that keeps running through sensor faults."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.dashboard import render, sparkline
from tools.reference_sim import generate_frames
from tools.telemetry import decode_line


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "tools" / "dashboard.py"


class DashboardTests(unittest.TestCase):
    def test_null_imu_renders_during_debounce_and_disconnect(self) -> None:
        for state in ("online", "offline"):
            for barometer_present in (True, False):
                with self.subTest(state=state, barometer_present=barometer_present):
                    payload = next(generate_frames(100))
                    payload["sensors"]["mpu6050"] = state
                    payload["imu"] = None
                    if not barometer_present:
                        payload["baro"] = None
                    output = render(decode_line(json.dumps(payload)))
                    self.assertIn("accel g       n/a", output)
                    self.assertIn("gyro dps      n/a", output)
                    self.assertIn(f"MPU6050 {state.upper()}", output)

    def test_sparkline_handles_extreme_finite_readings(self) -> None:
        self.assertEqual(sparkline([-1e308, 0.0, 1e308]), ".~@")
        self.assertEqual(sparkline([0.0, 1e308]), ".@")

    def test_stdin_replay_skips_bad_bytes_and_survives_sensor_outage(self) -> None:
        outage = next(generate_frames(100, disconnect="all", disconnect_at_ms=0))
        recovered = next(generate_frames(100))
        data = b"boot banner\n\xff\n" + self.encode(outage, recovered)
        result = self.replay("-", data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"FreeRTOS Sensor Hub"), 2)
        self.assertIn(b"MPU6050 OFFLINE", result.stdout)
        self.assertIn(b"MPU6050 ONLINE", result.stdout)
        self.assertIn(b"accel g       n/a", result.stdout)
        self.assertEqual(result.stderr.count(b"ignored malformed frame"), 2)

    def test_file_replay_skips_bad_utf8_line(self) -> None:
        data = b"\xff\n" + self.encode(next(generate_frames(100)))
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.ndjson"
            capture.write_bytes(data)
            result = self.replay(str(capture))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"FreeRTOS Sensor Hub"), 1)
        self.assertIn(b"ignored malformed frame #1", result.stderr)

    def test_max_frames_counts_valid_frames_from_stdin(self) -> None:
        payloads = list(generate_frames(300))
        result = self.replay("-", b"noise\n" + self.encode(*payloads), "--max-frames", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(b"FreeRTOS Sensor Hub"), 2)
        self.assertIn(b"ignored malformed frame #1", result.stderr)

    @staticmethod
    def encode(*payloads: dict) -> bytes:
        return "".join(json.dumps(payload) + "\n" for payload in payloads).encode("utf-8")

    @staticmethod
    def replay(path: str, data: bytes | None = None, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(DASHBOARD), "--file", path, "--no-ansi", *arguments],
            input=data, capture_output=True, timeout=10, cwd=ROOT,
        )


if __name__ == "__main__":
    unittest.main()
