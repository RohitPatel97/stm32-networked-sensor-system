from __future__ import annotations

import json
import sys
import unittest

from tools.reference_sim import generate_frames
from tools.telemetry import TelemetryError, decode_line, decode_stream


UNSIGNED_FIELDS = (
    (None, "seq", 32),
    (None, "ts_us", 64),
    ("health", "i2c_retries", 32),
    ("health", "i2c_errors", 32),
    ("health", "queue_drops", 32),
    ("health", "deadline_misses", 32),
    ("timing", "acquisition_us", 32),
    ("timing", "processing_us", 32),
)


def nominal_line() -> str:
    return json.dumps(next(generate_frames(100)))


def oversized_json_integer() -> str:
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    return "9" * max(5000, limit + 1)


def deeply_nested_json() -> str:
    # CPython's C decoder can use a nesting limit distinct from Python's limit.
    depth = max(10000, sys.getrecursionlimit() * 2)
    return "[" * depth + "0" + "]" * depth


class TelemetryParserTests(unittest.TestCase):
    def test_schema_requires_integer_one(self) -> None:
        for schema in (True, False, 1.0, "1", None, 0, 2):
            with self.subTest(schema=repr(schema)):
                payload = json.loads(nominal_line())
                payload["schema"] = schema
                with self.assertRaises(TelemetryError):
                    decode_line(json.dumps(payload))
        self.assertEqual(decode_line(nominal_line()).raw["schema"], 1)

    def test_sensor_payload_keys_are_required(self) -> None:
        for name in ("imu", "baro"):
            with self.subTest(payload=name):
                payload = json.loads(nominal_line())
                del payload[name]
                with self.assertRaisesRegex(TelemetryError, name):
                    decode_line(json.dumps(payload))

    def test_explicit_null_payloads_preserve_debounced_health(self) -> None:
        for state in ("online", "offline"):
            with self.subTest(state=state):
                payload = json.loads(nominal_line())
                payload["imu"] = payload["baro"] = None
                payload["sensors"] = {"mpu6050": state, "bmp280": state}
                frame = decode_line(json.dumps(payload))
                self.assertIsNone(frame.imu)
                self.assertIsNone(frame.baro)
                self.assertEqual(frame.mpu6050_online, state == "online")
                self.assertEqual(frame.bmp280_online, state == "online")

    def test_invalid_sensor_numbers_raise_telemetry_error(self) -> None:
        fields = (
            ("imu", "accel_g", 0),
            ("imu", "gyro_dps", 2),
            ("imu", "temperature_c", None),
            ("baro", "temperature_c", None),
            ("baro", "pressure_pa", None),
            ("baro", "altitude_m", None),
        )
        for sensor, field, index in fields:
            for value in (10**400, -(10**400), True, None, "1", float("nan"),
                          float("inf"), -float("inf")):
                with self.subTest(sensor=sensor, field=field, value=repr(value)[:40]):
                    payload = json.loads(nominal_line())
                    if index is None:
                        payload[sensor][field] = value
                    else:
                        payload[sensor][field][index] = value
                    with self.assertRaises(TelemetryError):
                        decode_line(json.dumps(payload))

    def test_decoder_integer_limit_raises_telemetry_error(self) -> None:
        with self.assertRaises(TelemetryError):
            decode_line(oversized_json_integer())

    def test_decoder_recursion_limit_raises_telemetry_error(self) -> None:
        with self.assertRaises(TelemetryError):
            decode_line(deeply_nested_json())

    def test_unsigned_fields_reject_out_of_range_values(self) -> None:
        for section, field, bits in UNSIGNED_FIELDS:
            for value in (-1, 1 << bits, 10**400):
                with self.subTest(field=field, value=repr(value)[:40]):
                    payload = json.loads(nominal_line())
                    container = payload[section] if section else payload
                    container[field] = value
                    with self.assertRaisesRegex(TelemetryError, field):
                        decode_line(json.dumps(payload))

    def test_unsigned_wire_width_boundaries_round_trip(self) -> None:
        for boundary in ("maximum", "zero"):
            with self.subTest(boundary=boundary):
                payload = json.loads(nominal_line())
                for section, field, bits in UNSIGNED_FIELDS:
                    container = payload[section] if section else payload
                    container[field] = (1 << bits) - 1 if boundary == "maximum" else 0
                self.assertEqual(decode_line(json.dumps(payload)).raw, payload)

    def test_overflowing_json_exponents_are_rejected(self) -> None:
        payload = json.loads(nominal_line())
        payload["baro"]["pressure_pa"] = "overflowing-exponent"
        for exponent in ("1e400", "-1e400"):
            with self.subTest(exponent=exponent):
                line = json.dumps(payload).replace('"overflowing-exponent"', exponent)
                with self.assertRaises(TelemetryError):
                    decode_line(line)

    def test_skip_invalid_stream_continues_to_valid_frame(self) -> None:
        oversized_sensor = json.loads(nominal_line())
        oversized_sensor["baro"]["pressure_pa"] = 10**400
        oversized_timestamp = json.loads(nominal_line())
        oversized_timestamp["ts_us"] = 10**400
        malformed_lines = (
            "boot banner",
            b"\xff\n",
            oversized_json_integer(),
            deeply_nested_json(),
            json.dumps(oversized_sensor),
            json.dumps(oversized_timestamp),
        )
        for line in malformed_lines:
            with self.subTest(prefix=repr(line[:32])):
                frames = list(decode_stream([line, nominal_line()], ignore_invalid=True))
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0].sequence, 9)

    def test_strict_stream_reports_malformed_frame(self) -> None:
        with self.assertRaises(TelemetryError):
            list(decode_stream(["boot banner", nominal_line()]))

    def test_unknown_additive_fields_are_preserved(self) -> None:
        payload = json.loads(nominal_line())
        payload["diagnostic"] = {"firmware": "future-version"}
        payload["imu"]["range_g"] = 2
        payload["health"]["reset_count"] = 3
        frame = decode_line(json.dumps(payload))
        self.assertEqual(frame.raw, payload)
        self.assertEqual(frame.imu["range_g"], 2)  # type: ignore[index]


if __name__ == "__main__":
    unittest.main()
