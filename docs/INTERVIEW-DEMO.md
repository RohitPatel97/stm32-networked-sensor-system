# STM32 Renode interview demo

Prepared for Tuesday, October 20, 2026. The demo runs the compiled STM32F401RE
Cortex-M4F firmware, STM32 HAL and FreeRTOS in Renode. MPU6050 and BMP280 register
models supply I2C data; the firmware sends the shared telemetry protocol through
USART2 to the Python gateway and live dashboard.

## Launch on this computer

1. Double-click **STM32 Sensor Lab** on the Desktop.
2. Wait for the live overview. Startup checks actual firmware telemetry before
   showing the dashboard inside this single Windows app.
3. Use **Run all tests** to run the full suite. Close the app window when finished;
   it saves an interrupted run and stops its gateway and Renode process.

This prepared checkout runs offline with its installed `.venv`, portable Renode,
and compiled watchdog-enabled ELF. No administrator access, downloads, build,
or physical board is needed to present it. Renode runs in the background; the
dashboard buttons control that emulator through its private monitor pipe.
Opening the app again brings the existing window forward.

## A three minute walkthrough

| Step | Action and expected observation | Explain |
| --- | --- | --- |
| 1 | Both sensors online; acceleration `0 / 0 / 1` g, gyro `0 / 0 / 0` deg/s, altitude `56.1` m | Separate acquisition, processing, telemetry and health tasks run under FreeRTOS. Stationary Z acceleration includes gravity. |
| 2 | Click **Show acceleration test**. Acceleration becomes `0.5 / -0.25 / 0.75` g; gyro becomes `5 / -20 / 10` deg/s. The app displays a verified sample and report link, and holds the values. | Changes enter at the sensor registers and pass through the actual drivers and unit conversions. The three axes and their signs are checked against expected values. |
| 3 | Click **Change altitude**. Pressure becomes about `946.4` hPa and altitude `571.8` m | Firmware derives altitude from barometric pressure using a 101325 Pa reference. |
| 4 | Click **Disconnect IMU**. IMU fields become `—`, device becomes degraded, BMP280 keeps reporting | An I2C NACK exercises retries, bounded failure handling, partial telemetry and alerts. |
| 5 | Click **Restore / stationary**. Both sensors return online and recovery alerts appear | Firmware redetects and initializes the sensor without rebooting. Lifetime I2C totals stay visible. |
| 6 | Click **Reboot MCU**. Boot ID increments and frame sequence restarts | The flash journal separates sessions while the board UID stays stable. |

For deeper questions, **Disconnect barometer** makes temperature, pressure and
altitude unavailable; **Disconnect both** preserves diagnostic telemetry.
**Reconnect UART** resumes the transport without changing Boot ID.
**Trigger watchdog** stops TIM2; the independent watchdog resets
the MCU, increments Boot ID and restores healthy telemetry. Wait for the green
“Verified” message before the next action. Finish with **Restore / stationary**.

Run **all tests** to show the 22-case suite and its saved HTML/JSON report. In
**Component tests**, expand a case to explain its expected versus observed
results. Use **Model coverage** to distinguish software behavior from electrical
validation. The [Sensor Lab guide](SENSOR-LAB.md) documents every case and launcher.

## What to say about the implementation

“I combined an RTOS sensor hub and a networked gateway around a shared,
versioned telemetry contract. The firmware handles sensor failures and maintains
boot identity; the gateway validates frames, tracks sessions, keeps bounded
history, and streams diagnostics to a browser. This demo executes the ARM ELF in
Renode and injects failures at the I2C and UART boundaries.”

The baseline temperature is 25.08 C and pressure is 1006.53 hPa. Frame and sample
sequences should advance, measurements should be current, queue drops should stay
zero, and the watchdog should be enabled with health eligible. Acquisition and
processing times are emulated diagnostics, not measured physical-board timing.

Renode's sensor models establish digital behavior, not electrical integrity or
sensor accuracy. The RCC model does not distinguish watchdog and manual reset
causes reliably: use the boot transition and Renode IWDG log as evidence. Reset
flags remain visible but should not be presented as physical reset-cause proof.
A new launcher process creates a fresh virtual board with Boot ID 1; MCU resets
inside that process preserve UID and journal.

## Rehearse and recover

From PowerShell in the project folder, run a preflight that exits automatically:

```powershell
& '.\Launch Interview Demo.cmd' --check-only --no-browser
```

- If fields show `—` after a fault, use **Restore / stationary**. The
  absence of measurements during disconnection is intentional.
- If the browser says Reconnecting, keep or reopen the launcher. A browser tab
  alone does not run the firmware or gateway.
- If port 8765 belongs to an older demo or another service, stop that service or
  run `& '.\Launch Interview Demo.cmd' --port 8766`. The launcher never kills an
  unrelated process to take its port.
- If startup fails, the console stays open with an error. Logs and preflight
  evidence are under `var/interview/`. Setup instructions are in [RENODE.md](RENODE.md).
- This launcher is prepared for this checkout. Copying only the `.cmd` to another
  computer is insufficient; that computer also needs Python dependencies, Renode,
  the project sources and the ELF.

See [the full system test results](validation/FULL-SYSTEM-20261018.md).
