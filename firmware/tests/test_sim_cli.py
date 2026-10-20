"""Black-box assertions for the compiled deterministic C simulator."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run(binary: Path, *arguments: str) -> tuple[list[dict], dict]:
    result = subprocess.run(
        [str(binary), *arguments], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10,
    )
    frames = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    summaries = [json.loads(line) for line in result.stderr.splitlines() if line.startswith("{")]
    assert len(summaries) == 1, result.stderr
    return frames, summaries[0]


def assert_dashboard_recovery(frames: list[dict]) -> None:
    """Replay real C output through the dashboard, including null IMU payloads."""
    dashboard = Path(__file__).resolve().parents[1] / "tools" / "dashboard.py"
    result = subprocess.run(
        [sys.executable, str(dashboard), "--file", "-", "--no-ansi"],
        input="".join(json.dumps(frame) + "\n" for frame in frames),
        check=False, text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr, result.stderr
    snapshots = result.stdout.split("FreeRTOS Sensor Hub - telemetry v3\n")[1:]
    assert len(snapshots) == len(frames), result.stdout
    assert any(frame["imu"] is None for frame in frames)
    assert frames[0]["imu"] is not None and frames[-1]["imu"] is not None
    for frame, snapshot in zip(frames, snapshots):
        assert f"seq {frame['seq']:8d}" in snapshot, snapshot
        lines = snapshot.splitlines()
        for label in ("accel g", "gyro dps"):
            vector = next(line for line in lines if line.startswith(label))
            assert ("n/a" in vector) == (frame["imu"] is None), vector
        pressure = next(line for line in lines if line.startswith("pressure"))
        assert ("n/a" in pressure) == (frame["baro"] is None), pressure


def main() -> int:
    binary = Path(sys.argv[1])

    frames, summary = run(binary, "--duration-ms", "2000")
    assert len(frames) == 20
    assert summary["samples_requested"] == 200
    assert summary["samples_processed"] == 200
    assert summary["i2c_errors"] == 0
    assert summary["queue_drops"] == 0
    assert summary["watchdog_feeds"] == 2

    frames, summary = run(binary, "--duration-ms", "2000", "--disconnect", "bmp",
                          "--disconnect-at-ms", "500", "--i2c-nack-every", "7")
    assert frames[-1]["sensors"]["bmp280"] == "offline"
    assert frames[-1]["sensors"]["mpu6050"] == "online"
    assert summary["sensor_disconnects"] == 1
    assert summary["i2c_retries"] > 0
    assert summary["i2c_errors"] > 0

    _, summary = run(binary, "--duration-ms", "1000", "--queue-capacity", "2",
                     "--processor-period-ms", "30")
    assert summary["queue_drops"] > 0

    _, summary = run(binary, "--duration-ms", "3000", "--stall-task", "processing",
                     "--stall-at-ms", "1200")
    assert summary["watchdog_withheld"] > 0
    assert summary["deadline_misses"] > 0

    # Exercise each real driver's reinitialization after a temporary outage.
    for target, names in (("bmp", ["bmp280"]), ("mpu", ["mpu6050"]),
                          ("all", ["mpu6050", "bmp280"])):
        frames, summary = run(binary, "--duration-ms", "2000", "--disconnect", target,
                              "--disconnect-at-ms", "500", "--reconnect-at-ms", "1000")
        outage = [frame for frame in frames if 600_000 <= frame["ts_us"] < 1_000_000]
        assert outage
        for name in names:
            assert all(frame["sensors"][name] == "offline" for frame in outage)
            assert frames[-1]["sensors"][name] == "online"
            payload = "imu" if name == "mpu6050" else "baro"
            assert all(frame[payload] is None for frame in outage)
            assert frames[-1][payload] is not None
        assert summary["sensor_disconnects"] == len(names)
        assert summary["sensor_reconnects"] == len(names)
        assert summary["watchdog_feeds"] == 2
        assert summary["watchdog_withheld"] == 0
        assert summary["samples_processed"] == 200
        if target in ("mpu", "all"):
            assert_dashboard_recovery(frames)

    # Sensors absent during startup must recover through their normal read path.
    frames, summary = run(binary, "--duration-ms", "1000", "--disconnect", "all",
                          "--disconnect-at-ms", "0", "--reconnect-at-ms", "500")
    assert frames[0]["imu"] is None and frames[0]["baro"] is None
    assert frames[-1]["imu"] is not None and frames[-1]["baro"] is not None
    assert summary["sensor_disconnects"] == 0  # Never online before the outage.
    assert summary["sensor_reconnects"] == 2

    frames, summary = run(binary, "--duration-ms", "3000", "--stall-task", "acquisition",
                          "--stall-at-ms", "1200")
    assert summary["tick_overruns"] > 0
    assert summary["watchdog_withheld"] == 2
    # Processing also stops heartbeating once acquisition stops supplying input.
    assert summary["deadline_misses"] == 4
    assert summary["samples_acquired"] == summary["samples_processed"] == 119
    assert len(frames) == 30  # Telemetry continues reporting the latest sample.
    assert frames[-1]["seq"] == 118 and frames[-1]["ts_us"] == 1_190_000

    # Without a first sample, neither acquisition nor processing is ever healthy.
    frames, summary = run(binary, "--duration-ms", "2000", "--stall-task", "acquisition",
                          "--stall-at-ms", "0")
    assert len(frames) == 20 and all(not f["sample_present"] for f in frames)
    assert all(f["imu"] is None and f["baro"] is None for f in frames)
    assert summary["samples_acquired"] == summary["samples_processed"] == 0
    assert summary["watchdog_feeds"] == 0
    assert summary["watchdog_withheld"] == 2
    assert summary["deadline_misses"] == 4

    # A 10 ms dropout is below the three-acquisition offline threshold.
    _, summary = run(binary, "--duration-ms", "1000", "--disconnect", "bmp",
                     "--disconnect-at-ms", "500", "--reconnect-at-ms", "510")
    assert summary["i2c_errors"] == 1
    assert summary["sensor_disconnects"] == 0
    assert summary["sensor_reconnects"] == 0

    # UINT32_MAX formerly allowed the inclusive millisecond loop to wrap forever.
    numeric_options = ("--duration-ms", "--processor-period-ms", "--queue-capacity",
                       "--i2c-nack-every", "--disconnect-at-ms", "--reconnect-at-ms",
                       "--stall-at-ms")
    invalid_values = ("-1", "+1", " 1", "1 ", "1.5", "", "4294967296",
                      "999999999999999999999999999999")
    rejected = 0
    for option in numeric_options:
        for value in invalid_values:
            assert_rejected(binary, option, value)
            rejected += 1
    invalid_arguments = (
        ("--duration-ms", "0"), ("--duration-ms", "3600001"),
        ("--duration-ms", "4294967295"), ("--processor-period-ms", "0"),
        ("--queue-capacity", "0"), ("--queue-capacity", "17"),
        ("--duration-ms",), ("--unknown", "1"), ("--disconnect", "invalid"),
        ("--stall-task", "invalid"), ("--reconnect-at-ms", "500"),
        ("--disconnect", "bmp", "--disconnect-at-ms", "500", "--reconnect-at-ms", "500"),
        ("--disconnect", "bmp", "--disconnect-at-ms", "500", "--reconnect-at-ms", "400"),
    )
    for arguments in invalid_arguments:
        assert_rejected(binary, *arguments)
        rejected += 1

    # Large supported non-duration values must retain their full unsigned width.
    _, summary = run(binary, "--duration-ms", "100", "--i2c-nack-every", "4294967295",
                     "--processor-period-ms", "4294967295")
    assert summary["samples_acquired"] == 10 and summary["samples_processed"] == 0
    print(f"12 simulator scenarios, 2 dashboard recovery replays, and {rejected} CLI rejection cases passed")
    return 0


def assert_rejected(binary: Path, *arguments: str) -> None:
    result = subprocess.run([str(binary), *arguments], check=False, text=True,
                            capture_output=True, timeout=5)
    assert result.returncode == 2, (arguments, result.returncode, result.stderr)
    assert not result.stdout, (arguments, result.stdout)
    assert result.stderr.strip(), arguments


if __name__ == "__main__":
    raise SystemExit(main())
