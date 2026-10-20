from __future__ import annotations

import json

import pytest

from sensor_monitor.models import WireTelemetry
from sensor_monitor.protocol import (
    FrameDecodeError,
    FrameTooLargeError,
    NewlineJsonDecoder,
    ProtocolError,
    encode_frame,
    parse_frame,
)


def valid_wire(sequence: int = 7) -> WireTelemetry:
    return WireTelemetry(
        device_id="node-01",
        sequence=sequence,
        uptime_ms=700,
        temperature_c=25.2,
        pressure_hpa=1008.4,
        accel_g=(0.0, -0.01, 1.01),
        battery_v=12.2,
    )


def test_fragmented_frame_is_buffered_until_newline() -> None:
    frame = encode_frame(valid_wire())
    decoder = NewlineJsonDecoder()
    assert list(decoder.feed(frame[:13])) == []
    readings = list(decoder.feed(frame[13:]))
    assert len(readings) == 1
    assert readings[0].device_id == "node-01"
    assert decoder.buffered_bytes == 0


def test_multiple_frames_in_one_uart_chunk() -> None:
    decoder = NewlineJsonDecoder()
    readings = list(decoder.feed(encode_frame(valid_wire(7)) + encode_frame(valid_wire(8))))
    assert [item.sequence for item in readings] == [7, 8]


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b'{"schema_version":1}',
        b'{"schema_version":1,"device_id":"bad id"}',
        b'{"schema_version":true,"device_id":"node","sequence":0,"uptime_ms":0,"temperature_c":24.0,"pressure_hpa":1013.0,"accel_g":[0.0,0.0,1.0]}',
        b'{"schema_version":1,"device_id":"node","sequence":"0","uptime_ms":0,"temperature_c":24.0,"pressure_hpa":1013.0,"accel_g":[0.0,0.0,1.0]}',
        b"\xff\xfe",
    ],
)
def test_invalid_frames_are_rejected(raw: bytes) -> None:
    with pytest.raises(FrameDecodeError):
        parse_frame(raw)


def test_bounded_decoder_discards_unterminated_oversize_frame() -> None:
    decoder = NewlineJsonDecoder(max_frame_bytes=64)
    with pytest.raises(FrameTooLargeError):
        list(decoder.feed(b"x" * 65))
    assert decoder.buffered_bytes == 0


def test_decoder_requires_bytes_and_skips_blank_lines() -> None:
    decoder = NewlineJsonDecoder()
    with pytest.raises(TypeError):
        list(decoder.feed("not bytes"))  # type: ignore[arg-type]
    assert list(decoder.feed(b"\r\n\n")) == []


@pytest.mark.parametrize("field,value", [("sequence", True), ("temperature_c", "25"),
                                        ("accel_g", [0.0, float("nan"), 1.0])])
def test_wire_does_not_coerce_or_accept_nonfinite_values(field, value) -> None:
    payload = valid_wire().model_dump()
    payload[field] = value
    with pytest.raises(FrameDecodeError):
        parse_frame(json.dumps(payload).encode())


def test_event_decoder_recovers_around_malformed_frames() -> None:
    decoder = NewlineJsonDecoder()
    events = decoder.feed_events(
        encode_frame(valid_wire(7)) + b"not-json\n" + encode_frame(valid_wire(8))
    )
    assert [item.sequence for item in events if not isinstance(item, ProtocolError)] == [7, 8]
    assert sum(isinstance(item, ProtocolError) for item in events) == 1


def test_oversized_tail_is_discarded_until_delimiter() -> None:
    decoder = NewlineJsonDecoder()
    assert isinstance(decoder.feed_events(b"x" * 2049)[0], FrameTooLargeError)
    assert decoder.buffered_bytes == 0
    assert decoder.feed_events(b"remaining oversized tail") == []
    events = decoder.feed_events(b"\n" + encode_frame(valid_wire()))
    assert len(events) == 1
    assert events[0].sequence == 7
