# Sustained stress checks

Run these from the combined project root with the gateway development dependencies
installed and the C simulator built. Both original project directories are left
alone. The tests exercise host software and modeled failures; they do not flash
or connect to physical hardware.

## Ten-minute v3 runtime test

```powershell
.venv/Scripts/python.exe scripts/stress_test.py --duration 600 --warmup 60 --rate 200 --binary build/host/sensor_hub_sim.exe --output docs/validation/stress-run.json
```

The runner executes a new C simulator process for each boot. Every simulated boot
includes an outage of both sensors followed by recovery. The boot sequence starts
above JavaScript's exact-integer range to exercise decimal boot IDs at the API.
The real decoder receives fragmented records, partial streams cut by reconnects,
delayed previous-boot frames, duplicates, and oversized records. All intentional
rejections are counted separately from unexpected failures.

A real localhost Uvicorn service serves a persistent WebSocket client and another
client that repeatedly connects, receives events, and disconnects. In-process
fast and slow subscribers verify complete ordered delivery, bounded history,
drop accounting, queue draining, and subscriber cleanup. The slow subscriber
deliberately discards old data when its queue fills.

The requested rate is the simulator's pacing ceiling. OS scheduling, processing,
process startup, and instrumentation reduce achieved throughput; the report
records the actual rate. Simulation time is accelerated relative to physical
sensor acquisition. This is not a hardware throughput measurement.

After warmup, samples record Python allocations after garbage collection,
working set, Windows private committed memory, open handles, live asyncio tasks,
and Python threads. The report compares equal early/late sample windows and
fits a slope. Its gates allow less than 2 MiB Python allocation growth and
64 KiB/minute slope, less than 16 MiB Windows private-memory growth and
1 MiB/minute slope, less than 32 MiB working-set growth, no more than 16 additional
handles, and no more than 10 additional tasks. These measurements cover the
service and bounded test clients in one process; they are not measurements of
the simulator's RSS or physical-device RAM. A finite passing run does not prove
that leaks are impossible under all workloads.

The JSON output includes all checks, resource samples, source/binary hashes, and
cleanup results. A sibling `.progress.json` file updates every 30 seconds.
The service binds an ephemeral loopback port and exits after the run.

## Serial lifecycle and reboot stress

```powershell
.venv/Scripts/python.exe scripts/reconnect_stress.py --boots 500 --binary build/host/sensor_hub_sim.exe --output docs/validation/reconnect-run.json
```

This executes 500 fresh C boot captures through the production `SerialSource`
using 1,000 scripted ports. It includes failed opens, read failures, partial and
oversized tails, idle reads, duplicate frames, and delayed prior-boot frames.
The test checks that each opened port closes exactly once, the source and
subscribers are released, and all fresh frames arrive in order. PySerial's
physical driver and USB enumeration are not exercised.

## Full boot-journal capacity

The normal `boot_and_protocol_tests` CTest suite now also fills all 6,553 journal
slots with 505 injected interrupted writes. It verifies 6,048 published boot IDs
are strictly increasing and that exhaustion fails without further programming.
This validates the journal algorithm against a host flash-storage model; actual
STM32 power interruption and flash retention still require board testing.

## Application and server lifecycle checks

```powershell
.venv/Scripts/python.exe scripts/lifecycle_stress.py --cycles 200 --server-cycles 6 --binary build/host/sensor_hub_sim.exe --output docs/validation/lifecycle-run.json
```

After ten warmup cycles, create and discard 200 applications while continuously
tracing Python allocations. Weak references verify that each app, hub, and source
is collected; retained allocations must stay below 256 KiB. Then run six short
localhost services through complete startup/shutdown, collecting garbage before
checking remaining tasks, threads, handles, and working set. These short cycles
check cleanup; the ten-minute test supplies sustained-memory evidence.

Use the [validation directory](validation/) for recorded runs. The October 17
results and fixes are summarized in [the stress report](validation/STRESS-20261017.md),
with raw sustained, serial-reconnect, and lifecycle reports beside it.
