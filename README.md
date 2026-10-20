# STM32 Sensor Hub and Networked Monitoring System

[![Integrated sensor system](https://github.com/RohitPatel97/stm32-networked-sensor-system/actions/workflows/ci.yml/badge.svg)](https://github.com/RohitPatel97/stm32-networked-sensor-system/actions/workflows/ci.yml)

A complete software path from embedded C sensor acquisition to a live network
dashboard: **STM32F401RE / FreeRTOS -> UART JSON -> Python gateway -> REST and
WebSocket -> browser**. MPU6050 and BMP280 drivers acquire at 100 Hz; telemetry
publishes at 10 Hz. The compiled C simulator exercises the same driver,
processing, sensor-state, and telemetry code without a board.

This project combines the two original repositories without modifying them.
Their source revisions and file hashes are recorded in
[provenance](docs/PROVENANCE.md). This repository is independent: no source
checkout from either original is required to build or run it.

## What is integrated

- One [v3 UART contract](protocol/README.md) with an exported JSON Schema,
  generated C fixtures, strict validation, and cross-language regressions.
- Stable board identity and a persistent boot counter. Old sessions, duplicate
  frames, downgrades, and backward samples cannot refresh health or alerts.
- Separate transmitted-frame and acquired-sample sequences. Health continues
  arriving when acquisition stalls, without making old measurements fresh.
- Partial sensor records, including both sensors absent, remain valid. Missing
  values are explicit `null` and render as `n/a`, with gaps in the chart.
- Cumulative counters remain available after recovery. Current error alerts
  clear after one second of producer time without a new counter increment.
- Gyroscope, altitude, I2C retries, deadline misses, timing, watchdog eligibility,
  reset flags, sample age, and boot identity reach the API and dashboard.
- Existing bounded history, client queues, alerts, serial reconnect framing,
  REST routes, and gateway v1/v2 compatibility remain available.

```text
firmware/          C core, simulator, STM32 target, and firmware tests
gateway/           Python service, browser dashboard, legacy tools/tests
protocol/          Shared UART specification, JSON Schema, generated fixture
tests/integration/ Compiled C -> gateway -> API/WebSocket regression tests
tests/dashboard.cjs Production dashboard exercised with real received events
scripts/           Demo, schema export, validation, original-integrity check
deploy/            Combined-project service configuration
docs/              Architecture, provenance, and observed validation evidence
```

## Verify the combined project

```sh
python scripts/verify.py
```

Run this after the host build. It runs firmware CTest, all gateway/integration
tests, schema-drift checking, and gateway lint. CI also cross-compiles STM32
images with the hardware watchdog disabled and enabled.

See [the validation record](docs/VALIDATION.md) for commands, observed results,
and limits. The browser was also inspected against a running local service.

For sustained memory, WebSocket reconnect, serial-lifecycle, and reboot checks,
see [stress testing](docs/STRESS-TESTING.md). The runners save resource samples,
failure accounting, and source hashes for repeatable comparisons.

## Connect a physical board

Build the target with Arm GNU 13.2.Rel1 on PATH:

```sh
cmake -S firmware -B build/stm32 -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DHUB_BUILD_STM32=ON -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/stm32 --parallel
```

PowerShell should quote the `-DCMAKE_TOOLCHAIN_FILE=...` argument. The linker
reserves flash sector 7 for the boot journal. **Read the [boot provisioning
policy](protocol/README.md#boot-identity-and-provisioning) before flashing:**
ordinary updates must preserve that sector; an intentional journal/mass erase
requires a new `HUB_DEVICE_EPOCH` and therefore a new device identity.

For a Linux/Pi gateway, install the gateway package and set:

```sh
SENSOR_MONITOR_SOURCE=serial SENSOR_MONITOR_SERIAL_PORT=/dev/ttyACM0 sensor-monitor serve
```

Use the matching COM port on Windows. The firmware's USART2 uses PA2 TX / PA3 RX
at 115200 8N1; select the board's virtual COM port or a suitable 3.3 V adapter.
Pin/wiring details and the watchdog gate are in the
[target guide](firmware/target/stm32f401re/README.md).

Physical flashing, power-interruption behavior, UART transport, sensor timing,
watchdog resets, and a Pi soak remain unverified. Firmware cross-compilation and
host tests establish software/build behavior only. Gateway state is in memory
and resets on restart; sender identity is not authenticated.
