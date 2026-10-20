# STM32F401RE firmware

**Combined-project update:** UART now emits shared v3. Flash sector 7 is reserved
for the persistent boot journal, leaving 384 KiB for the image. Normal flashing
must preserve the journal; intentional journal/mass erase requires a new
`HUB_DEVICE_EPOCH`. Read the [provisioning policy](../../../protocol/README.md#boot-identity-and-provisioning).
Commands below run from `firmware/`; the combined root README uses `-S firmware`.
Earlier size/timing results below are historical; current results are in
[combined validation](../../../docs/VALIDATION.md).

This directory contains a complete CMake firmware build for NUCLEO-F401RE:
board setup, interrupt routing, FreeRTOS configuration, startup, and linker
layout. Startup and vendor sources are fetched at fixed upstream commits.
Both watchdog configurations compile and link; **the resulting image has not
yet been flashed or validated on a physical board**.

## Build

Install Git, Ninja, CMake 3.20+, Python 3.10+, and Arm GNU Toolchain 13.2.Rel1.
Place the toolchain's `bin` directory on PATH. From the repository root:

```bash
cmake -S . -B build/stm32 -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DHUB_BUILD_STM32=ON -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/stm32 --parallel
```

In PowerShell, quote `"-DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake"`.
The first configure needs network access. Host builds fetch no vendor sources.
To use an already downloaded dependency tree, CMake supports
`-DFETCHCONTENT_BASE_DIR=/absolute/path/to/dependencies`.

Artifacts are under `build/stm32/target/stm32f401re/`: ELF, HEX, BIN, and MAP.
A post-link check verifies flash size, the initial stack pointer, Reset/SVC/
PendSV/SysTick/TIM2 vectors, a bounded libc heap, and real FreeRTOS/application
symbols. These checks verify image structure, not execution.

Use STM32CubeProgrammer and the board's ST-LINK connection to load the HEX
during bring-up. No hardware programming is performed by the build or CI.

## Pinned dependencies

HAL and device revisions are the pair from
[STM32CubeF4 v1.28.1](https://github.com/STMicroelectronics/STM32CubeF4/tree/v1.28.1).
CMSIS Core matches that release's CMSIS 5.9.0 version.

| Component | Version / commit | Upstream license |
|---|---|---|
| [STM32 HAL](https://github.com/STMicroelectronics/stm32f4xx-hal-driver/tree/c2e1406d7ea4b73aa42b98ddeb75a8670b1a5a16) | v1.8.3 / `c2e1406d7ea4b73aa42b98ddeb75a8670b1a5a16` | BSD-3-Clause |
| [STM32 CMSIS device](https://github.com/STMicroelectronics/cmsis-device-f4/tree/5f41fb29d22773896c780052bf61e47fc924d524) | v2.6.10 / `5f41fb29d22773896c780052bf61e47fc924d524` | Apache-2.0 |
| [CMSIS Core](https://github.com/ARM-software/CMSIS_5/tree/61e36449f53c25ef7825c40f7dd93685736f457f) | 5.9.0 / `61e36449f53c25ef7825c40f7dd93685736f457f` | Apache-2.0 |
| [FreeRTOS kernel](https://github.com/FreeRTOS/FreeRTOS-Kernel/tree/f388a5c8078e152913e4eb3c5d75bf89561392df) | V11.1.0 / `f388a5c8078e152913e4eb3c5d75bf89561392df` | MIT |

Sources remain in the ignored build tree with their license files. The project
does not relabel third-party code as its own. CI downloads Arm GNU 13.2.Rel1
from Arm and verifies the archive's published SHA-256 checksum.

## Board configuration and wiring

| NUCLEO-F401RE | Connection | Configuration |
|---|---|---|
| 3V3 / GND | Both breakouts | 3.3 V-compatible modules and common ground |
| PB8 / D15 | SCL | I2C1, 400 kHz; external 4.7 kOhm pull-up to 3.3 V |
| PB9 / D14 | SDA | I2C1; external 4.7 kOhm pull-up to 3.3 V |
| PA2 / PA3 | ST-LINK VCP TX/RX | USART2, 115200 baud, 8-N-1 |

MPU6050 address `0x68` assumes AD0 low. BMP280 address `0x76` assumes SDO low.
Verify breakout identity; BMP180/BME280 variants are not this driver's target.

- Internal 16 MHz HSI feeds PLL M=16, N=336, P=4: nominal 84 MHz SYSCLK.
- APB1 is 42 MHz; TIM2 receives 84 MHz. Prescaler 8399 and period 99 give
  nominal 100 Hz. Oscillator accuracy must be checked on hardware.
- TIM2 priority is **5**, numerically greater than or equal to
  `configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY`. Lower numeric values mean
  higher urgency and must not call FreeRTOS APIs.
- SVC and PendSV route directly to the Cortex-M4F kernel port. SysTick advances
  the HAL millisecond clock and forwards to FreeRTOS after scheduler start.
  Blocking HAL calls must not run in interrupts or critical sections.
- The linker allows 512 KiB flash and 96 KiB SRAM. FreeRTOS uses a 24 KiB
  `heap_4` array; libc has a separate bounded 4 KiB heap for float formatting.
  At least 2 KiB is reserved for the main/interrupt stack.
- `configASSERT`, allocation-failure hooks, and stack-overflow checks remain
  enabled in MinSizeRel. Task stacks retain their conservative sizes until
  on-board high-water measurements justify reductions.

## Watchdog and diagnostics

The default build leaves IWDG disabled. The health field in telemetry reports
the latest eligibility decision (`fed`/`withheld`); with IWDG disabled it does
not prove a physical refresh. The port never refreshes an uninitialized IWDG.

After verifying UART and health, create an enabled build:

```bash
cmake -S . -B build/stm32-watchdog -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DHUB_BUILD_STM32=ON -DHUB_ENABLE_WATCHDOG=ON -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/stm32-watchdog --parallel
```

IWDG starts after RTOS objects are created and before scheduler start. Its
nominal timeout is 2 seconds; LSI tolerance affects the actual timeout. Once
started, IWDG cannot be disabled without resetting the MCU.

Debugger symbols available for physical evidence:

- `hub_reset_flags`: RCC reset-cause flags captured before clearing at boot.
- `hub_stack_high_water_words[4]`: acquisition, processing, telemetry, and health
  task stack minima, sampled once per second. One word is four bytes here.
  Values are zero until the first health check; no safe stack margin is claimed.
- Existing DWT telemetry fields: unsigned cycle deltas converted to microseconds.

## Bring-up acceptance gates

1. Flash with IWDG disabled; verify 3.3 V, sensor identities, and I2C waveforms.
2. Capture nominal 100 Hz TIM2 and at least 60 seconds of valid 10 Hz UART data.
3. Measure DWT distributions over at least 10 minutes, including worst-case
   retries. Establish whether the 10 ms acquisition budget holds with margin.
4. Physically unplug/replug each sensor and both; capture readiness and recovery.
5. Enable IWDG, suspend each task, and record reset-cause evidence after reboot.
6. Record stack minima under nominal and fault loads, then perform soak and
   power-cycle/brownout tests.

## Optional CubeMX integration

A generated CubeMX project is not required for the supplied build. To reuse the
adapter in an existing CubeMX project, copy only `app_freertos` and
`hub_stm32_port` headers/sources plus the portable core. Supply equivalent
peripheral handles and a compatible 1 kHz FreeRTOS configuration. Call
`HubRtos_Init()` before scheduler start and forward the TIM2 HAL callback.
Do not duplicate the supplied `main.c`, interrupt handlers, or HAL tick
implementation alongside Cube-generated equivalents.

See [validation evidence](../../docs/VALIDATION.md) for compiled artifacts and
the remaining physical-board checklist.
