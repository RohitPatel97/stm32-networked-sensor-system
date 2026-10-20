# FreeRTOS telemetry bridge

The bridge converts the supported complete-sample subset of FreeRTOS Sensor Hub
schema 1 into gateway schema 2. It is a file/API converter; the serial source
still expects gateway-format JSON. Convert a source capture before using gateway
validation or replay. The bridge does not change firmware or establish physical
UART, Raspberry Pi, or FreeRTOS task validation.

## Source and fixture provenance

The source contract and two-record example are pinned to FreeRTOS Sensor Hub
commit [`79dec62d2bb11132fa317f2c7f36baa719bd74fd`](https://github.com/RohitPatel97/freertos-sensor-hub/tree/79dec62d2bb11132fa317f2c7f36baa719bd74fd):

- [Protocol and units](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/docs/PROTOCOL.md)
- [C telemetry formatter](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/src/telemetry.c)
- [Source parser](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/tools/telemetry.py)
- [Upstream example](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/examples/sample_telemetry.ndjson),
  copied unchanged to [`examples/freertos_telemetry.ndjson`](../examples/freertos_telemetry.ndjson).

The bundled records are example/test data from the host simulation project,
not a documented physical UART capture. Their timing values do not establish
measured target performance.

The local copy and the pinned upstream checkout's example have identical SHA-256:
`69d27192706bae1f2afc28a1edc7a3907052624ba6a1148aad96635e1ab6725f`.

## Field mapping

| Source | Gateway | Rule |
| --- | --- | --- |
| Caller-supplied device ID | `device_id` | Stable ID obeying gateway character/length rules |
| Caller-supplied boot ID | `boot_id` | Explicit uint64 boot counter; never inferred from source data |
| `schema: 1`, `type: "telemetry"` | `schema_version: 2` | Only this source schema/type is supported |
| `seq` | `sequence` | Preserve uint32 acquisition sequence, including gaps and rollover |
| `ts_us` | `uptime_ms` | Integer division by 1000; discard submillisecond remainder |
| `baro.temperature_c` | `temperature_c` | Use BMP280 temperature, not IMU die temperature |
| `baro.pressure_pa` | `pressure_hpa` | Divide by 100 |
| `imu.accel_g` | `accel_g` | Preserve all three axes in g |
| `health.i2c_errors` | `i2c_errors` | Preserve uint32 cumulative count |
| `health.queue_drops` | `queue_drops` | Preserve uint32 combined raw/telemetry queue-drop count |
| Not provided | `battery_v: null` | Unknown; no synthetic battery reading |
| Not provided | `watchdog_resets: null` | Unknown; `fed`/`withheld` is a decision, not a reset count |

The converter validates but omits `imu.gyro_dps`, `imu.temperature_c`,
`baro.altitude_m`, `health.i2c_retries`, `health.deadline_misses`,
`health.watchdog`, both `timing` durations, and the debounced sensor states.
The gateway has no equivalent fields for them. Keep the original capture when
those diagnostics matter. Unknown additive source fields are ignored and cannot
override the explicit device/boot IDs or mapped fields.

All required source fields must exist. `schema`, sequence, counters, and timing
values require actual JSON integers; booleans and numeric strings are rejected.
Sequence/counters/durations fit uint32; `ts_us` fits uint64. All sensor numbers,
including omitted measurements, must be finite and representable. The converted
reading must also fit the gateway's temperature, pressure, and acceleration
ranges. Duplicate JSON keys are rejected to avoid ambiguous mappings.

## Missing measurements and health state

The source explicitly permits `imu: null` or `baro: null` while its sensor is
still `online` during fault debounce. The target also snapshots current sensor
status separately from the queued sample. Payload presence determines validity
of that sample; sensor status is checked as `online` or `offline` independently.

The gateway currently requires temperature, pressure, and acceleration in every
reading. A null payload in either sensor therefore raises `BridgeError` with an
`incomplete sensor frame` diagnostic. It is a valid but unsupported source frame,
not evidence of malformed firmware output. The bridge does not substitute zeros,
reuse old measurements, or invent health counts. File conversion omits these
partial records. Replay sends the retained records at its configured fixed
interval and does not reconstruct original timestamp gaps, so converted replay
compresses missing-sensor outages and cannot verify their timing or age-based
stale/offline transitions. Source-specific partial-sensor health is not
represented in the gateway API.

## Boot and replay policy

FreeRTOS schema 1 has no device identity or reliable boot identifier. Provide a
stable device ID and a persisted, monotonically increasing uint64 boot counter
for each known physical boot. For imported files, maintain that assignment in a
capture manifest. Split a capture containing known multiple boots and convert
each segment with its own assigned counter. The converter does not detect those
boundaries or maintain the counter for you.

Keep the same boot ID when reconverting or replaying the same boot's records.
Assigning a larger boot ID to old data falsely presents it as a new session.
Sequence/timestamp decreases alone cannot distinguish reboot, rollover, and
delayed data, so the bridge never guesses. Conversion checks individual records;
gateway ingestion enforces freshness. Gateway history and freshness baselines
remain in memory and are lost when its process restarts. Neither mechanism is
authenticated protection against malicious replay.

## Python API

```python
from sensor_monitor.bridge import BridgeError, convert_freertos_frame
from sensor_monitor.protocol import encode_frame

wire = convert_freertos_frame(raw_bytes, device_id="hub-01", boot_id=17)
gateway_record = encode_frame(wire)
```

`BridgeError` derives from the gateway's `ProtocolError`. One record may contain
an optional final LF or CRLF. Input is bounded to the gateway's 2048-byte record
limit, excluding final LF but including CR; errors in UTF-8, JSON, source schema,
or target schema are reported without producing a reading. File conversion uses
the shared bounded capture reader rather than loading an entire capture.

Focused host checks:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_bridge.py -q
.\.venv\Scripts\python.exe -m ruff check src/sensor_monitor/bridge.py tests/test_bridge.py
```

## Physical evidence audit

On October 12, 2026, the public FreeRTOS repository's `main` and
`fix/telemetry-fault-recovery` branch trees contained example data and host test
code, with no physical capture bundle found. Its releases list was empty and
Actions reported zero currently available artifacts. This audit does not rule
out unpublished or expired evidence.

The pinned [validation record](https://github.com/RohitPatel97/freertos-sensor-hub/blob/79dec62d2bb11132fa317f2c7f36baa719bd74fd/docs/VALIDATION.md)
marks physical bring-up unverified. Its successful
[October 1 CI run](https://github.com/RohitPatel97/freertos-sensor-hub/actions/runs/36889965971)
establishes host build/test execution; the workflow does not compile the STM32
adapter against Cube-generated sources. Physical claims require separately
traceable board/build information, UART captures, disconnect/reconnect results,
and measured soak results.
