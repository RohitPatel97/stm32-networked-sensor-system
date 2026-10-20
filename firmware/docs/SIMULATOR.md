# Deterministic host simulation

Run these commands from the repository root after the host build.


All host faults are deterministic and reproducible:

```bash
# A transient NACK every seventh physical bus attempt; retries should recover.
./build/host/sensor_hub_sim --duration-ms 3000 --i2c-nack-every 7

# Permanently remove the barometer at 500 ms.
./build/host/sensor_hub_sim --duration-ms 2000 --disconnect bmp --disconnect-at-ms 500

# Restore the barometer at 1000 ms and verify valid data returns.
./build/host/sensor_hub_sim --duration-ms 2000 --disconnect bmp --disconnect-at-ms 500 --reconnect-at-ms 1000

# Force raw-queue pressure by consuming at 33 Hz instead of 100 Hz.
./build/host/sensor_hub_sim --duration-ms 1000 --queue-capacity 2 --processor-period-ms 30

# Stop the processing task; the 1 Hz health check withholds watchdog refresh.
./build/host/sensor_hub_sim --duration-ms 3000 --stall-task processing --stall-at-ms 1200
```

| Scenario | Injected behavior | Expected observable |
|---|---|---|
| Nominal | none | 100 acquired/processed samples per second, 10 frames/s, zero errors/drops |
| Transient bus fault | every Nth physical I2C attempt NACKs | `i2c_retries` rises; terminal `i2c_errors` may remain zero |
| Sensor unplug | MPU6050, BMP280, or both stop acknowledging | three failed cycles, sensor becomes `offline`, telemetry continues |
| Slow consumer | processing period exceeds 10 ms | bounded raw queue fills and `queue_drops` rises |
| Acquisition stall | semaphore is no longer consumed | `tick_overruns` rises and health later fails |
| Task stall | selected task stops heartbeating | `deadline_misses` and `watchdog_withheld` rise |

The C simulator writes telemetry to stdout and one JSON summary to stderr.
The summary includes `sensor_disconnects` and `sensor_reconnects`; the existing
version-1 UART format is unchanged. `--reconnect-at-ms` restores simulated
register responses and exercises the portable driver's reinitialization path.
It does not model electrical startup or conversion-ready timing. Omit it for
the original permanent-outage behavior. This option belongs to the C simulator;
the Python reference generator is a separate tooling model.

Numeric options accept decimal digits only, up to `4294967295`. Duration is
further limited to **1..3600000 ms** (one simulated hour), queue capacity to 1..16,
and processor period must be positive. Reconnect requires a selected disconnect
target and a later timestamp. Invalid input exits 2 before simulation. These
checks prevent truncation and a `UINT32_MAX` duration loop wrapping indefinitely.

## Problem / solution test cases

| Problem to reproduce | Implemented solution | Automated acceptance evidence |
|---|---|---|
| Scheduling must keep up with acquisition | Bounded queues and separate processing/reporting periods | 2 s produces 200 processed samples, 20 frames, zero drops, and two watchdog feeds |
| Removing a sensor must not stop the other sensor | Independent valid bits and retrying drivers | BMP outage yields `baro: null`, MPU stays online, and terminal I2C errors rise |
| A restored sensor must resume valid data | Drivers reinitialize; shared status logic counts transitions | MPU, BMP, and both-device outages at 500 ms recover at 1000 ms with one disconnect/reconnect per device |
| Sensors may be absent at boot | Read path retries initialization | Both sensors absent at 0 ms regain payloads after 500 ms; two reconnects, no previous online-to-offline transition |
| Brief faults must not cause status flapping | Three consecutive failed acquisitions required for offline status | A one-cycle fault increases I2C errors without a disconnect/reconnect |
| Long outages must not overflow error state | Saturating failure count in shared host/target status logic | 303 failures leave count at 255 and one disconnect; recovery clears the count |
| Slow consumers must not create unlimited backlog | Fixed queue capacity and observable drops | Capacity 2 and a 30 ms processing period produce drops |
| Task failure must stop watchdog service | Per-task heartbeat deadlines | Processing stall withholds service; acquisition stall also increases tick overruns |
| A task waiting for input must not look productive | Processing heartbeat follows a successful dequeue, matching the target adapter | Acquisition stall at 1200 ms produces four deadline misses across the 2 s and 3 s health checks; boot stall leaves both tasks unhealthy |
| A valid missing-sensor frame must not stop monitoring | Render null payloads as `n/a`, independently of debounced sensor state | Two compiled-simulator recovery replays render every snapshot through MPU-only and simultaneous outages |
| Corrupted captures must not terminate a session | Validate each line and skip malformed frames with a diagnostic | Invalid UTF-8, oversized numbers, invalid schema types, and decoder-limit failures are rejected; following valid frames survive |
| A millisecond clock can wrap | Unsigned elapsed-time comparison | Across `UINT32_MAX`, 30 ms passes and 31 ms misses the acquisition deadline |
| Invalid sensor data must not cause arithmetic errors | Validate raw ADC values and guard compensation arithmetic | Negative/sentinel/out-of-range raw data, zero pressure calibration, and overflowing corrupt trim are rejected without modifying output |
| Invalid CLI inputs must fail promptly | Strict decimal/range checks and bounded duration | 69 malformed or out-of-range inputs exit 2 without telemetry |

Tests are in [`tests/c/test_core.c`](../tests/c/test_core.c),
[`tests/test_sim_cli.py`](../tests/test_sim_cli.py), and the Python `tests/test_*.py`
modules: **6 C test groups, 12 compiled simulator scenarios, 2 dashboard recovery
replays, 69 CLI rejection cases, and 22 Python tests**. With Python available,
CTest runs all three suites. Run the quick-start commands above. See
[`docs/VALIDATION.md`](VALIDATION.md) for execution evidence and
[`WORKFLOW.md`](../WORKFLOW.md) for follow-up work.
