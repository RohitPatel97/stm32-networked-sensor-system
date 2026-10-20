"""Run all combined host acceptance gates after building the C simulator."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--ctest", default="ctest")
parser.add_argument("--binary", type=Path, help="compiled simulator path")
parser.add_argument(
    "--output", type=Path, help="save command outputs and source hashes as JSON"
)
args = parser.parse_args()
binary = args.binary or Path(
    os.environ.get(
        "SENSOR_HUB_BINARY",
        ROOT
        / "build/host"
        / ("sensor_hub_sim.exe" if sys.platform == "win32" else "sensor_hub_sim"),
    )
)
if not binary.is_file() and not args.binary:
    binary = ROOT / "build/host/Release/sensor_hub_sim.exe"
if not binary.is_file():
    raise SystemExit("Build the C simulator first, or provide --binary")
binary = binary.resolve()
test_env = {**os.environ, "SENSOR_HUB_BINARY": str(binary)}
commands = [
    [args.ctest, "--test-dir", "build/host", "-C", "Release", "--output-on-failure"],
    [sys.executable, "scripts/export_protocol.py", "--check"],
    [sys.executable, "scripts/generate_fixture.py", str(binary), "--check"],
    [sys.executable, "-m", "ruff", "check", "gateway"],
    ["node", "--check", "demo/web/lab.js"],
    [sys.executable, "-m", "pytest", "-q"],
]
report = {
    "started_at": datetime.now(UTC).isoformat(),
    "python": sys.version,
    "platform": platform.platform(),
    "checks": [],
}
for command in commands:
    print("Running:", " ".join(command), flush=True)
    result = subprocess.run(
        command, cwd=ROOT, env=test_env, capture_output=True, text=True, check=False
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    report["checks"].append(
        {
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    )
    if result.returncode:
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        raise SystemExit(result.returncode)
report["source_sha256"] = {
    str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
        p.read_bytes()
    ).hexdigest()
    for folder in (
        "firmware/src",
        "firmware/include",
        "firmware/sim",
        "firmware/target",
        "firmware/tools",
        "firmware/tests",
        "gateway/src",
        "gateway/tests",
        "tests",
        "scripts",
        "protocol",
        "demo/web",
        "desktop",
        "emulation/renode",
    )
    for p in sorted((ROOT / folder).rglob("*"))
    if p.is_file() and "__pycache__" not in p.parts
}
report["binary_sha256"] = {
    str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(
        p.read_bytes()
    ).hexdigest()
    for p in (
        ROOT / "build/host/sensor_hub_sim.exe",
        ROOT / "build/host/sensor_hub_sim",
        ROOT / "build/stm32/target/stm32f401re/sensor_hub_firmware.elf",
        ROOT / "build/stm32-on/target/stm32f401re/sensor_hub_firmware.elf",
        ROOT / "build/desktop/SensorLab.exe",
    )
    if p.is_file()
}
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print("All combined host acceptance gates passed")
