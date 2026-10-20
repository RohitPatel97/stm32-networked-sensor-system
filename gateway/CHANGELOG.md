# Changelog

## 1.2.0 - 2026-10-12

- Add opt-in protocol v2 with explicit uint64 boot counters, older-session/downgrade rejection, and preserved v1/uint32 rollover behavior.
- Expose boot identity in readings/health and count accepted session changes; retain alert transitions across boots.
- Clear only decoder framing at serial connection boundaries and coordinate worker I/O cleanup with stop/cancellation.
- Add an explicit, bounded FreeRTOS capture converter with verified unit mappings, incomplete-frame diagnostics, and unknown battery/watchdog values.
- Add a reproducible timed host soak harness with bounded metrics, predeclared limits, and source hashes.
- Expand regression coverage and verify the running simulator's REST/WebSocket/fault-recovery flow.
- Run current GitHub Actions and strengthen container CI to verify acquisition, history and API responses.
- Document session contracts, bridge provenance and physical hardware acceptance separately.

### Included capture hardening from October 1

- Enforce the live 2048-byte frame limit in capture validation, with physical error line numbers and recovery after invalid records.
- Stream replay captures through bounded reads instead of loading the entire file; preserve LF framing and accept an optional final newline.
- Keep replay file open, read, and close operations off the event loop, including cleanup after cancellation.
- Add capture boundary, recovery, bounded-read, cancellation, and downstream alert/WebSocket regression tests.
- Clarify PowerShell setup, one-pass replay, repeated-sequence rejection, validation scope, and remaining integration limits in the README.

## 1.1.0 - 2026-09-04

- Reject duplicate, backward, and ambiguous sequence numbers before changing health, history, alerts, or WebSocket output.
- Accept unsigned 32-bit rollover and preserve per-device sequence baselines.
- Add regression cases for stale-data rejection, wraparound, independent devices, and alert/fan-out isolation.
- Document reproducible problem/solution cases, local verification, and reboot/session limitations.

## 1.0.0 - 2026-08-26

- Added strict, bounded newline-delimited JSON ingestion.
- Added serial, replay, and deterministic simulator sources.
- Added bounded telemetry history, device health, transition-based alerts, REST endpoints, and WebSocket fan-out.
- Added a dependency-free dashboard, rotating logs, systemd and container deployment assets, tests, and CI.
