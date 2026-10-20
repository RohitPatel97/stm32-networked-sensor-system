#!/usr/bin/env python3
"""Dependency-free deployment smoke test for CI or a Raspberry Pi."""

from __future__ import annotations

import json
import sys
from urllib.parse import quote
from urllib.request import urlopen


def check(base_url: str) -> dict:
    base_url = base_url.rstrip("/")
    with urlopen(f"{base_url}/healthz", timeout=3) as response:
        health = json.load(response)
    with urlopen(f"{base_url}/api/v1/snapshot", timeout=3) as response:
        snapshot = json.load(response)
    assert health["status"] == "ok", health
    assert snapshot["stats"]["frames_accepted"] > 0, "no accepted telemetry yet"
    assert isinstance(snapshot["devices"], list) and snapshot["devices"], "no devices yet"
    device_id = snapshot["devices"][0]["device_id"]
    with urlopen(
        f"{base_url}/api/v1/devices/{quote(device_id, safe='')}/readings?limit=1", timeout=3
    ) as response:
        readings = json.load(response)
    assert isinstance(readings, list) and readings, "no device history yet"
    assert readings[-1]["device_id"] == device_id, "history belongs to a different device"
    return {
        "source": health["source"],
        "accepted": snapshot["stats"]["frames_accepted"],
        "devices": len(snapshot["devices"]),
        "last_sequence": readings[-1]["sequence"],
    }


if __name__ == "__main__":
    result = check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
    print("ok " + " ".join(f"{key}={value}" for key, value in result.items()))
