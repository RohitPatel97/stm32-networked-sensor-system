from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensor_monitor.bridge import BridgeError, convert_freertos_frame
from sensor_monitor.protocol import MAX_FRAME_BYTES, ProtocolError, encode_frame, parse_frame

FIXTURE = Path(__file__).parents[1] / "examples" / "freertos_telemetry.ndjson"


def source_frame() -> dict:
    return json.loads(FIXTURE.read_bytes().splitlines()[0])


def convert(payload: dict):
    return convert_freertos_frame(json.dumps(payload).encode(), device_id="hub-01", boot_id=17)


def set_field(payload: dict, path: str, value: object) -> None:
    parts = path.split(".")
    node = payload
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value


def test_upstream_fixture_converts_with_units_and_explicit_identity() -> None:
    frames = [
        convert_freertos_frame(raw, device_id="hub-01", boot_id=17)
        for raw in FIXTURE.read_bytes().splitlines()
    ]
    assert len(frames) == 2
    assert [frame.sequence for frame in frames] == [9, 19]
    assert [frame.uptime_ms for frame in frames] == [100, 200]
    assert [frame.pressure_hpa for frame in frames] == [1006.52, 1006.55]
    assert frames[0].temperature_c == 25.08  # BMP280, not the IMU's die temperature.
    assert frames[0].accel_g == (-0.1389, 0.1, 0.9889)
    for frame in frames:
        assert frame.schema_version == 2
        assert frame.device_id == "hub-01"
        assert frame.boot_id == 17
        assert frame.battery_v is None
        assert frame.watchdog_resets is None
        assert parse_frame(encode_frame(frame)).model_dump(exclude={"received_at"}) == (
            frame.model_dump()
        )


@pytest.mark.parametrize("watchdog", ["fed", "withheld"])
def test_watchdog_decision_does_not_invent_a_reset_count(watchdog: str) -> None:
    payload = source_frame()
    payload["health"]["watchdog"] = watchdog
    payload["health"]["i2c_errors"] = 3
    payload["health"]["queue_drops"] = 7
    frame = convert(payload)
    assert frame.watchdog_resets is None
    assert (frame.i2c_errors, frame.queue_drops) == (3, 7)


def test_timestamp_truncates_submillisecond_remainder_without_rounding() -> None:
    payload = source_frame()
    payload["ts_us"] = 1999
    assert convert(payload).uptime_ms == 1


@pytest.mark.parametrize("boundary", [0, 2**32 - 1])
def test_unsigned_boundaries_are_preserved(boundary: int) -> None:
    payload = source_frame()
    payload["seq"] = boundary
    payload["ts_us"] = 2**64 - 1
    for counter in ("i2c_retries", "i2c_errors", "queue_drops", "deadline_misses"):
        payload["health"][counter] = boundary
    payload["timing"] = {"acquisition_us": boundary, "processing_us": boundary}
    frame = convert_freertos_frame(
        json.dumps(payload).encode(), device_id="hub-01", boot_id=2**64 - 1
    )
    assert frame.sequence == boundary
    assert frame.uptime_ms == (2**64 - 1) // 1000
    assert frame.i2c_errors == boundary
    assert frame.queue_drops == boundary
    assert frame.boot_id == 2**64 - 1


@pytest.mark.parametrize(
    "path,value",
    [
        ("schema", True), ("schema", 1.0), ("schema", "1"), ("schema", 2),
        ("type", "summary"), ("seq", True), ("seq", 1.0), ("seq", -1),
        ("seq", 2**32), ("ts_us", 2**64), ("ts_us", "1000"),
        ("sensors.mpu6050", "unknown"), ("sensors.bmp280", False),
        ("health.i2c_retries", -1), ("health.i2c_errors", True),
        ("health.queue_drops", 2**32), ("health.deadline_misses", 1.5),
        ("health.watchdog", 0), ("timing.acquisition_us", "1"),
        ("timing.processing_us", 2**32),
        ("imu.accel_g", [0, 1]), ("imu.accel_g", [0, True, 1]),
        ("imu.gyro_dps", [0, 0, "1"]), ("imu.temperature_c", None),
        ("baro.temperature_c", "25"), ("baro.pressure_pa", False),
        ("baro.altitude_m", float("nan")), ("imu.temperature_c", float("inf")),
        ("baro.altitude_m", 10**400),
    ],
)
def test_source_types_bounds_and_unmapped_fields_are_validated(path: str, value: object) -> None:
    payload = source_frame()
    set_field(payload, path, value)
    with pytest.raises(BridgeError):
        convert(payload)


@pytest.mark.parametrize(
    "path",
    [
        "schema", "type", "seq", "ts_us", "sensors", "imu", "baro", "health", "timing",
        "sensors.mpu6050", "sensors.bmp280", "imu.accel_g", "imu.gyro_dps",
        "imu.temperature_c", "baro.temperature_c", "baro.pressure_pa", "baro.altitude_m",
        "health.i2c_retries", "health.i2c_errors", "health.queue_drops",
        "health.deadline_misses", "health.watchdog", "timing.acquisition_us",
        "timing.processing_us",
    ],
)
def test_missing_source_fields_are_rejected(path: str) -> None:
    payload = source_frame()
    parts = path.split(".")
    node = payload
    for part in parts[:-1]:
        node = node[part]
    del node[parts[-1]]
    with pytest.raises(BridgeError):
        convert(payload)


@pytest.mark.parametrize("state", ["online", "offline"])
@pytest.mark.parametrize("sensor,payload_name", [("mpu6050", "imu"), ("bmp280", "baro")])
def test_null_measurement_is_incomplete_regardless_of_debounced_status(
    state: str, sensor: str, payload_name: str,
) -> None:
    payload = source_frame()
    payload["sensors"][sensor] = state
    payload[payload_name] = None
    with pytest.raises(BridgeError, match=f"incomplete sensor frame: {payload_name} is null"):
        convert(payload)


def test_status_snapshot_does_not_replace_per_sample_payload_validity() -> None:
    payload = source_frame()
    # The target snapshots current health separately from the queued sample.
    payload["sensors"] = {"mpu6050": "offline", "bmp280": "offline"}
    assert convert(payload).temperature_c == 25.08


@pytest.mark.parametrize(
    "raw",
    [b"", b"\xff", b"[]", b"{}{}", b'{"schema":1,"schema":1}', b"[" * 1000 + b"]" * 1000],
)
def test_bad_encoding_structure_and_ambiguous_fields_raise_protocol_errors(raw: bytes) -> None:
    with pytest.raises(ProtocolError):
        convert_freertos_frame(raw, device_id="hub-01", boot_id=17)


def test_frame_size_limit_and_optional_line_ending() -> None:
    raw = json.dumps(source_frame()).encode()
    exact = raw.ljust(MAX_FRAME_BYTES, b" ")
    assert convert_freertos_frame(exact, device_id="hub-01", boot_id=17).sequence == 9
    assert convert_freertos_frame(exact + b"\n", device_id="hub-01", boot_id=17).sequence == 9
    assert convert_freertos_frame(raw + b"\r\n", device_id="hub-01", boot_id=17).sequence == 9
    with pytest.raises(BridgeError, match="receive limit"):
        convert_freertos_frame(exact + b" ", device_id="hub-01", boot_id=17)
    with pytest.raises(BridgeError, match="receive limit"):
        convert_freertos_frame(exact + b"\r\n", device_id="hub-01", boot_id=17)


@pytest.mark.parametrize("boot_id", [True, -1, 2**64, "17", 17.0, None])
def test_boot_identity_must_be_an_explicit_uint64(boot_id: object) -> None:
    with pytest.raises(BridgeError, match="boot_id"):
        convert_freertos_frame(
            json.dumps(source_frame()).encode(), device_id="hub-01", boot_id=boot_id
        )


@pytest.mark.parametrize("device_id", ["", "bad id", "x" * 65, 17])
def test_device_identity_obeys_gateway_rules(device_id: object) -> None:
    with pytest.raises(BridgeError, match="device_id"):
        convert_freertos_frame(
            json.dumps(source_frame()).encode(), device_id=device_id, boot_id=17
        )


@pytest.mark.parametrize(
    "path,value",
    [("baro.temperature_c", 151), ("baro.pressure_pa", 29999), ("imu.accel_g", [0, 0, 17])],
)
def test_valid_source_numbers_outside_gateway_ranges_are_rejected(
    path: str, value: object,
) -> None:
    payload = source_frame()
    set_field(payload, path, value)
    with pytest.raises(BridgeError, match="outside gateway schema"):
        convert(payload)


def test_unknown_additive_fields_are_ignored_without_overriding_mapping() -> None:
    payload = source_frame()
    payload["device_id"] = "untrusted-source-id"
    payload["boot_id"] = 999
    payload["health"]["watchdog_resets"] = 999
    payload["baro"]["new_sensor_detail"] = {"ok": True}
    frame = convert(payload)
    assert (frame.device_id, frame.boot_id) == ("hub-01", 17)
    assert frame.watchdog_resets is None


def test_conversion_requires_bytes() -> None:
    with pytest.raises(BridgeError, match="must be bytes"):
        convert_freertos_frame("{}", device_id="hub-01", boot_id=17)


def test_overflowing_json_exponent_is_rejected_even_in_unmapped_sensor_field() -> None:
    raw = json.dumps(source_frame()).encode().replace(b"56.18", b"1e400")
    with pytest.raises(BridgeError, match=r"baro\.altitude_m must be a finite number"):
        convert_freertos_frame(raw, device_id="hub-01", boot_id=17)


def test_direct_api_rejects_multiline_json_like_capture_conversion() -> None:
    raw = json.dumps(source_frame(), indent=1).encode()
    with pytest.raises(BridgeError, match="one LF-delimited source record"):
        convert_freertos_frame(raw, device_id="hub-01", boot_id=17)
