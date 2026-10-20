# Expanded test run — October 12, 2026

All final checks passed in the combined project. Both original projects remain
unchanged: 116 imported source-file hashes match and both original Git working
trees are clean. No production firmware or gateway code changed in this test run.

| Check | Result |
| --- | --- |
| Full Python regression suite | 249 passed: 198 inherited gateway cases and 51 integration cases |
| Firmware CTest | All 4 suites passed |
| New fault matrix | 18 cases passed |
| Compiled C → gateway → HTTP/WebSocket → production dashboard handlers | 6 cases passed, then 10 consecutive repeat runs (60 additional executions) |
| Schema and compiled fixture consistency | Passed |
| Ruff: gateway, scripts, integration tests | Passed |
| 60-second legacy-v2 compatibility stress | 15,000/15,000 frames accepted at approximately 250 frames/second across four devices |
| Stress parsing/order/device rejections | Zero |
| Stress fast subscriber | All 15,000 events received in order; p95 latency upper bound 1 ms |
| Stress slow subscriber | Expected 14,992 old events discarded; latest 8 retained |
| Stress reconnect/reboot handling | 14 scripted transport reconnects and 4 device session changes verified |
| Stress bounded history/memory | 120 readings/device; 20,781 bytes maximum sampled Python allocation growth after warmup |
| Existing live v3 demo | 54,264 frames accepted, 452 session changes, zero parse errors, sequence anomalies, device rejections, or WebSocket drops at the recorded snapshot |
| Existing browser | Live barometer outage, valid IMU readings, recovery alerts visible; no captured browser errors |

The 18 additional fault cases cover sensors absent at startup (MPU6050, BMP280,
or both), with and without recovery; processing and health task stalls at startup
or during operation; telemetry stalls and stale/offline transitions; queue
pressure at capacities 1, 2, and 8; transient I2C retries and retry exhaustion;
and three C-simulated devices over three boots with fragmented CRLF records,
transport resets, bounded history, and fast/slow subscribers. Existing cases
also cover acquisition stalls, malformed input, rollover, old boot rejection,
and child-process shutdown.

The six dashboard cases use MPU6050, BMP280, and simultaneous outages at replay
rates of 100 and 1,000 frames/second. Each verifies all 30 frames arrived in order,
the appropriate missing-value displays, recovery alerts, final online health,
and finite chart coordinates using the production JavaScript handlers.

## Failure found and resolved

The initial run passed 225 Python cases and failed the dashboard case. Its C
source could advance through the outage while the test loaded static assets and
opened the socket. The test now holds the actual C subprocess behind a start
barrier until the WebSocket subscriber is registered, then requires all frame
sequences 0–29. The assertions were expanded to both sensors and both replay
rates; ten consecutive runs of those six cases passed. This fixes test timing
without weakening assertions or adding arbitrary startup delays.

The upstream Starlette/httpx TestClient deprecation warning remains non-fatal.
The stress run uses synthetic legacy-v2 frames through the production hub;
the 51 integration cases use compiled C v3 output or explicit protocol mutations.
Stress latency measures encode-complete to subscriber consumption, not browser
or hardware latency. Memory numbers are sampled `tracemalloc` allocations, not
process RSS. Hardware behavior was not tested in this run.

## Evidence and reproduction

- [Final host gates, output, source and binary hashes](multiple-cases-host.json)
- [Initial failure output](multiple-cases-initial-failure.json)
- [Integration JUnit report](multiple-cases-integration.xml)
- [Ten repeated dashboard runs](multiple-cases-dashboard-repeats.json)
- [Compatibility stress measurements and acceptance checks](multiple-cases-soak.json)
- [Live HTTP snapshot](multiple-cases-live-api.json)
- [Browser state and error log](multiple-cases-browser.txt)

From the combined project root, with dependencies installed and the simulator built:

```sh
python scripts/verify.py --output docs/validation/multiple-cases-host.json
python -m pytest tests/integration -q
python -m pytest tests/integration/test_system.py -k compiled_c_to_http -q
python scripts/check_originals.py
```

Use `--ctest` with the installed CTest path when it is not on `PATH`. The local
run used `../tmp/native-tools/Scripts/ctest.exe` and `.venv/Scripts/python.exe`.
The stress harness can be run from the root without a Git commit:

```python
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("gateway/scripts").resolve()))
from soak_test import SoakConfig, run_soak

result = asyncio.run(run_soak(SoakConfig(
    duration_seconds=60, rate_hz=250, warmup_seconds=5,
)))
print(json.dumps(result, indent=2))
assert result["passed"]
```
