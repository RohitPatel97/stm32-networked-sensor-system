"""Export or check the JSON Schema used by the live gateway validator."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway" / "src"))
from sensor_monitor.shared_protocol import UnifiedFrame


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = ROOT / "protocol" / "telemetry-v3.schema.json"
    content = json.dumps(UnifiedFrame.model_json_schema(), indent=2) + "\n"
    if args.check:
        if path.read_text(encoding="utf-8") != content:
            raise SystemExit(
                "Protocol schema differs from the runtime validator; regenerate it"
            )
    else:
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
