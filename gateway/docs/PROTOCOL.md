# Legacy protocol v1/v2 and session policy

The combined project also accepts [shared v3](../../protocol/README.md) directly,
including partial sensor records and full firmware diagnostics. The mandatory
flat sensor fields described below apply only to the legacy versions.

The gateway accepts UTF-8 JSON records delimited by LF, with at most 2048 bytes before LF. CR in CRLF counts toward this limit. Protocol v1 remains supported. Protocol v2 adds an explicit `boot_id` and permits `watchdog_resets: null` when a bridge has no reset-count measurement. Sensor values remain mandatory and range checked.

## Boot identity

`boot_id` is an unsigned 64-bit integer that must increase for each boot of one stable `device_id`. The producer must persist the counter and increment it before emitting a new boot's first record. Never infer a boot from a falling uptime, UART reconnect, or sequence reset. Never use a random UUID or a counter that resets to zero at every reboot. For a replaced device or an exhausted counter, provision a new device ID.

The gateway does not write or generate the producer's counter. For a converted FreeRTOS capture, the operator supplies the known boot identity and splits recordings at known boot boundaries. Replaying the same capture must retain the same boot ID.

## Acceptance rules

The first valid sample for a device establishes its baseline. Thereafter:

| Accepted baseline | Incoming sample | Result |
| --- | --- | --- |
| v1 | v1 | Compare sequence using uint32 serial arithmetic |
| v1 | v2 | Establish the first explicit boot baseline, regardless of sequence |
| v2 | v1 | Reject downgrade |
| v2 boot N | v2 boot below N | Reject delayed older session |
| v2 boot N | v2 boot N | Compare sequence using uint32 serial arithmetic |
| v2 boot N | v2 boot above N | Accept a new boot and sequence baseline |

Within a boot, `(new_sequence - previous_sequence) mod 2^32` must be between 1 and 2147483647. This accepts rollover from 4294967295 to 0, but rejects duplicates, backward records and ambiguous half-range jumps. The policy does not infer lost sample counts from gaps.

Rejected records increment `sequence_anomalies` and cannot refresh device age, replace history, change alerts, or reach subscribers. `session_changes` counts accepted changes from an existing device baseline to a newer boot, including v1-to-v2 migration; initial discovery is not a change. History remains bounded across boots. REST/WebSocket readings and device health expose `boot_id` (`null` for v1).

Alert state is retained across boots. A continuing hot condition does not produce a duplicate active alert; a later accepted clean sample produces recovery. A delayed clean frame from an older boot cannot falsely clear an active fault.

## Transport lifecycle

Each successful serial open signals a new byte stream and clears only the decoder's partial buffer and discard mode. Idle read timeouts keep partial frames. Reconnects preserve sequence/session baselines, history and alerts. An incomplete frame from a previous connection cannot consume the first complete new frame.

Serial open/read calls run on workers. Shutdown waits for pending I/O before closing the port off the event loop, and interrupts retry backoff. The configured serial read timeout is 0.5 seconds; an OS open/driver operation that stalls cannot be forcibly terminated by cancelling its Python awaiter.

## Persistence and trust limits

Gateway baselines, history and alerts are in memory. Restarting the gateway establishes new baselines from the first samples it receives, including an old capture. Increasing boot IDs are a producer/operator contract, not authentication or cryptographic replay protection. A forged higher boot ID is not distinguishable from a legitimate newer boot. Deploy only on a trusted input path or add authenticated transport before relying on sender identity.

Legacy v1 devices still require a gateway restart or new device ID after a firmware sequence reset that fails freshness checks. Serial reconnect alone intentionally does not reset freshness.

Tests: `tests/test_sessions.py`, `tests/test_serial_reconnect.py`, and `tests/test_store.py` cover the acceptance and lifecycle cases using host-generated data and mocked serial I/O.
