# Project workflow

Repository: https://github.com/RohitPatel97/networked-embedded-sensor-monitor

## Completed in September 2026

- [x] Bound protocol decoding, device history, and subscriber queues.
- [x] Reject stale/duplicate data before health, alert, and subscriber updates.
- [x] Support uint32 sequence rollover with a regression test.
- [x] Document problem/solution test cases and hardware limitations.
- [x] Run 50 tests on Windows Python 3.12.14; 93.09% coverage; Ruff passed.

## October 1, 2026 - bounded capture validation and replay

### Problem and expected behavior

The CLI accepted schema-valid records larger than the live decoder's 2048-byte limit, so a successful offline check did not establish that a capture would pass framing during ingestion. Replay loaded the entire capture and split at lone carriage returns as well as LF. Expected behavior: bounded reads, the same LF framing and byte limit as live input, one rejection per oversized record, correct physical error line numbers, and recovery for following valid records.

### Solution and regression evidence

- Share `capture.iter_capture_records` between validation and replay. Read at most 2050 bytes at a time, drain oversized physical lines, and retain only a 2049-byte prefix for rejection. File inputs may omit their final LF.
- Run CLI records through `NewlineJsonDecoder`; preserve its CRLF size accounting and whitespace rejection.
- Keep replay file operations on worker threads and finish an in-flight read before closing after cancellation.
- Cover exact frame limits, CRLF, EOF, oversized blank records, malformed UTF-8, physical line numbers, bounded reads, lone CR, replay loops, empty captures, and cancellation cleanup.
- Confirm through the hub that an oversized hot record cannot enter history, raise an alert, or reach subscribers, while the next valid record is accepted.
- Before implementation, the focused CLI/source suite produced **8 failed, 12 passed**. Failures reproduced oversized acceptance, missing oversized-blank rejection, unbounded replay loading, and lone-CR splitting.

### Observed result and publication

Local verification on Windows, Python 3.12.14:

```text
python -m pytest --cov=sensor_monitor --cov-report=term-missing
69 passed, 1 warning; combined line/branch coverage: 94.16%

python -m ruff check .
All checks passed!

sensor-monitor validate examples/telemetry.jsonl
accepted=3 rejected=0

git diff --check
No whitespace errors.
```

The commands used the repository's `.venv/Scripts` executables. The warning is an upstream Starlette/httpx test-client deprecation. The cancellation regression was added after its cleanup fix and passed; no failing-before result is claimed for that case.

Published implementation: [`105c46e19ca0183e5786c791e2ac4383a8fa69d1`](https://github.com/RohitPatel97/networked-embedded-sensor-monitor/commit/105c46e19ca0183e5786c791e2ac4383a8fa69d1), pushed to `main`.

Observed [GitHub Actions run 36883808043](https://github.com/RohitPatel97/networked-embedded-sensor-monitor/actions/runs/36883808043): **success** for Python 3.11, 3.12, and 3.13 (lint, tests, sample validation) and the production container build. CI emitted upstream action-runtime deprecation annotations; all jobs passed. This publication record is a subsequent documentation-only commit; the linked implementation commit identifies the tested source.

### Remaining limits

- These are host tests with generated capture files and mocked file I/O. They do not establish physical UART/Pi behavior or a hardware soak result.
- History and sequence baselines remain in memory. Looping replay keeps original sequences and does not reset freshness checks; use one-pass replay or the simulator as described in the README.
- Boot/session semantics and the separate FreeRTOS schema bridge are still pending.
- Serial reconnects still preserve partial decoder state; transport-boundary framing reset needs a separate change and regression test.
- Bounded capture buffering does not establish a measured process RSS or latency ceiling. Cancellation waits for the active file operation to finish; it cannot abort a stalled OS read.

## October 12, 2026 - software completion milestone

### Problem and solution

The remaining software work was explicit reboot/session identity, incompatible FreeRTOS telemetry, transport framing surviving reconnects, and reproducible sustained-load evidence. Version 1.2 adds opt-in protocol v2 with a producer-maintained increasing uint64 boot counter; rejects older sessions and legacy downgrade; preserves uint32 rollover, bounded history and alert continuity; and resets only decoder framing when serial connections change.

`convert-freertos` maps the pinned upstream complete-sample schema to gateway v2 with explicit device/boot IDs and units. It rejects unavailable sensor payloads rather than fabricating values, retains unknown battery/watchdog counts as null, and reports physical line diagnostics. Upstream raw fixtures and their SHA256 provenance are documented in `docs/BRIDGE.md`.

### Regression cases and observed results

- Session tests first produced **8 failed, 7 passed** against the old schema. The completed tests cover boot transition, older boot, downgrade, duplicate, wraparound, independent devices, gateway restart and alert/fan-out isolation.
- Eight mock serial cases cover partial/oversized reconnect, idle timeout fragmentation, failed-open retry, stop during read/backoff, and cancellation during open/read without resource leaks.
- Bridge and CLI cases cover integer/numeric bounds, incomplete/null sensors, unit mappings, duplicate JSON keys, malformed/oversized input, partial conversion, invalid CLI flags and input-file overwrite protection.
- Review found and fixed simulator interval overflow and a smoke-check gap that accepted an empty telemetry history.
- Final local suite: **198 passed, 97.52% combined line/branch coverage** on Windows Python 3.12.14. One upstream Starlette/httpx test-client deprecation warning remains. Ruff passed; `git diff --check` passed.
- V1 fixture: `accepted=3 rejected=0`. FreeRTOS fixture: `converted=2 rejected=0`, then `accepted=2 rejected=0` in gateway validation.
- Running loopback simulator: readiness, nonempty device history, WebSocket initial snapshot, active critical-temperature alert and recovery all passed. Observed output: `ok source=simulator accepted=384 devices=1 last_sequence=383`. The [saved API check](docs/validation/host-api-20261012.json) records the later snapshot with 391 accepted frames and no parse errors.
- `docs/SOAK_TEST.md` defined the 600-second acceptance profile before its full run; the harness recorded source hashes so the working-tree code can be traced to the published implementation. The observed result is recorded below.

### Validation boundaries and resume synchronization

The public GitHub evidence audit found no physical capture bundle in this repository or the two FreeRTOS branches inspected, no releases, and no currently available Actions artifacts. The pinned upstream physical checklist remains unverified; this does not rule out private or expired evidence. See `docs/HARDWARE_VALIDATION.md`.

The master resume and both role-specific PDFs were updated locally with project-based features, **198 host tests / 97.5% line/branch coverage**, and mock-tested UART recovery. They retain unrelated experience and links; each remains one page after visual QA. No physical hardware or unobserved soak claim was added. Resume files are outside this public code repository.

Current limits: ephemeral storage and gateway baselines; unauthenticated producer boot IDs; operator-provided identity for converted captures; complete-sensor bridge subset; simulated/mocked transport and host-only timing/memory evidence. Physical UART/Pi/systemd acceptance remains open. Production persistence and authentication are outside the portfolio gateway scope.

### Publication

Published implementation: [`a9b692183be6b5f84aff19870b77b5141f917ebb`](https://github.com/RohitPatel97/networked-embedded-sensor-monitor/commit/a9b692183be6b5f84aff19870b77b5141f917ebb), pushed to `main`.

Observed [GitHub Actions run 38286683654](https://github.com/RohitPatel97/networked-embedded-sensor-monitor/actions/runs/38286683654): **success** for Python 3.11, 3.12, and 3.13 (lint, tests, legacy fixture validation, FreeRTOS conversion and converted-fixture validation). The production container built, started, and passed the stricter readiness/accepted-frame/device-history smoke check. A subsequent documentation/evidence commit records these results and clarifies that replay compresses source timing gaps and bounded history is not a total process-memory ceiling.

### Observed full host soak

The [saved JSON report](docs/validation/host-soak-20261012.json) records **all 17 acceptance checks passed**, exit status 0, from the run starting at `2026-10-12T17:42:37.401304+00:00` on Windows/Python 3.12.14:

| Measurement | Observed result |
| --- | --- |
| Duration and achieved rate | 600.006 seconds; 99.999 frames/s |
| Generated / received / accepted / fast-consumed | 60,000 each; no parse, sequence, capacity or fast-order errors |
| Fast subscriber latency | p95 upper bound 1 ms; maximum 4.872 ms; all 60,000 samples measured |
| Scripted transport resets / boot changes | 59 / 20 |
| Stalled-subscriber drops | Exactly 59,992; latest eight frame indices retained |
| Device history | Exactly 120 readings for each of four devices |
| Traced Python allocation growth | 333 bytes above warmup baseline, maximum of 1,129 post-warmup samples; 5 MiB limit |

Latency measures encoding completion through the in-process fast subscriber; memory measures sampled current `tracemalloc` allocations, not RSS. The run includes no physical serial driver, Pi, HTTP or network transport. The separate API evidence covers loopback HTTP/WebSocket behavior.

The report truthfully records launch HEAD `65bd5a1` with a dirty worktree because it began before the implementation commit. After completion, all ten launch-time SHA256 values matched the unchanged local files; their content also matched published implementation `a9b6921` after Git CRLF/LF normalization. The raw report retains its original launch metadata. No thresholds were relaxed after this run.

## Reproduce validation

```sh
python -m pip install -e ".[dev]"
python -m ruff check .
python -m pytest --cov=sensor_monitor --cov-report=term-missing
sensor-monitor validate examples/telemetry.jsonl
sensor-monitor convert-freertos examples/freertos_telemetry.ndjson --device-id hub-01 --boot-id 100 --output converted.jsonl
sensor-monitor validate converted.jsonl
python scripts/soak_test.py --output docs/validation/host-soak-20261012.json
```

## Remaining physical acceptance and optional extensions

1. Run the physical acceptance checklist with an STM32 and Raspberry Pi. Acceptance: board/image/firmware identities, raw UART captures, reconnect/reboot/service tests and retained commands/logs.
2. Measure a target soak using predefined load, duration and thresholds. Acceptance: Pi RSS, CPU, network latency and loss/reconnect measurements, distinguished from the completed host software checks.
3. If required for a production deployment, add authenticated ingress and persistent storage/session baselines. These are optional scope extensions, not claims of this release.

## Working agreement

For each change, capture the failing stimulus, expected behavior, implementation, regression test, command output, remaining limits, and commit. Run the relevant tests before committing; check the GitHub Actions run after pushing. Keep resume claims tied to source and observed evidence. This file is the durable record for the dedicated Codex project task.
