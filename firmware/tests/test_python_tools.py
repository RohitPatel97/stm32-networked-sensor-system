from __future__ import annotations

import json
import unittest

from tools.dashboard import render, sparkline
from tools.reference_sim import generate_frames
from tools.telemetry import TelemetryError, decode_line, decode_stream


class TelemetryTests(unittest.TestCase):
    def test_nominal_reference_frame_round_trips(self) -> None:
        payload = next(generate_frames(100))
        frame = decode_line(json.dumps(payload))
        self.assertEqual(frame.sequence, 9)
        self.assertTrue(frame.mpu6050_online)
        self.assertAlmostEqual(frame.baro["temperature_c"], 25.08)  # type: ignore[index]
        self.assertIn("FreeRTOS Sensor Hub", render(frame, [100650.0, 100651.0]))

    def test_parser_rejects_wrong_schema_and_bad_counter(self) -> None:
        payload = next(generate_frames(100))
        payload["schema"] = 2
        with self.assertRaises(TelemetryError):
            decode_line(json.dumps(payload))
        payload["schema"] = 1
        payload["health"]["i2c_errors"] = -1
        with self.assertRaises(TelemetryError):
            decode_line(json.dumps(payload))
        payload["health"]["i2c_errors"] = 0
        payload["imu"]["accel_g"] = [1.0, 2.0]
        with self.assertRaises(TelemetryError):
            decode_line(json.dumps(payload))
        with self.assertRaises(TelemetryError):
            decode_line('{"schema":1,"type":"telemetry","seq":NaN}')

    def test_disconnect_and_counters_are_observable(self) -> None:
        frames = list(generate_frames(1000, disconnect="bmp", disconnect_at_ms=500,
                                      i2c_nack_every=5, queue_drop_every=29))
        self.assertEqual(len(frames), 10)
        last = decode_line(json.dumps(frames[-1]))
        self.assertFalse(last.bmp280_online)
        self.assertTrue(last.mpu6050_online)
        self.assertGreater(last.i2c_retries, 0)
        self.assertGreater(last.i2c_errors, 0)
        self.assertGreater(last.queue_drops, 0)

    def test_stream_can_skip_noise(self) -> None:
        valid = json.dumps(next(generate_frames(100)))
        frames = list(decode_stream(["debug text", valid], ignore_invalid=True))
        self.assertEqual(len(frames), 1)

    def test_sparkline_is_bounded(self) -> None:
        self.assertEqual(sparkline([]), "-")
        self.assertEqual(len(sparkline([1.0, 2.0, 3.0])), 3)


if __name__ == "__main__":
    unittest.main()
