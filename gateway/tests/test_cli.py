from __future__ import annotations

from pathlib import Path

import pytest

from sensor_monitor.cli import main
from sensor_monitor.models import WireTelemetry
from sensor_monitor.protocol import encode_frame


def padded_frame(size: int) -> bytes:
    frame = encode_frame(
        WireTelemetry(
            device_id="capture-node",
            sequence=1,
            uptime_ms=100,
            temperature_c=24.0,
            pressure_hpa=1013.0,
            accel_g=(0.0, 0.0, 1.0),
        )
    ).removesuffix(b"\n")
    return frame.ljust(size, b" ")


def test_validate_example_capture() -> None:
    assert main(["validate", str(Path(__file__).parents[1] / "examples/telemetry.jsonl")]) == 0


def test_simulate_emits_requested_count(capsys) -> None:
    assert main(["simulate", "--count", "2", "--interval", "0"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 2


@pytest.mark.parametrize(
    "size,ending,accepted",
    [
        (2048, b"\n", True),
        (2049, b"\n", False),
        (2047, b"\r\n", True),
        (2048, b"\r\n", False),
        (2048, b"", True),
        (2049, b"", False),
    ],
)
def test_validate_enforces_uart_byte_limit(tmp_path, capsys, size, ending, accepted) -> None:
    capture = tmp_path / "boundary.jsonl"
    capture.write_bytes(padded_frame(size) + ending)

    assert main(["validate", str(capture)]) == (0 if accepted else 1)

    output = capsys.readouterr()
    assert output.out == f"accepted={int(accepted)} rejected={int(not accepted)}\n"
    if accepted:
        assert output.err == ""
    else:
        assert output.err.count("line 1:") == 1
        assert "receive limit" in output.err


def test_validate_recovers_and_reports_physical_line_numbers(tmp_path, capsys) -> None:
    capture = tmp_path / "mixed.jsonl"
    valid = padded_frame(300)
    capture.write_bytes(b"\n" + valid + b"\r\n\xff\n\r\n" + padded_frame(100_000) + b"\n" + valid)

    assert main(["validate", str(capture)]) == 1

    output = capsys.readouterr()
    assert output.out == "accepted=2 rejected=2\n"
    assert output.err.count("line 3:") == 1
    assert output.err.count("line 5:") == 1
    assert "line 4:" not in output.err
    assert "line 6:" not in output.err


@pytest.mark.parametrize("ending", [b"\n", b""])
@pytest.mark.parametrize("padding", [b" ", b"\r"])
def test_validate_rejects_oversized_blank_record_once(tmp_path, capsys, ending, padding) -> None:
    capture = tmp_path / "oversized-blank.jsonl"
    capture.write_bytes(padding * 100_000 + ending)

    assert main(["validate", str(capture)]) == 1

    output = capsys.readouterr()
    assert output.out == "accepted=0 rejected=1\n"
    assert output.err.count("line 1:") == 1
    assert "receive limit" in output.err


def test_validate_whitespace_only_record_matches_live_rejection(tmp_path, capsys) -> None:
    capture = tmp_path / "whitespace.jsonl"
    capture.write_bytes(b"\n \t \n\r\n")

    assert main(["validate", str(capture)]) == 1

    output = capsys.readouterr()
    assert output.out == "accepted=0 rejected=1\n"
    assert output.err.count("line 2:") == 1
