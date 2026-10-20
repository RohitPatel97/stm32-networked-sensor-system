# FreeRTOS Sensor Hub workflow

## October 12, 2026: complete firmware build and formatter optimization

Published implementation: [fc7386e](https://github.com/RohitPatel97/freertos-sensor-hub/commit/fc7386e8c963c550c6078d64fd798868b1ad7dd5).

**Problem:** the target adapter could not produce a firmware image without an
external generated project. Dependency versions, startup, memory layout, and
interrupt routing were unverified. Telemetry used 352 bytes of temporary arrays,
could format NaN/infinity as invalid JSON, and left old data after some failures.
The README had grown to 347 lines and mixed quick-start instructions with details.

**Solution:** supply a complete Arm GNU CMake build with pinned real HAL/CMSIS/
FreeRTOS sources, board setup, startup, linker script, bounded libc heap,
interrupt routing, and explicit watchdog off/on builds. Add post-link vector/
memory/symbol checks, CI firmware artifacts, reset-cause and stack diagnostics.
Write telemetry directly into the caller buffer, reject non-finite active
payloads, clear writable output on failure, and reuse the encoded byte count
for UART transmission. Require Python for test builds. Reduce README to 148
lines and move reference material into linked architecture/simulator guides.

**Regression cases and observed results:**

- New C regression failed on the original formatter accepting a non-finite
  payload. Six C groups now pass, including all ten float fields, exact capacity,
  truncation, null arguments, ignored invalid-sensor fields, and output clearing.
- Host CTest passes all three suites: 12 simulator scenarios, 2 dashboard
  recovery replays, 69 CLI rejections, and 22 Python tests, alongside the C tests.
- Eight nominal/fault simulator cases retain identical telemetry/summary bytes.
- Real Cortex-M4F builds pass with IWDG OFF and ON using Arm GNU 13.2.Rel1 /
  GCC 13.2.1. Default flash usage is 36,916 bytes; enabled usage is 37,028 bytes.
  Both account for 32,336 bytes of RAM, including reserved heaps/main stack.
- Image checks pass for five vectors and kernel/application symbols; four
  corrupted-image cases are rejected. Linker segments separate executable flash
  from writable RAM. Python syntax and relative documentation links pass.
- Arm GCC `-Os` stack reports: formatter 448 -> 96 bytes, append helper 28 bytes.
  This excludes libc callees; it is not measured total task stack or latency.

**Remaining limits:** the software/build deliverable is complete, but physical
bring-up is still required. No claim is made for successful flashing, electrical
operation, sensor readiness, target timing, live UART, watchdog resets, stack
margins, or soak reliability. The default watchdog-disabled image reports health
eligibility rather than actual watchdog refresh. Review the acceptance gates in
[the target guide](target/stm32f401re/README.md) before hardware use.

## October 1, 2026: telemetry fault recovery

Published implementation: [9c1ca0a](https://github.com/RohitPatel97/freertos-sensor-hub/commit/9c1ca0a8ac1e5ac4417a164dfa4abaad1f2c8007).

**Problem:** a valid `imu: null` frame crashed the dashboard. Invalid UTF-8 and
oversized numeric values could also escape malformed-frame handling. The
simulator refreshed processing health even when no input was available, unlike
the STM32 adapter.

**Solution:** render missing payloads as `n/a`, decode captures line by line,
normalize parser failures, validate schema and unsigned wire widths, and provide
stdin replay with `--file -`. Processing now heartbeats only after a sample is
processed. CTest discovers the complete Python suite alongside C/integration
checks. README and protocol documentation include the updated behavior and demo.

| Regression case | Reproduced before the fix | Observed after the fix |
|---|---|---|
| MPU/all-sensor outage followed by recovery | Null IMU raises `TypeError` while rendering | All 20 snapshots render in each of two compiled-simulator recovery replays; missing values become `n/a` and later recover |
| Malformed input followed by a valid frame | Bad UTF-8, huge sensor integers, and decoder limits can abort the session | Bad lines are reported/skipped; subsequent valid frames are decoded and rendered |
| Schema and integer boundaries | `true`/`1.0` schema accepted; oversized integer fields accepted | Only integer schema 1 and uint32/uint64 field ranges accepted; maxima, zero, explicit null, and additive fields remain supported |
| Acquisition stalls at 1200 ms or from boot | Existing simulator reports only two deadline misses | Fresh simulator reports four misses, accounting for acquisition and processing; watchdog refresh withheld |

**Validation:** fresh CMake 4.4.2 / Ninja Release build on Windows, Zig 0.16.0 /
Clang 21.1.0, Python 3.12.14. All three CTest suites passed: 5 C test groups,
12 simulator scenarios, 2 dashboard recovery replays, 69 CLI rejection cases,
and 22 Python tests. Sample dashboard replay, Python syntax compilation, and
diff review passed. The old ignored executable was used only to reproduce the
heartbeat bug; acceptance ran against a fresh build of the changed sources.

**Remaining limits:** these are host tests and deterministic device/scheduling
models. Target compilation, physical UART/I2C behavior, measured timing,
watchdog resets, stack margins, and soak reliability still require hardware
artifacts. The NDJSON wire format remains version 1; previously accepted
malformed frames can now be rejected. Replay tests do not establish live serial
transport behavior or physical sensor recovery time.

## September 4, 2026: completed update

- Added temporary sensor-outage injection and reconnect summary counters.
- Shared sensor-status code between the host simulator and STM32 adapter.
- Added recovery, startup-absence, brief-dropout, status saturation, clock-wrap,
  and invalid-command-line regression cases.
- Added invalid-ADC and corrupt-calibration regressions for BMP280 compensation;
  use a fresh build, since existing ignored executables may predate source fixes.
- Documented the engineering problem, solution, and acceptance criteria in
  [README.md](README.md); hardware gates remain in [VALIDATION.md](docs/VALIDATION.md).

## Reproduce validation

Use a C99 compiler, CMake 3.20+, and Python 3.10+. Test-enabled builds require
Python and run all three suites. Standard GCC builds run in GitHub CI; local
Windows host validation uses Zig 0.16.0 / Clang 21.1.0 and a `zig ar` wrapper.

```bash
cmake -S . -B build/host -DCMAKE_BUILD_TYPE=Release
cmake --build build/host --config Release --parallel
ctest --test-dir build/host -C Release --output-on-failure
python -m unittest discover -s tests -p "test_*.py" -v
python tools/dashboard.py --file examples/sample_telemetry.ndjson --no-ansi --max-frames 1
```

The host suite contains 6 C test groups, 12 simulator scenarios, 2 dashboard
recovery replays, 69 CLI rejection cases, and 22 Python tests. Use the separate
[target build commands](target/stm32f401re/README.md) to compile/link actual
FreeRTOS firmware; neither build measures execution timing on an MCU.

## Next three tasks

1. Flash the watchdog-disabled image on the identified board and capture sensor
   identity, I2C waveforms, TIM2 frequency, and 60 seconds of valid UART telemetry.
2. Measure sensor startup/reinitialization readiness and worst-case I2C retries;
   collect physical unplug/replug and waveform evidence before claiming a
   hardware recovery time or 10 ms acquisition deadline.
3. Capture watchdog resets and task stack high-water marks on the board, then
   evaluate DMA UART only if measurements justify the additional complexity.

For each change, record the problem, solution, focused regression, observed
result, remaining limits, and published implementation commit here. Run the
commands above, review the diff, and inspect CI after publishing.
Resume claims may cite embedded C, STM32/FreeRTOS design, I2C/UART, interrupts,
bounded queues, mutexes, fault injection, unit/integration testing, CMake, and CI.
Actual target compilation is now verified. Physical execution, logic-analyzer
captures, timing, and soak tests remain unverified until artifacts are recorded.
