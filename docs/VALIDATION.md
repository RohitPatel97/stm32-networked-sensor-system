# Combined-system validation — October 12, 2026

Latest follow-up: the [October 17 stress test](validation/STRESS-20261017.md)
found and fixed queue completion, simulator pacing, and app-lifecycle retention
issues; 255 Python tests and four C suites pass. It includes sustained runtime,
serial reconnects, full boot-journal capacity, and repeated service shutdowns.

Earlier follow-up: the [October 17 isolated test run](validation/sandbox-20261017/REPORT.md)
found and fixed a WebSocket startup race; 251 Python tests, four sanitized C
suites, packaging/runtime checks, and 5,000 decoder recovery cases passed.
The results below retain the October 12 evidence.

The combined project's current host software and target build were checked
locally. GitHub publication, CI execution, physical flashing, and Pi deployment
were not performed for this new repository.

## Observed results

| Gate | Observed result |
| --- | --- |
| Fresh host C build | Passed, Zig 0.16 / Clang 21.1, CMake 4.4, Ninja, Release |
| Firmware CTest | 4/4 suites passed: boot/protocol, C core, simulator integration, Python tooling |
| Gateway and cross-component pytest | 249 passed on Python 3.12.14 in the expanded test run |
| Gateway Ruff | Passed |
| Exported JSON Schema drift check | Passed |
| Real HTTP/WebSocket path | Compiled simulator streamed into FastAPI; all 30 frames accepted, no parse/sequence errors |
| Browser renderer regression | Production JavaScript rendered received C-origin events, missing values, recovery, and finite chart coordinates |
| Live browser inspection | Barometer outage rendered `n/a` while IMU remained live; recovery rendered online with nonzero historical counters; no browser errors observed |
| STM32, watchdog OFF | Linked image and five-vector/layout checks passed; 38,940 flash bytes; 32,464 bytes accounted RAM |
| STM32, watchdog ON | Linked image and five-vector/layout checks passed; 39,052 flash bytes; 32,464 bytes accounted RAM |
| Original source integrity | All 116 original imported files match pre-integration SHA-256 hashes; original Git working trees clean |

The inherited firmware suites include six C core groups, 12 simulator scenarios,
two terminal-dashboard recovery replays, 69 CLI rejection cases, and 22 Python
tool tests. The new boot suite checks interruptions at all five programmed words,
torn commit, committed-record corruption, exhaustion, overflow, and formatter
failure behavior. The combined pytest count includes 198 inherited gateway
tests and 51 integration cases. See [the expanded test report](validation/MULTIPLE-CASES.md)
for the additional fault matrix, repeated dashboard checks, and compatibility stress run.

The only pytest warning is an upstream Starlette/httpx TestClient deprecation.
It did not fail any test. Dependencies used for this run are pinned in
`requirements-lock.txt`; the recorded coverage figures in imported gateway
WORKFLOW are historical and are not a coverage claim for this integration.

## Regression coverage

- Actual C output traverses shared framing/validation, history, current health,
  alerts, REST, WebSocket, and the production browser rendering functions.
- MPU6050, BMP280, and simultaneous outages retain partial records and recover
  without fabricated sensor values or resetting cumulative totals.
- Acquisition stalls from startup or during a run preserve communication
  liveness while marking measurements unavailable/stale and task health failed.
- New boots reset frame/sample baselines. Delayed older boots, duplicate frames,
  backward samples, and mutations of an unchanged sample are rejected.
- Uint32 frame/sample/counter rollover and uint64 boot identity are exercised.
- Serial stream boundaries clear partial/oversized framing without clearing
  session state. Inherited eight mock-serial lifecycle cases still pass.
- Malformed, duplicate-key, unknown-field, non-finite, oversized, and out-of-range
  inputs are rejected; a future sample timestamp is rejected even by one microsecond.
- Missing temperature cannot falsely clear a prior temperature alarm.
- Stopping a compiled source with a full stdout pipe and cancelling during
  subprocess startup reap the child without hanging or leaking it.

## Reproduce and inspect evidence

```sh
cmake -S firmware -B build/host -DCMAKE_BUILD_TYPE=Release
cmake --build build/host --config Release --parallel
python scripts/verify.py --output docs/validation/host.json
python scripts/generate_fixture.py build/host/sensor_hub_sim --check
python scripts/check_originals.py
```

Use the `.exe` path on Windows, or the `Release/` path with a multi-config build.
The original-integrity command requires the neighboring original checkouts;
the combined build/tests do not. On the local Windows machine the CMake/CTest,
compiler, and pinned vendor-source caches were reused read-only from existing
tool installations; all new build outputs are inside this project's `build/`.

- [Host report with command outputs and source/binary hashes](validation/host.json)
- [Watchdog-disabled build output](validation/stm32-off.txt)
- [Watchdog-enabled build output](validation/stm32-on.txt)
- [Live dashboard screenshot](validation/dashboard.jpg)
- [Loopback HTTP snapshot from the compiled-simulator service](validation/live-api.json)
- [Original import manifest](originals-manifest.json)

The earlier live demo briefly held the Windows simulator executable open during
a rebuild. The demo was stopped, the executable was relinked successfully, and
the complete host gate was rerun against the fresh build; the saved host report
records the successful final run.

## Remaining physical acceptance

The subsequent [Renode execution report](validation/RENODE-20261017.md) adds
actual ARM/FreeRTOS execution, sensor faults, reboot and watchdog tests. It also
records target-only fixes that host compilation had not exposed. See
[the setup guide](RENODE.md) for reproduction and model limitations.

Validate flash-journal retention across reset/power interruption, first flashing
and updates preserving sector 7, sensor startup/recovery, actual UART reconnects,
watchdog operation, stack margins, timing, and sustained Pi operation on the
identified hardware. The simulator's times and sample signals are deterministic
models. This work makes no new physical validation or measured-hardware claim.
Gateway history/session baselines remain in memory; ingress remains unauthenticated.
The journal has 6553 append slots and requires reprovisioning with a new epoch
after intentional erase or exhaustion.
