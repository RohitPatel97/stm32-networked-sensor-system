from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def binary() -> Path:
    path = Path(
        os.environ.get(
            "SENSOR_HUB_BINARY",
            str(
                ROOT
                / "build"
                / "host"
                / (
                    "sensor_hub_sim.exe"
                    if sys.platform == "win32"
                    else "sensor_hub_sim"
                )
            ),
        )
    )
    if not path.is_file() and "SENSOR_HUB_BINARY" not in os.environ:
        path = ROOT / "build/host/Release/sensor_hub_sim.exe"
    assert path.is_file(), "Build firmware first: cmake -S firmware -B build/host"
    return path.resolve()


@pytest.fixture
def compiled_frames(binary):
    def generate(*args):
        output = subprocess.run(
            [str(binary), *args], capture_output=True, check=True, timeout=10
        )
        return [json.loads(line) for line in output.stdout.splitlines()]

    return generate
