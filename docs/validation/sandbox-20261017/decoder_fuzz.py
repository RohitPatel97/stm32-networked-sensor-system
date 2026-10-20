"""Seeded corrupt-stream recovery checks; keep this run reproducible."""

import json
import random
import subprocess
from pathlib import Path

from sensor_monitor.models import TelemetryReading
from sensor_monitor.protocol import NewlineJsonDecoder

ROOT = Path(__file__).resolve().parents[1]
rng = random.Random(20261017)
result = subprocess.run(
    [str(ROOT / "build/host/sensor_hub_sim.exe"), "--duration-ms", "100"],
    capture_output=True, check=True,
)
valid = result.stdout.strip()
accepted = rejected = 0
for i in range(5000):
    if i % 3 == 0:
        junk = rng.randbytes(rng.randrange(1, 5000))
    elif i % 3 == 1:
        junk = bytearray(valid)
        for _ in range(rng.randrange(1, 10)):
            junk[rng.randrange(len(junk))] = rng.randrange(256)
        junk = bytes(junk)
    else:
        junk = b"[" * rng.randrange(100, 1500) + b"0"
    stream = junk + b"\n" + valid + b"\n"
    decoder = NewlineJsonDecoder()
    events = []
    offset = 0
    while offset < len(stream):
        length = rng.randrange(1, 500)
        events.extend(decoder.feed_events(stream[offset:offset + length]))
        assert decoder.buffered_bytes <= 2048
        offset += length
    assert isinstance(events[-1], TelemetryReading)
    assert events[-1].sequence == 0 and events[-1].boot_id == 1
    assert decoder.buffered_bytes == 0
    accepted += sum(isinstance(e, TelemetryReading) for e in events)
    rejected += sum(not isinstance(e, TelemetryReading) for e in events)
report = {"seed": 20261017, "cases": 5000, "accepted_records": accepted,
          "rejected_records": rejected, "valid_tail_recovery_cases": 5000,
          "buffer_limit_bytes": 2048, "passed": True}
(ROOT / "evidence/decoder-fuzz.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
