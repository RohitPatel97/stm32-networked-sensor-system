# STM32 Sensor Lab desktop demo

Double-click **STM32 Sensor Lab** on the Windows Desktop. This single Windows
app starts the compiled Cortex-M4F firmware in Renode, checks live telemetry, and
shows the dashboard, fault controls, tests and reports in one window. No separate
terminal or browser is needed. Closing the window saves an interrupted test run
and stops its gateway and emulator. Opening it again brings forward the existing
window, or starts a fresh board session with your saved reports still available.

Select **Run all tests** inside the app to run all 22 tests. A healthy run takes
about 45 seconds, including a 30-second stability observation. If a matching
command-line demo was already running, the app attaches to it and leaves that
externally owned service running on close. An unrelated service occupying port
8765 produces an error with a Retry button; it is never terminated by this app.

## What the console shows

- **Live overview:** acceleration XYZ, gyro XYZ, both temperatures, pressure,
  derived altitude, board UID, Boot ID, watchdog, sample/frame sequences,
  acquisition/processing timing, I2C failures, queue drops and recent events.
  Heap and stack margins are timestamped snapshots taken by the memory tests.
- **Component tests:** choose a component or individual cases. Expand a case
  to see its method, expected values and observed assertions. Run one case,
  a selected group, or the complete suite.
- **Run reports:** download printable HTML or machine-readable JSON. Reports
  survive browser reloads and closing/relaunching the program.
- **Model coverage:** maps each part of the configured hub to its checks and
  describes the limits of the emulator.

The control buttons inject motion, pressure changes, missing sensors, UART
disconnects, MCU resets and a timer stall that triggers the independent watchdog.
Each manual action also saves a report. Use **Restore / stationary** to return
to the default readings. A disconnected sensor correctly displays an em dash.

### Show the acceleration test

Select **Show acceleration test** beside **Run all tests**. The app selects the
acceleration chart, scrolls to the instruments and writes the MPU6050 registers
through Renode. The actual ARM firmware must report **X +0.500, Y −0.250,
Z +0.750 g** before the check passes. The demonstration also sets gyro values to
**+5, −20, +10 deg/s**. Values remain visible until another test or restoration.

The evidence panel shows the expected values, a recorded firmware sample, Boot ID,
timestamp and a downloadable result. That recorded result stays visible after
restoring, while the live axes return to **0, 0, 1 g**. Press **Restore / stationary**
in the panel to restore both sensors.

**Run all tests** separately executes all 22 automated cases, including the
accelerometer check, and restores the stationary baseline at completion. These
controls are disabled while a suite is running to prevent conflicting register
changes. For only the automated acceleration check, select **Component tests →
Accelerometer XYZ → Run test**.

## Documented test cases

| ID | Component and check | Expected result |
| --- | --- | --- |
| baseline | ARM boot, sensor initialization and task health | Online; 25.08 C, 1006.53 hPa, [0,0,1] g, zero gyro; watchdog fed |
| accel | MPU6050 acceleration XYZ | [0.5,-0.25,0.75] g |
| gyro | MPU6050 gyro XYZ | [5,-20,10] deg/s |
| imu_temp | MPU6050 die temperature | Raw 3400 becomes 46.53 C |
| imu_limits | Signed IMU register endpoints | -32768, zero and 32767 scale correctly |
| baro_temp | BMP280 compensation | Raw 530000 becomes 28.25 C |
| altitude | BMP280 pressure and altitude | 946.43 hPa; altitude agrees with the reference-pressure formula |
| imu_fault | MPU6050 removal and reattachment | IMU unavailable, barometer valid; recovery without reboot |
| baro_fault | BMP280 removal and reattachment | Barometer unavailable, IMU valid; recovery without reboot |
| both_fault | Both I2C sensors absent | Diagnostic telemetry continues; both sensors recover |
| gpio | GPIOA/B modes and alternate functions | PA2/3 AF7; PB8/9 AF4, open-drain register bits set |
| clocks | Used RCC clock gates | GPIOA/B, TIM2, USART2 and I2C1 enabled |
| timer | TIM2 and interrupt setup | Enabled, prescaler 8399, reload 99; NVIC IRQ28 enabled |
| systick | RTOS tick and DWT counter | SysTick reload 83999 and interrupt enabled; DWT advances |
| cadence | Acquisition and telemetry scheduling | Approximately 100 samples/s and 10 frames/s in firmware time |
| memory | FreeRTOS heap and four task stacks | Positive free heap and all task stack margins above zero |
| flash | Boot journal and UID mapping | Valid committed complementary journal words; UID matches telemetry |
| reboot | MCU reset and session handling | Same UID, Boot ID +1, restarted sequence/uptime, healthy tasks |
| uart | USART2 setup and real bridge reconnect | UE/TX/RX enabled, BRR 364; new connection, unchanged Boot ID |
| watchdog | TIM2 stall and IWDG reset | Automatic reset; Boot ID +1 and healthy telemetry resumes |
| websocket | Actual gateway connection | Snapshot followed by telemetry; Boot ID remains a decimal string |
| soak | 30-second stability observation | Frames continue; stable heap/boot, positive stacks, bounded history; no new parser, ordering or drop errors |

Tests run serially against the actual ARM ELF. Each case first restores a known
baseline, so a selected subset can run independently. Fault controls are disabled
while a suite runs. **Stop after current test** allows the current check to finish,
then restores the board; the stability loop can stop at its next one-second
checkpoint. Remaining cases are marked cancelled. Failed cases retain their
evidence and the runner attempts subsequent cases and final recovery.

## Reports and reproducibility

Reports are written under `var/lab-reports/` before execution and after each case.
Each records status, UTC timestamps, duration, expected and actual checks,
before/after telemetry, firmware SHA256, model coverage and the run-log directory.
The report list displays the newest 50; older files remain on disk. A report left
running by an abrupt process termination is shown as incomplete, never passed.
The original failed development run remains available as evidence of the fixes.

Renode startup scripts, initial journal image, monitor logs and gateway logs are
under `var/interview/<timestamp>/`. Reports use atomic replacement. HTML escapes
all recorded text, and downloads accept only generated report IDs and HTML/JSON.

These PowerShell commands run from the combined project directory:

```powershell
# Single desktop app
& '.\Launch Sensor Lab.cmd'
# Open the console and immediately run every case
& '.\Launch Sensor Lab.cmd' --run-tests
# Run all cases without opening a browser; exit nonzero on a failure
.venv/Scripts/python.exe scripts/interview_demo.py --no-browser --test-and-exit
# Startup preflight only
.venv/Scripts/python.exe scripts/interview_demo.py --no-browser --check-only
# Use another local port if needed
& '.\Launch Sensor Lab.cmd' --port 8766
```

If `--test-and-exit` connects to an already running lab, it runs and waits for its
own report, then leaves that existing lab running. Tests cannot start over an
active demonstration or suite. Command-line flags retain the developer console
workflow; a no-argument launcher opens the desktop app. Missing runtime/ELF files
and occupied ports are reported in the app with a Retry button.

This prepared Windows checkout includes its Python environment, portable Renode
1.17.0, compiled ELF and native app at `build/desktop/SensorLab.exe`. It uses
Windows .NET Framework 4.8 and the installed Microsoft Edge WebView2 runtime.
The app runs offline, but its support files stay in this checkout: the EXE alone
is not a portable distribution. The former three desktop command launchers are
preserved under `demo/legacy-launchers/desktop/`.

To rebuild the app and recreate its desktop shortcut after moving the project:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_desktop.ps1 -InstallShortcut
```

The first build downloads a pinned Microsoft WebView2 SDK from NuGet and verifies
its SHA256; later builds use the cached package. See [RENODE.md](RENODE.md) for
Python/Renode dependencies and firmware builds. The native host source is
`desktop/SensorLab.cs`. It embeds the existing local UI; it does not duplicate
firmware, telemetry processing or test logic.

The app and all its gateway/Renode children belong to a Windows process job.
A private stdin handshake starts the backend only after job assignment. Window
closure sends a graceful shutdown, awaits report cleanup, and releases the job;
an abrupt app crash terminates its children. Reports interrupted by a crash
remain incomplete rather than falsely passed. App logs rotate at 1 MB with one
backup in `var/desktop/`; the embedded browser profile and downloaded report
copies are also stored there. Original reports stay in `var/lab-reports/`.

## Coverage limits and implementation details

All components in the hub's active acquisition and telemetry path have functional,
configuration or diagnostic checks. GPIO output-type storage is supplied by
`HubGPIOPort.cs` because the upstream GPIO model treats those bits as tags. The
extension stores the firmware's writes and resets them on MCU reset; it does not
simulate open-drain voltage or external pull-ups. The UART expectation uses the
STM32 HAL's quantized 42 MHz / 115200 calculation, yielding BRR 364 (about 115385
baud), rather than assuming a different rounding rule.

RTC/PWR/bit-band aliases are platform support, with no corresponding application
feature to demonstrate. ADC, SPI, CAN, USB and the user LED/button are unused by
this firmware. They are identified as support-only or unused in the coverage
view, not counted as tested features. Digital sensor models test register values,
drivers and conversions; electrical integrity, physical sensor accuracy, real
timing, flash wear and power interruption still require a physical board.

The model does not faithfully distinguish reset-cause flags. Watchdog recovery is
established by the induced stall, reset and advancing boot journal. A new emulator
process creates a new virtual UID and Boot ID 1; MCU resets within that process
retain its UID and journal. A 30-second stable heap is a useful regression check,
not a proof that all long-duration memory leaks are absent.

The console batches sensor register writes, renders at most four times per second,
keeps 90 chart frames and eight recent events, uses one reconnecting WebSocket,
and avoids overlapping status requests. Hidden pages pause rendering and polling.
The report index is cached and incrementally updated; Renode logs rotate at 2 MB
with three backups. Gateway queues and history remain bounded. Report archives
are deliberately retained for review and therefore grow with the number of runs.

For Tuesday's explanation, start with the live values, demonstrate **Tilt and
rotate**, disconnect and restore the IMU, then reboot the MCU. Finish by running
all tests and opening an HTML report to show how each claim was checked. See the
[interview walkthrough](INTERVIEW-DEMO.md) and
[validation evidence](validation/SENSOR-LAB-20261019.md).
