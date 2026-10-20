# Sensor Lab validation on October 19 2026

The desktop demo executes the STM32F401RE ARM ELF in Renode 1.17.0 and streams
its USART2 output through the production Python gateway into the new Sensor Lab
console. The complete 22-case suite passed from both the browser and the desktop
launcher. Both original project repositories remain unchanged: all 116 source
hashes match their pre-integration manifest.

## Reproducible evidence

- [Full suite JSON](sensor-lab-20261019/full-suite.json) and
  [printable HTML](sensor-lab-20261019/full-suite.html): 22 passed, zero failed,
  zero cancelled; final sensors restored and board online.
- [Host acceptance results](sensor-lab-host-20261019.json): 272 Python tests,
  all four CTest groups, protocol schema,
  shared fixture, gateway lint, console JavaScript syntax and Python regressions,
  with source and binary hashes.
- [Initial discrepancy report](sensor-lab-20261019/initial-failures.json): 20
  passed and two failed before the GPIO model and UART test fixes.
- [Cancellation report](sensor-lab-20261019/cancellation.json): one stability case
  cancelled, no false pass, board restored.
- [Console screenshot](sensor-lab-20261019/console.png).

The final suite ran at 17:24–17:25 PDT on October 19. Report timestamps and IDs
use UTC, so their date is October 20. The firmware SHA256 is
`46598310fdb612481b9822c262aadf6730b7827a6f275a23415122bf34458372`.
The final JSON contains each test's expected values, actual assertions, elapsed
time, before/after telemetry and the precise monitor-log directory.

## Observations

| Check | Observed result |
| --- | --- |
| Sensor values | Acceleration and gyro XYZ, signed endpoints, both temperatures, compensated pressure and derived altitude passed |
| I2C faults | Individual and simultaneous sensor removal produced null payloads, incremented diagnostics and recovered without reboot |
| GPIO and clocks | PA2/3 AF7, PB8/9 AF4/open-drain register storage and used RCC enable bits passed |
| RTOS timing | TIM2/NVIC, SysTick/DWT and 100 Hz sample / 10 Hz telemetry cadence checks passed |
| Identity | UID matched telemetry; committed flash journal validated; MCU reset and IWDG advanced Boot ID |
| Transport | Actual UART bridge disconnect/reconnect passed; WebSocket snapshot/update and decimal-string Boot ID passed |
| 30-second stability | 301 accepted frames; heap stayed at 12,008 bytes; history bounded at 600 |
| Stack margins after stability | Acquisition 286, processing 413, telemetry 983, health 225 words |
| New stability errors | Zero parser errors, ordering anomalies, device rejections, WebSocket drops and firmware queue drops |
| Final gateway totals | 1,824 received/accepted frames, zero parser/order/rejection/WebSocket-drop errors; four session transitions and two UART reconnects across two suites in that process |

The stability test is a short regression test, not a long-term leak qualification.
The earlier sustained stress results remain in [STRESS-20261017.md](STRESS-20261017.md).

## Fixes and UI verification

The first run found that Renode's GPIO output-type register did not retain the
firmware's writes. The upstream model declares these as tagged bits.
`HubGPIOPort.cs` now stores OTYPER writes, masks reserved bits and clears them on
reset while delegating other behavior to the stock model. This verifies digital
configuration only. See the [upstream GPIO model](https://github.com/renode/renode-infrastructure/blob/master/src/Emulator/Peripherals/Peripherals/GPIOPort/STM32_GPIOPort.cs).

The UART test initially expected BRR 365. STM32 HAL quantizes its intermediate
divider before rounding the fraction; at 42 MHz and 115200 baud it writes 364.
The corrected expectation agrees with the actual firmware and HAL algorithm.
See the [STM32 UART HAL macros](https://github.com/STMicroelectronics/stm32f4xx-hal-driver/blob/master/Inc/stm32f4xx_hal_uart.h).

Browser verification covered the complete suite, a selected-case cancellation,
manual IMU removal and restoration, report history across process restart, and
HTML download. During IMU removal all acceleration values displayed unavailable
while BMP280 remained online. Recovery restored the baseline. The downloaded
HTML's SHA256 matched the saved report. No browser console errors were recorded.
The mobile layout at 390 px and desktop layout at 1440 px fit the viewport; the
desktop view used two columns. Temporary viewport overrides were reset.

Host regressions also check failed-case persistence with subsequent-case
continuation, concurrent-control rejection, cancellation and shutdown cleanup,
manual failures, escaped HTML, bounded report history, invalid report paths,
authentication and selection validation, and an interrupted report write that
must not produce a pass. Renode controls remain absent from the production-only
gateway application.

## Scope

All active hub components have functional, configuration or diagnostic checks in
the [case catalog](../SENSOR-LAB.md). Support-only RTC/PWR/bit-band features and
unused MCU peripherals are explicitly labelled. The sensor models do not prove
electrical integrity, physical accuracy, oscillator timing, flash wear or behavior
under power interruption. Reset-cause flags are not faithful in the RCC model.
Known model warnings for flash/cache/RCC and I2C timing bits remain visible in the
logs. New emulator processes create fresh virtual boards; MCU resets retain the
current process's UID and journal.
