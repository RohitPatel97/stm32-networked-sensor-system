# Legacy telemetry protocol v1

This is the imported compatibility contract. The combined board and C simulator
now emit [shared v3](../../protocol/README.md), with a 1024-byte formatter buffer.
The legacy formatter and old Python reference fixtures still use the format below.

Transport is UTF-8 newline-delimited JSON (NDJSON), one object per line, at
115200 baud, 8 data bits, no parity, one stop bit. Firmware emits telemetry at
10 Hz. Consumers should impose a reasonable line limit (the firmware buffer is
768 bytes), ignore unknown fields, and select decoding behavior from `schema`.

## Required top-level fields

| Field | Type | Meaning |
|---|---|---|
| `schema` | integer | Protocol version; currently `1` |
| `type` | string | `telemetry` |
| `seq` | uint32 integer | Acquisition sequence of the reported latest sample; wraps at 32 bits |
| `ts_us` | uint64 integer | Acquisition timestamp in microseconds since boot |
| `sensors` | object | `mpu6050` and `bmp280`, each `online` or `offline` |
| `imu` | object or null | Scaled acceleration, angular rate, and die temperature |
| `baro` | object or null | Compensated temperature/pressure and derived altitude |
| `health` | object | Cumulative fault/backpressure counters and watchdog decision |
| `timing` | object | Acquisition and processing duration in microseconds |

The target sends `null` when an acquisition contains no valid data for a sensor.
Sensor state changes only after the configured consecutive-failure threshold,
so a frame can contain `null` while state is still `online` during the debounce
window. Consumers must use payload presence for per-sample validity and the
state field for the debounced health status.

## Counter semantics

- `i2c_retries`: additional physical attempts after an initial failed attempt.
- `i2c_errors`: logical I2C operations that failed after all attempts, plus mutex
  acquisition timeouts.
- `queue_drops`: sum of raw and telemetry queue drops. The simulator summary
  exposes more detail.
- `deadline_misses`: task deadline failures observed by the 1 Hz health task.
- `watchdog`: the most recent health decision, `fed` or `withheld`.

All counters are unsigned and monotonic modulo 32 bits. A consumer should treat a
lower value after reconnect as reboot/wrap, not a negative delta.

## Parser behavior

`tools/telemetry.py` requires an integer `schema` equal to `1`; JSON `true`,
`1.0`, and `"1"` are rejected. Sequence, health counters, and timing durations
must be integers in `0..4294967295`; timestamps must be integers in
`0..18446744073709551615`. Booleans are not numeric values. Sensor numbers must
be finite and representable by Python's floating-point tooling.

Every required field must be present. In particular, omitted `imu` or `baro`
is malformed, whereas an explicit `null` is a valid absent measurement. The
dashboard displays that measurement as `n/a` regardless of debounced status.

Malformed JSON/UTF-8, invalid states, numeric overflow, and JSON decoder
digit/nesting-limit errors are exposed as `TelemetryError`. Stream consumers
can skip these errors and continue with the next line. File and stdin replay
read bytes so a bad UTF-8 line does not prevent later frames from being decoded.
The parser preserves unknown additive fields in the raw mapping. This tightens
validation of protocol v1 without changing the firmware's emitted wire format.

## Firmware formatter

The C formatter writes directly into a bounded caller buffer. Active sensor
payloads containing NaN or infinity are rejected, as is insufficient capacity.
It returns the encoded byte count on success (excluding the terminating NUL),
or `-1` on failure. Every failure clears a writable, nonempty output buffer;
callers must still check the return value before transmitting. Fields belonging
to an invalid sensor are ignored and encode as `null`.

The build defaults to IWDG disabled for bring-up. In that configuration,
`health.watchdog` is the health eligibility decision and does not establish an
actual hardware refresh. Enable IWDG explicitly after validating the board.
