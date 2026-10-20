# Full Renode system test October 18 2026

The STM32F401RE ARM firmware passed 12 acceptance groups and produced 1,665
validated UART frames. The 167-second virtual scenario includes a 120-second
steady phase. The gateway recorded zero parser errors, sequence anomalies,
device rejections or WebSocket drops. The prepared interview launcher also
passed preflight, live browser controls, stop/restart and duplicate launch checks.

## Sensor and diagnostic results

| Check | Observed result |
| --- | --- |
| Baseline acceleration | `[0, 0, 1]` g |
| All acceleration axes changed | `[0.5, -0.25, 0.75]` g |
| Baseline gyroscope | `[0, 0, 0]` deg/s |
| All gyroscope axes changed | `[5, -20, 10]` deg/s |
| Baseline barometer | 25.08 C, 1006.53 hPa, altitude 56.1 m |
| Temperature registers changed | 28.25 C |
| Pressure registers changed | 946.43 hPa, altitude 571.78 m; independently checked against the pressure formula |
| Boot IDs | `9007199254741093` through `9007199254741097`, preserved as decimal strings beyond JavaScript's exact integer range |
| Timing and health | Acquisition/processing fields present; baseline I2C, retries, queue drops and deadline misses zero; watchdog enabled and fed |
| Sensor removal | MPU6050, BMP280, and both removed from I2C; affected values become null/`n/a`; remaining sensor continues |
| Sensor recovery | Both return online with current measurements and recovery alerts, without changing boot identity |
| Three MCU resets | Stable UID, advancing flash journal, frame sequence restarts at zero |
| UART reconnect | Five disconnects recover without decoder/order errors |
| Watchdog recovery | Disabled TIM2 causes IWDG reset; next boot resumes and feeds the watchdog |
| WebSocket reconnect | New client receives snapshot then fresh telemetry |
| Dashboard rendering | Production JavaScript checked against real Renode events, including all six IMU axes, altitude, boot/frame identity, faults and recovery |

## Interview demo verification

The visible browser exercised all nine controls: stationary/restore, motion,
pressure/altitude, MPU disconnect, BMP disconnect, both disconnected, MCU reboot,
UART reconnect and watchdog reset. During disconnected states, unavailable fields
are intentional; they recover when the sensors are restored. The original demo
processes were not running at the start of this work. The new launcher waits for
live, complete firmware telemetry before opening the browser.

The reboot and watchdog controls now wait for resumed watchdog health before
reporting success. Reloaded dashboard alerts were appearing oldest first; the
snapshot rendering now preserves the API's newest-first ordering. A live reload
confirmed recovery messages above the earlier fault messages, with no browser
console warnings or errors in the final page.

The actual `Launch Interview Demo.cmd` passed a separate `--check-only` startup
on port 8766 and released its gateway and emulator on exit. The live demo was
stopped and relaunched from that command file on port 8765. Launching it a second
time reused the running instance. The final reboot/watchdog/restore checks each
returned an online board with watchdog health fed.

The demo uses loopback sockets, fixed allowed actions, a per-run control token,
and host validation. Tests reject missing tokens, unknown actions, extra command
fields, preflight-time control requests, untrusted hosts and simultaneous actions.
The normal gateway exposes no Renode control endpoints.

## Build and regression checks

- Watchdog-enabled ARM build was current and completed successfully.
- Four CTest suites, schema/fixture checks and gateway lint passed.
- 265 Python tests passed. A pre-existing Starlette/httpx deprecation warning remains.
- All 116 original source hashes remain unchanged.
- ELF SHA-256: `46598310fdb612481b9822c262aadf6730b7827a6f275a23415122bf34458372`.

## Evidence and reproduction

- [Renode report](renode-full-20261018/report.json)
- [Raw ARM UART frames](renode-full-20261018/uart.jsonl)
- [Renode monitor log](renode-full-20261018/monitor.txt)
- [Host checks](renode-full-20261018-host.json)
- [Launcher preflight](renode-full-20261018/preflight.json)
- [Final reboot and watchdog control responses](renode-full-20261018/demo-controls.json)
- [Live snapshot](renode-full-20261018/live-snapshot.json)
- [Browser diagnostics](renode-full-20261018/dashboard-state.txt)
- [Dashboard screenshot](renode-full-20261018/dashboard.png)
- [Launch and interview walkthrough](../INTERVIEW-DEMO.md)

```powershell
.venv/Scripts/python.exe scripts/test_renode.py --output build/renode-next-test --soak-seconds 120
& '.\Launch Interview Demo.cmd' --check-only --no-browser
```

This establishes the implemented firmware, modeled sensors and software flow in
Renode. Physical sensor accuracy, electrical bus behavior and real MCU timing
still require hardware. The RCC model's reset flags do not reliably distinguish
manual and watchdog reset; the IWDG event and boot transition establish the
watchdog test result. This run is not an exhaustive memory-leak qualification.
