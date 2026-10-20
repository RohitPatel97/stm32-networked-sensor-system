# Host soak acceptance and reproduction

This is a host software test through the actual `TelemetryHub`, decoder, store, alert
engine, and subscriber queues. It starts no HTTP server and uses no physical device,
serial driver, Raspberry Pi, or network connection. Scripted connection boundaries
exercise decoder recovery; they are not evidence of physical UART reconnect behavior.

## Acceptance defined before the full run

The default profile runs for **600 seconds at 100 total frames/second**, round-robin
across **four devices**, targeting **60,000 complete frames**. Each device retains
120 history records, and each of two subscriber queues has capacity eight.

- Every generated complete frame must be received and accepted, with no parse,
  sequence, or device-capacity rejection.
- The fast subscriber must receive every frame in order, with every latency measured
  and **p95 latency no greater than 50 ms**.
- The second subscriber deliberately consumes nothing until the end. Exactly
  **59,992 oldest pending updates** must be dropped, and its final queue must hold
  the last eight frame indices. Any additional drop fails accounting.
- Every 1,000 frames, before the next complete frame, the source emits a partial
  JSON prefix and `SourceReset`. The run must observe **59 scripted reconnects**.
  These events preserve device and sequence state.
- Each device increments its schema-v2 `boot_id` after 2,500 of its own samples and
  restarts its sequence at zero. The run must observe **20 boot transitions**.
- After a **30-second warmup**, maximum sampled current traced Python allocation
  growth above the warmup baseline must stay at or below **5 MiB**.
- Elapsed time must reach 600 seconds and stay within a predefined 5% scheduling
  overrun allowance (630 seconds). The report includes achieved total frame rate.
- Each device's retained history must equal its configured bound at completion.

## Measurement limits

Latency runs from completion of JSON encoding to fast-subscriber consumption; it
includes decoding, store/alert processing, event serialization, and queue scheduling.
It excludes sensor acquisition, wire transmission, HTTP, and browser rendering.
One fixed histogram records 1 ms buckets through 1,000 ms plus an overflow count;
the reported p95 is a conservative bucket upper bound. A percentile beyond that
range fails the check. A fixed timestamp ring detects missing/overwritten latency
samples, which also fail acceptance. No sample list or pending dictionary grows
with run duration.

Memory uses Python's `tracemalloc`, starting before hub/source allocation. After
warmup, current traced bytes are sampled every 0.5 seconds and at completion. The
growth check uses the maximum sampled value minus the baseline; the report also
records peak traced bytes over the entire run. This is **not process RSS**, does not
measure untraced native allocations, and can miss peaks between samples. Tracing
itself affects timing. Report construction and file writing occur after measurements.

The stalled subscriber verifies bounded drop-oldest behavior. It does not simulate
a real network client's transport buffering. History, latency metrics, timestamp
tracking, and queues remain bounded by the stated configuration.

## Run and inspect

From the repository root, with the project's development environment installed:

```powershell
.\.venv\Scripts\python.exe scripts/soak_test.py --output docs/validation/host-soak-20261012.json
```

On Linux/macOS use `.venv/bin/python` instead. Exit status is zero only when every
reported acceptance check passes. The JSON stores the exact configuration, platform,
Python version, UTC start time, Git HEAD and dirty state at launch, SHA256 hashes of
the harness and relevant source files at launch, observed metrics, and each check's
result. A dirty working tree means the tested code includes local
changes beyond the recorded HEAD; record its eventual published commit in WORKFLOW.md.

For a quick harness check, use `--duration 5 --warmup 1`; this is a different profile
and must not be presented as the 600-second soak. The short regression test exercises
boot changes, reconnects, accounting, threshold failures, and histogram overflow.

## Observed October 12 run

The [600-second report](validation/host-soak-20261012.json) passed all 17 checks:
60,000 frames generated, received, accepted and consumed by the fast subscriber;
zero parse, freshness, capacity or ordering errors; 59 scripted resets; 20 boot
changes; and exactly 59,992 deliberate stalled-subscriber drops. Every device
retained 120 readings and the stalled queue retained the latest eight indices.

Elapsed time was 600.006 seconds (99.999 frames/s). Fast-subscriber p95 latency
was at most 1 ms, with a 4.872 ms maximum. Maximum sampled current traced Python
allocation growth above the warmup baseline was 333 bytes across 1,129 samples,
below the predefined 5 MiB limit. These measurements have the scope limits above.

The run began with uncommitted implementation changes. Its ten source-file hashes
were verified unchanged at completion, and content matched published commit
`a9b692183be6b5f84aff19870b77b5141f917ebb` after CRLF/LF normalization. See
[WORKFLOW.md](../WORKFLOW.md) for publication and CI evidence.
