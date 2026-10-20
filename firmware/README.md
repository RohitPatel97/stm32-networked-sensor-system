# STM32 / FreeRTOS sensor firmware

Firmware component of the [STM32 Networked Sensor System](../README.md).

MPU6050 and BMP280 drivers feed 100 Hz acquisition and processing. Four FreeRTOS
tasks coordinate the I2C mutex, bounded queues, 10 Hz UART telemetry, and health
decisions. The board and compiled simulator emit the [shared v3 protocol](../protocol/README.md).
Legacy schema-1 formatting and Python fixtures remain for compatibility testing.

From the combined repository root:

```sh
cmake -S firmware -B build/host -DCMAKE_BUILD_TYPE=Release
cmake --build build/host --config Release --parallel
ctest --test-dir build/host -C Release --output-on-failure
python scripts/demo.py --scenario recovery
```

The combined Python environment is installed from `./gateway[dev]`.
For the local terminal monitor, pipe simulator stdout to
`python firmware/tools/dashboard.py --file - --no-ansi`.
The Python reference generator remains a legacy schema-1 tooling demo; the C
simulator is the authoritative driver/processing integration model.

For actual ARM firmware:

```sh
cmake -S firmware -B build/stm32 -G Ninja -DCMAKE_TOOLCHAIN_FILE=cmake/arm-none-eabi.cmake -DHUB_BUILD_STM32=ON -DCMAKE_BUILD_TYPE=MinSizeRel
cmake --build build/stm32 --parallel
```

The target allocates a persistent boot ID before publishing UART records.
Flash sector 7 is reserved for an append-only journal. Normal updates must
preserve it; erasing it requires a new provisioned `HUB_DEVICE_EPOCH`.
Read [boot provisioning](../protocol/README.md#boot-identity-and-provisioning)
and the [target guide](target/stm32f401re/README.md) before flashing.

Current results are recorded in [combined validation](../docs/VALIDATION.md).
Component WORKFLOW and older reports record imported history. Physical board
bring-up, flash power-cut behavior, watchdog resets, timing, and soak remain
unverified. The watchdog defaults to disabled.
