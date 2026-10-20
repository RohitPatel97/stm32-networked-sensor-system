# Combined project workflow

## October 12, 2026 — integration

**Problem:** separately tested firmware and gateway had incompatible live UART
contracts. Legacy capture conversion omitted missing-sensor records and firmware
diagnostics; boot IDs were operator-assigned. Cumulative error counters could
leave recovered hardware degraded, and a sample sequence could not distinguish
live health telemetry from stalled acquisition.

**Implementation:** imported both projects into a new repository; added shared
v3 telemetry, STM32 UID/epoch identity and persistent boot journal, frame/sample
ordering, nullable sensor ingestion, recent-counter fault recovery, diagnostic
API/browser fields, and a direct compiled-simulator source. Added cross-component
tests, schema/fixture checks, root CI, deployment guidance, and validation tools.

**Observed evidence:** [validation](docs/VALIDATION.md) records 249 pytest cases,
four firmware CTest suites, live browser inspection, and both ARM watchdog builds.
The [original manifest](docs/originals-manifest.json) verifies both original source
trees remain unchanged. This new project has not been pushed or deployed.

**Next hardware work:** complete board flash/power-cut/UART/recovery acceptance,
then measure target timing, stack use, watchdog behavior, and Pi soak results.
Keep new evidence distinct from the imported components' historical records.

## October 12, 2026 — expanded test cases

Added 18 fault-matrix cases and expanded the compiled-C-to-dashboard test to six
sensor/rate combinations. Fixed its startup race by waiting for the WebSocket
subscription before starting the simulator. All 249 Python tests and four CTest
suites pass; the dashboard cases passed ten consecutive runs. A 60-second legacy
compatibility stress run accepted 15,000 frames across four devices with no parse
or ordering errors. The live v3 demo and original-source integrity checks also
passed. See the [full results and evidence](docs/validation/MULTIPLE-CASES.md).

## October 17, 2026 — isolated build and runtime checks

Built a new source copy with a fresh Python environment, installed gateway wheel,
and Debug C undefined-behavior instrumentation. The initial 249 tests passed,
but a new targeted test exposed a lost-frame window between the WebSocket's
initial snapshot and subscription. Registered the subscriber before capturing
and sending the snapshot, with regression coverage for delivery and disconnect
cleanup. All 251 tests, four C suites, 5,000 seeded decoder recovery cases, and
real localhost HTTP/WebSocket plus dashboard-renderer checks passed. Python
statement/branch coverage was 96.22%. Both originals remain unchanged.

The [sandbox report](docs/validation/sandbox-20261017/REPORT.md) records commands,
failure evidence, final results, hashes, and isolation limits. This was a separate
source tree/environment on Windows; Docker and physical hardware were not used.

## October 17, 2026 — sustained stress and lifecycle fixes

Added repeatable sustained-runtime, serial-reconnect, and application-lifecycle
stress runners. The tests exposed missing subscriber queue completion accounting,
early timer wakeups that violated simulator pacing, and retained application
instances caused by endpoint closures held in FastAPI's callable metadata caches.
Fixed queue accounting, rechecked pacing against a high-resolution deadline, and
shared endpoint functions that obtain each app's state from the request/socket.

The full journal test now exercises all 6,553 slots with 505 interrupted writes.
The [stress report](docs/validation/STRESS-20261017.md) records sustained runtime,
serial failures, before/after memory evidence, shutdown cleanup, and the final
255 Python tests plus four C suites. Both original source trees remain unchanged.

## October 17, 2026 — real ARM execution in Renode

Added an STM32F401RE platform, MPU6050/BMP280 register models, a bounded loopback
UART bridge, an owned emulator launcher, and an ARM-to-REST/WebSocket acceptance
runner. Actual target execution exposed unsupported `%llu` formatting in
newlib-nano and starvation of lower-priority tasks during repeated polling I2C
timeouts. Fixed both, plus a gateway startup-health alert race.

The [Renode guide](docs/RENODE.md) describes launch, test commands and model
limits. [Validation evidence](docs/validation/RENODE-20261017.md) covers sensor
faults/recovery, flash boot identity, reconnects and an IWDG-triggered reset.
Physical board acceptance remains outstanding.
