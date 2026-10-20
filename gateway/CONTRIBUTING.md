# Contributing

Small, reviewable changes are welcome. Open an issue before changing the UART schema or public API because both are compatibility surfaces.

## Development loop

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
ruff check .
pytest --cov=sensor_monitor
sensor-monitor validate examples/telemetry.jsonl
```

New behavior should include a focused test. Hardware-dependent changes should also preserve a simulator or replay test path and document the exact board, firmware revision, wiring, and capture used for validation.
