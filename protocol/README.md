# Shared telemetry protocol v3

The board and compiled C simulator emit this contract directly. The gateway's
live serial, replay, and compiled-simulator sources all use the same decoder.
The machine-readable contract is [telemetry-v3.schema.json](telemetry-v3.schema.json),
exported from `gateway/src/sensor_monitor/shared_protocol.py`. Regenerate with
`python scripts/export_protocol.py`; CI uses `--check` to prevent drift. C output
is tested against that validator, including fault cases.

## Framing and fields

UTF-8 JSON, one object per LF, UART 115200 8N1. At most 2048 bytes before LF;
CR in CRLF counts. The firmware formatter has a 1024-byte caller buffer including
NUL and clears it on failure. It never transmits truncated JSON. Receive errors
resynchronize at LF; successful serial reconnect clears framing only.

| Field | Meaning |
| --- | --- |
| `schema_version: 3`, `type: "telemetry"` | Explicit shared protocol |
| `device_id` | Stable 1..64 character identifier, letters/digits/`.`/`_`/`-` |
| `boot_id` | Increasing uint64, allocated before any telemetry |
| `frame_seq` | uint32 transmitted-record sequence, advances even if sample repeats |
| `uptime_ms` | uint64 transmission uptime in milliseconds |
| `sample_present` | Whether acquisition/processing has ever produced a sample this boot |
| `seq`, `ts_us` | uint32 acquisition sequence and uint64 sample timestamp in microseconds |
| `sensors` | Debounced MPU6050 and BMP280 states: `online` or `offline` |
| `imu` | `accel_g[3]`, `gyro_dps[3]`, die `temperature_c`, or `null` |
| `baro` | BMP280 `temperature_c`, `pressure_pa`, `altitude_m`, or `null` |
| `health` | uint32 cumulative `i2c_retries`, `i2c_errors`, `queue_drops`, `deadline_misses`; `watchdog`, `watchdog_enabled`, `reset_flags` |
| `timing` | uint32 `acquisition_us`, `processing_us` |

Sample payload validity is independent of debounced sensor status. A failed read
can produce `null` while its state still says `online`. Both payloads may be null
in a valid acquired sample. Before the first processed sample, `sample_present`
is false, both payloads are null, and `seq` and `ts_us` are zero. A sample time
cannot exceed transmission uptime. All numbers must be finite; integer fields
reject booleans, strings, fractional values, and overflow. Duplicate object keys
and unknown fields are rejected.

`watchdog: "fed"` retains the old field spelling but means the latest **health
eligibility decision**. `watchdog_enabled: false` explicitly indicates no
hardware watchdog was started. Neither field establishes a reset count.
Battery voltage and watchdog reset count remain unknown in the normalized API.

## Ordering and freshness

A greater boot ID starts a new sequence baseline; an older boot is rejected.
Within a boot, frame sequence differences of 1..2147483647 modulo 2^32 are
forward; duplicates, backward values, and the ambiguous half-range are rejected.
The sample sequence has the same rollover rule but may repeat. A repeated sample
must retain its timestamp, payloads, and sample timing; its health/status may
change. Transmission uptime and sample time cannot go backward within a boot.
Once a sample exists, later records cannot revert to `sample_present: false`.
V3 devices cannot downgrade to v1/v2, including with a higher claimed boot ID.

The API's `sequence` is `frame_seq`. The original sample identity and full nested
diagnostics remain under `firmware`. Temperature is BMP280 temperature;
`pressure_hpa = baro.pressure_pa / 100`; absent sensors remain null. Top-level
v3 API `boot_id` is a decimal string to preserve uint64 precision in JavaScript;
health also provides `boot_id_decimal`. Nested wire integers are retained for
source fidelity; JavaScript consumers must use the top-level string for identity.

Device age tracks accepted UART records. Sample age additionally includes the
producer's `(uptime_ms - ts_us / 1000)` and time since gateway reception. A live
UART stream with stalled acquisition is degraded with stale measurements;
silence ages the device to stale/offline. Transport latency before reception is
not measurable from these independent clocks. Health-only startup records show
measurements unavailable.

Sensor availability and task health determine active faults. Counter increments
activate an alert for a one-second producer-time window; unsigned deltas handle
counter wrap. A first nonzero total is historical and is not itself a new event.
Counters and active-alert state are independent: recovering does not zero totals.
Unknown or stale temperature cannot falsely clear an active temperature alert.

Gateway histories, alert state, and session baselines are bounded in memory.
Restarting the gateway loses them. This contract supplies ordering, not sender
authentication or protection against an attacker forging newer boot IDs.

## Boot identity and provisioning

STM32 identity is its 96-bit silicon UID plus `HUB_DEVICE_EPOCH` (default `0`).
The epoch is a provisioned string of 1..24 letters/digits/`_`/`-` set with CMake.
Each startup reserves a greater boot counter before RTOS tasks/UART publication.

Flash sector 7 (`0x08060000..0x0807ffff`) is reserved; executable image space is
384 KiB. Each append-only record occupies five words: counter low/high, their
complements, and a commit marker written last. Each write is checked, and flash
caches are flushed before verification. Complete counter data is reserved even
when commit was interrupted, so gaps are allowed. Partial records consume slots.
Recognized committed corruption, write failure, exhaustion, or uint64 overflow
halts startup without publishing a reused session.

There are 6553 slots; interruptions may consume more than one per successful boot.
The implementation **never erases the journal automatically**. At exhaustion,
or after any deliberate journal/mass erase, provision a new epoch before use.
Normal firmware updates must preserve sector 7. Reusing the same UID/epoch after
erase violates session ordering. Automatic two-sector reclamation/wear levelling
is outside this implementation. Host tests model interrupted writes and torn
commit; electrical power-cut acceptance still requires the board.

The target extends its 32-bit RTOS tick count under a critical section for
monotonic sample/transmission times across tick rollover. This assumes at least
one clock observation per 32-bit tick period while running (normal tasks read it
at 10/100 Hz).

## Compatibility

Gateway v1/v2 flat records remain supported, with their original health rules.
The old FreeRTOS schema-1 file converter is retained only for historical captures;
its complete-sensor limitation does not apply to v3. The legacy C formatter and
Python reference generator remain for compatibility tests. The actual board and
compiled simulator use v3 by default. REST route paths remain `/api/v1/...`;
this names the HTTP API, not the UART schema version.
