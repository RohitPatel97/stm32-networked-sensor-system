# Validation record and hardware checklist

This document separates reproducible repository checks from work that requires
the named hardware. Do not mark a hardware item complete without attaching the
artifact (build log, capture, or test note) to a release or pull request.

## Automated host checks

Recorded October 12, 2026 on Windows: a CMake 4.4.2 / Ninja Release rebuild
using Zig 0.16.0 / Clang 21.1.0 passed with strict warnings. Python 3.12.14 ran
the tooling checks. CTest passed all three suites: 6 C groups, 12 simulator
scenarios, 2 simulator-to-dashboard recovery replays, 69 CLI rejection cases,
and 22 Python tests. Existing ignored executables were not used as validation
of the changed source. Physical target validation remains pending below.

The local Zig setup used `zig cc` as the C compiler and a local `zig ar` wrapper
as CMake's archiver. Standard GCC/Clang CMake commands remain the CI path.

| Check | Command / CI test | Pass criterion |
|---|---|---|
| Strict C build | `cmake --build build` | GCC/Clang completes with `-Wall -Wextra -Wpedantic -Werror` |
| Portable unit tests | `core_unit_tests` | six groups cover retry/mutex accounting, BMP280 vector, conversion/formatting, sensor transitions/saturation, heartbeat deadlines across clock wrap, and formatter failure/buffer contracts |
| Nominal schedule | `simulator_integration_tests` | 200 samples and 20 frames in 2 s; no errors/drops; two watchdog feeds |
| Bus/sensor fault | same integration test | BMP goes offline, MPU remains online, retry/error/disconnect counters rise |
| Queue pressure | same integration test | raw queue drops are nonzero and simulation remains bounded |
| Task stall | same integration test | watchdog refresh is withheld and deadline misses rise |
| Starved processing | same integration test | acquisition stall at 1200 ms records four misses at 2/3 s; boot stall never establishes acquisition or processing health |
| Recovery | same integration test | MPU, BMP, both-device, and startup-absence payloads recover; one-cycle faults do not flap status |
| Invalid CLI inputs | same integration test | 69 malformed and out-of-range inputs exit 2 promptly without telemetry |
| Dashboard recovery | same integration test | all 20 frames render for each of MPU-only and both-sensor outages; null payloads display `n/a`, then data returns |
| Python tooling | `python_tool_tests` / `python -m unittest discover -s tests -p "test_*.py" -v` | 22 tests cover parser bounds, null payloads, malformed-line continuation, file/stdin replay, and rendering |

The host CI job establishes host behavior. The separate STM32 job compiles and
links the complete firmware against pinned real HAL/CMSIS/FreeRTOS sources;
neither job executes the firmware on an MCU.

Integration tests use subprocess timeouts so a regression in argument handling
cannot leave a CI runner stuck in an unbounded simulation. The complete
problem / solution mapping is in [the simulator guide](SIMULATOR.md).

## STM32 build and optimization evidence

October 12, 2026: Arm GNU 13.2.Rel1 (GCC 13.2.1), CMake 4.4.2, Ninja,
MinSizeRel (`-Os`), Cortex-M4F hard-float ABI. The application/core compile with
`-std=c99 -Wall -Wextra -Wpedantic -Werror`. HAL 1.8.3, CMSIS device 2.6.10,
CMSIS Core 5.9.0, and FreeRTOS V11.1.0 are pinned by commit in the
[target build](../target/stm32f401re/CMakeLists.txt).

| Image | Flash used | RAM accounted by linker | Result |
|---|---:|---:|---|
| IWDG disabled | 36,916 bytes / 512 KiB | 32,336 bytes / 96 KiB | ELF/HEX/BIN linked; layout checks passed |
| IWDG enabled | 37,028 bytes / 512 KiB | 32,336 bytes / 96 KiB | ELF/HEX/BIN linked; layout checks passed |

RAM accounting includes the FreeRTOS heap and reserved libc heap/main stack;
it is not a measured runtime high-water mark. CI retains linked images, maps,
build logs, and compiler `.su` reports under `stm32f401re-firmware` artifacts.
The post-link checker validates five interrupt vectors, the initial stack
pointer, flash bounds, libc heap placement, and required kernel/application
symbols. Four local negative checks rejected truncated/oversized images, a bad
initial stack pointer, and an incorrect SVC vector. These are file-layout checks.

The telemetry formatter previously used two temporary arrays (192 + 160 bytes).
Direct bounded writes remove them. With the same Arm GCC `-Os` flags, its `.su`
report changes from 448 bytes to 96 bytes; the append helper uses 28 bytes.
This excludes libc callees and is not a timing or total-task-stack measurement.
Eight nominal/fault scenarios produce byte-identical telemetry and summaries
before/after the optimization. New tests reject NaN/infinity in all ten active
float fields, verify exact buffer capacity, clear output on failure, and allow
ignored invalid-sensor fields to remain non-finite.

## Physical bring-up checklist (currently unverified)

- [x] Compile/link both watchdog configurations and retain build/version evidence.
- [ ] Record exact NUCLEO-F401RE revision and sensor breakout part markings;
  record the firmware commit and programming/debugger tool versions on the board.
- [ ] Verify 3.3 V rail and idle-high SCL/SDA before attaching sensors.
- [ ] Capture 400 kHz I2C identity and burst-read transactions on a logic analyzer.
- [ ] Confirm TIM2 interrupt frequency is 100.00 Hz and ISR priority satisfies
  FreeRTOS syscall constraints.
- [ ] Capture at least 60 seconds of 10 Hz NDJSON with zero malformed lines.
- [ ] Report DWT min/mean/p95/p99/max for acquisition and processing over at
  least 10 minutes; verify acquisition max remains safely below 10 ms.
- [ ] Disconnect MPU6050 and BMP280 separately; attach status/counter captures.
- [ ] Inject recoverable NACKs or bus contention and confirm retry accounting.
- [ ] Create queue pressure in a debug build and confirm drop accounting.
- [ ] Suspend each task separately and demonstrate IWDG reset after refresh is
  withheld. Record reset cause from RCC flags on the following boot.
- [ ] Capture `uxTaskGetStackHighWaterMark` for every task and right-size stacks
  with at least the project's required safety margin.
- [ ] Complete an application-appropriate soak test and power-cycle/brownout test.

## Suggested evidence naming

Use `validation/YYYY-MM-DD/` in a release artifact (large binary captures do not
need to live in Git) with files such as `build.log`, `uart.ndjson`,
`i2c-nominal.png`, `bmp-disconnect.ndjson`, and `timing-summary.md`. Record the
Git commit SHA in `timing-summary.md` so results remain traceable.
