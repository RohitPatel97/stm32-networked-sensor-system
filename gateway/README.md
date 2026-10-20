# STM32 Sensor Gateway

Gateway component of the [STM32 Networked Sensor System](../README.md).

The Python service consumes shared v3 firmware JSON directly over serial or
from the compiled C simulator. It validates records, enforces boot/frame/sample
ordering, maintains bounded history, evaluates current faults, and publishes
REST/WebSocket data to its browser dashboard.

From the combined project root, install `python -m pip install -e "./gateway[dev]"`
and run `python scripts/demo.py` after building the firmware simulator.
This is the complete C-to-dashboard demonstration. `sensor-monitor serve`
without configuration retains the separate Python application simulator for
legacy API/fault demonstrations.

For serial input set `SENSOR_MONITOR_SOURCE=serial` and
`SENSOR_MONITOR_SERIAL_PORT` to the board's device path, then run
`sensor-monitor serve`. Configuration remains in [.env.example](.env.example).
Deployment guidance is in [the combined deploy directory](../deploy/README.md).

The [shared protocol](../protocol/README.md) is authoritative for v3. Missing
sensor values remain null; diagnostics are retained under `firmware`.
Cumulative error totals are separate from current health. Communication age,
sample age, and individual sensor availability are exposed separately.
Top-level v3 API boot IDs are decimal strings for browser precision.

REST endpoints remain `/api/v1/snapshot`, `/api/v1/devices`,
`/api/v1/devices/{id}/readings`, `/api/v1/alerts`, and `/api/v1/stats`.
WebSocket updates use `/ws/telemetry`; `/healthz` reports source readiness.
Interactive HTTP documentation is at `/docs`.

Gateway wire v1/v2 and `convert-freertos` remain for historical captures.
Their [legacy session policy](docs/PROTOCOL.md) and [schema-1 bridge](docs/BRIDGE.md)
are separate compatibility references. V3 does not need capture conversion.

Run all combined gates with `python scripts/verify.py` from the root.
The [validation report](../docs/VALIDATION.md) records the observed results.
Imported WORKFLOW and old reports describe the original repository.

History, session baselines, and alerts are in memory. The service does not
authenticate producers. Physical UART/Pi/systemd acceptance remains pending.
