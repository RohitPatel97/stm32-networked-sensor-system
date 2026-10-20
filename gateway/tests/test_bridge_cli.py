from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensor_monitor.cli import main
from sensor_monitor.protocol import parse_frame


def test_convert_freertos_capture_and_validate_output(tmp_path, capsys) -> None:
    output = tmp_path / "gateway.jsonl"
    assert (
        main(
            [
                "convert-freertos",
                str(Path(__file__).parents[1] / "examples/freertos_telemetry.ndjson"),
                "--output",
                str(output),
                "--device-id",
                "hub-1",
                "--boot-id",
                "7",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out == "converted=2 rejected=0\n"
    records = [parse_frame(raw) for raw in output.read_bytes().splitlines()]
    assert [(r.boot_id, r.sequence) for r in records] == [(7, 9), (7, 19)]
    assert all(r.device_id == "hub-1" and r.watchdog_resets is None for r in records)
    assert main(["validate", str(output)]) == 0
    assert capsys.readouterr().out == "accepted=2 rejected=0\n"


def test_converter_recovers_after_invalid_and_incomplete_records(tmp_path, capsys) -> None:
    first = (
        (Path(__file__).parents[1] / "examples/freertos_telemetry.ndjson")
        .read_bytes()
        .splitlines()[0]
    )
    incomplete = json.loads(first)
    incomplete["imu"] = None
    source = tmp_path / "mixed.jsonl"
    output = tmp_path / "gateway.jsonl"
    source.write_bytes(
        b"\n\xff\n"
        + first
        + b"\n"
        + json.dumps(incomplete).encode()
        + b"\n"
        + b" " * 100_000
        + b"\n"
        + first
    )
    assert (
        main(
            [
                "convert-freertos",
                str(source),
                "--output",
                str(output),
                "--device-id",
                "hub-1",
                "--boot-id",
                "7",
            ]
        )
        == 1
    )
    result = capsys.readouterr()
    assert result.out == "converted=2 rejected=3\n"
    assert all(f"line {n}:" in result.err for n in (2, 4, 5))
    assert len(output.read_bytes().splitlines()) == 2


def test_converter_does_not_overwrite_its_input(tmp_path, capsys) -> None:
    source = tmp_path / "same.jsonl"
    source.write_bytes(b"original")
    assert (
        main(
            [
                "convert-freertos",
                str(source),
                "--output",
                str(source),
                "--device-id",
                "hub-1",
                "--boot-id",
                "7",
            ]
        )
        == 2
    )
    assert source.read_bytes() == b"original"
    assert "different files" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["simulate", "--count", "0"],
        ["simulate", "--interval", "nan"],
        ["simulate", "--interval", "-1"],
        ["simulate", "--interval", "1e308"],
        ["simulate", "--boot-id", "-1"],
        ["simulate", "--boot-id", "18446744073709551616"],
        ["convert-freertos", "x", "--output", "y", "--device-id", "bad id", "--boot-id", "1"],
    ],
)
def test_invalid_cli_configuration_fails_before_io(args) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_simulate_v2_and_missing_input_have_explicit_results(tmp_path, capsys) -> None:
    assert main(["simulate", "--count", "2", "--interval", "0", "--boot-id", "8"]) == 0
    samples = [parse_frame(s.encode()) for s in capsys.readouterr().out.splitlines()]
    assert [(s.boot_id, s.sequence) for s in samples] == [(8, 0), (8, 1)]
    assert main(["validate", str(tmp_path / "absent")]) == 2
    assert "capture I/O error" in capsys.readouterr().err
