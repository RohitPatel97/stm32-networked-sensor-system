# Run the STM32 firmware in Renode

The combined project can execute its Cortex-M4F ELF with FreeRTOS, STM32 HAL,
MPU6050 and BMP280 drivers, and the production telemetry formatter. USART2 feeds
the existing gateway decoder, REST endpoints, WebSocket stream, and dashboard.
The two original repositories are unchanged.

## Launch

For the prepared Windows demo, double-click **Launch Sensor Lab.cmd** in the
project root. It preflights firmware telemetry and opens a test console with
22 component checks, fault controls and saved evidence. See the
[Sensor Lab guide](SENSOR-LAB.md) and [interview walkthrough](INTERVIEW-DEMO.md).

The current Windows workspace has Renode 1.17.0 portable installed under
`build/tools/renode-1.17.0/renode_1.17.0-portable/`. The source-only
`renode-master.zip` in Downloads is preserved; it is not the executable package.
For a fresh installation, obtain the Windows portable archive from the
[official Renode 1.17.0 release](https://github.com/renode/renode/releases/tag/v1.17.0)
and extract it, or pass another installed executable with `--renode`.

Install the Python dependencies and compile the watchdog-enabled ARM target:

```powershell
python -m pip install -r requirements-lock.txt
python -m pip install -e "./gateway[dev]"
# Arm GNU Toolchain, CMake and Ninja must be on PATH.
cmake -S firmware -B build/stm32-on -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DHUB_BUILD_STM32=ON -DHUB_ENABLE_WATCHDOG=ON -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/stm32-on
python scripts/renode_demo.py --port 8765
```

Open [the dashboard](http://127.0.0.1:8765/). Expected readings are 25.08 C,
1006.53 hPa, acceleration `[0, 0, 1]` g and zero angular velocity. The source
label is `renode`; both sensors become online and the watchdog becomes eligible.
Ctrl+C shuts down the gateway and its owned emulator process.

`--elf path/to/firmware.elf` chooses a different build. The launcher uses a private
stdin control pipe and a receive-only UART socket bound to `127.0.0.1`; it opens
no network-accessible Renode monitor. Logs go to a dated `var/renode/` folder.
Monitor logs rotate at 2 MB with three backups. Live mode does not retain an
unbounded raw UART capture. A disconnected UART reader gets subsequent complete
frames; there is no accumulated offline backlog.

To use an already-running **visible Renode GUI** whose `HubTerminal` exposes
USART2 on loopback port 12345, connect the gateway with:

```powershell
python scripts/renode_demo.py --uart-port 12345 --port 8765
```

This mode connects to that existing board and leaves the GUI process under your
control. In the Renode Monitor, `peripherals` shows the CPU, I2C controller and
sensor models; `showAnalyzer sysbus.usart2` opens its live UART output. The
dashboard and analyzer receive the same firmware output. Renode represents the
hardware through peripherals and registers, rather than a physical board drawing.

Each launcher execution provisions a new virtual board UID and erased boot
journal. MCU resets **within that execution** retain flash and advance the
firmware's journal. Restarting the entire emulator creates a different board;
cross-process flash persistence is not implemented. The `.resc` generated in
the run folder records the exact platform, ELF, UID, and initial journal used.

## Repeat the acceptance tests

```powershell
python scripts/test_renode.py --output build/renode-my-test --soak-seconds 60
python scripts/verify.py
python scripts/check_originals.py
```

Use a new output directory each time. The Renode suite uses the watchdog-enabled
ELF and runs actual instructions through the following cases:

- Default calibration and a boot ID above JavaScript's exact integer range.
- Changes to acceleration, angular velocity and temperature registers.
- IMU removal, barometer removal, simultaneous removal, and reattachment.
- Three MCU resets with stable UID and strictly increasing flash boot IDs.
- Five real UART TCP disconnects/reconnects.
- Acquisition timer stall, hardware IWDG reset, and firmware recovery.
- Continued telemetry and a new WebSocket client's snapshot/update sequence.

It checks REST and WebSocket payloads, null sensor fields, current health,
ordering, parse errors, and raw firmware JSON. The report, monitor log, startup
script and raw UART capture are saved in the specified directory. Faults remove
devices from Renode's I2C bus, exercising the HAL's error/timeout paths.

## Platform and model scope

| Component | Configuration |
| --- | --- |
| CPU/memory | Cortex-M4F, 512 KiB flash, 96 KiB SRAM, BOOT0=0 flash alias |
| Clock/interrupts | Fixed 84 MHz model, SysTick, NVIC, TIM2 IRQ 28 |
| I2C1 | STM32 register model at `0x40005400`, MPU6050 `0x68`, BMP280 `0x76` |
| USART2 | STM32 UART at `0x40004400`; firmware configures 115200 baud |
| Identity | Three UID words; 128 KiB journal at `0x08060000` |
| Watchdog | STM32 independent watchdog model with nominal 32 kHz LSI |

`emulation/renode/stm32f401re.repl` describes the used peripherals.
`HubSensors.cs` provides identities, configuration registers, signed IMU data
and Bosch calibration/raw readings. `HubTerminal.cs` provides the bounded,
loopback-only UART bridge. `HubGPIOPort.cs` extends the stock GPIO model with
output-type register storage so firmware open-drain configuration can be checked;
it does not model electrical levels. Include all three C# files before loading
the platform in a manually configured Renode session. The launcher connects the UART using Renode's
[terminal interface](https://renode.readthedocs.io/en/latest/host-integration/uart.html).

These are digital register models. They do not establish physical sensor
accuracy, I2C signal integrity, baud-rate timing, flash wear/programming physics,
power-cut behavior, or measured MCU timing. The upstream STM32F4 RCC model uses
default reset-cause bits; **reset_flags do not distinguish watchdog from manual
reset in this emulation**. The watchdog test establishes the reset through the
watchdog event and advancing journal. Validate reset flags on the real board.
Known model warnings about RCC/cache/I2C timing bits remain in the log; unmapped
accesses and invalid firmware JSON are not treated as acceptable results.

See [the observed validation results](validation/RENODE-20261017.md).
