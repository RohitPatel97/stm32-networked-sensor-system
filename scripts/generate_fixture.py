"""Generate the shared fixture from actual compiled C output (no hand-authored sensor data)."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary", type=Path)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
command = [
    str(args.binary.resolve()),
    "--duration-ms",
    "1500",
    "--boot-id",
    "42",
    "--disconnect",
    "mpu",
    "--disconnect-at-ms",
    "500",
    "--reconnect-at-ms",
    "1000",
]
result = subprocess.run(command, capture_output=True, check=True, timeout=10)
content = result.stdout.replace(b"\r\n", b"\n")
path = ROOT / "protocol/examples.ndjson"
if args.check:
    if path.read_bytes().replace(b"\r\n", b"\n") != content:
        raise SystemExit(
            "Compiled C fixture changed: regenerate and review the contract"
        )
else:
    path.write_bytes(content)
print(
    "Shared fixture verified"
    if args.check
    else "Shared fixture generated from compiled C"
)
