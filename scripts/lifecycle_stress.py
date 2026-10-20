"""Check discarded app collection and repeated localhost server shutdown."""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import tracemalloc
import weakref
from pathlib import Path
from types import SimpleNamespace

from sensor_monitor.config import Settings
from stress_test import ROOT, create_app, process_resources, run


def factory_cycles(count: int, output: Path) -> dict:
    settings = Settings(log_dir=output.parent / "stress-logs", log_level="ERROR")

    def discard_app() -> bool:
        application = create_app(settings)
        refs = [weakref.ref(obj) for obj in (
            application, application.state.hub, application.state.source,
        )]
        del application
        gc.collect()
        return all(ref() is None for ref in refs)

    for _ in range(10):
        discard_app()
    tracemalloc.start()
    baseline = tracemalloc.take_snapshot()
    samples = []
    collected = 0
    for cycle in range(1, count + 1):
        collected += discard_app()
        if cycle % 25 == 0 or cycle == count:
            samples.append({"cycle": cycle, "python_bytes": tracemalloc.get_traced_memory()[0],
                            **process_resources()})
    growth = tracemalloc.take_snapshot().compare_to(baseline, "lineno")
    tracemalloc.stop()
    checks = {
        "every_app_hub_and_source_collected": collected == count,
        "python_retained_below_256_KiB": samples[-1]["python_bytes"] < 256 * 1024,
    }
    return {"cycles": count, "collected_cycles": collected, "samples": samples,
            "top_growth": [str(item) for item in growth[:10]], "checks": checks,
            "passed": all(checks.values()),
            "scope": "application-factory lifecycles after 10 warmup cycles; continuous tracemalloc"}


async def server_cycles(count: int, binary: Path, output: Path) -> dict:
    samples = []
    functional_checks = []
    for cycle in range(1, count + 1):
        # Three seconds is enough for lifecycle coverage, not a memory-slope
        # estimate. Sustained memory gates are checked by the ten-minute run.
        args = SimpleNamespace(binary=binary, rate=200, duration=3,
                               warmup=0.5, sample_interval=0.5,
                               output=ROOT / "build" / f"shutdown-cycle-{cycle}.json")
        report = await run(args)
        functional_checks.append({key: value for key, value in report["checks"].items()
                                  if not any(word in key for word in ("growth", "slope"))})
        del report
        gc.collect()
        await asyncio.sleep(0.1)
        gc.collect()
        samples.append({"cycle": cycle, "tasks": len(asyncio.all_tasks()), **process_resources()})
    baseline = samples[1]
    checks = {
        "functional_checks_passed": all(all(c.values()) for c in functional_checks),
        "only_controller_task_remains": all(s["tasks"] == 1 for s in samples),
        "no_handle_accumulation": max(s["handles"] for s in samples[1:]) <= baseline["handles"] + 4,
        "no_thread_accumulation": all(s["threads"] == 1 for s in samples),
        "working_set_growth_bounded": samples[-1]["working_set"] - baseline["working_set"] < 4 * 1024**2,
    }
    return {"cycles": samples, "checks": checks, "functional_checks": functional_checks,
            "passed": all(checks.values()),
            "scope": "independent server start/stop cycles in one event loop; post-GC resources"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=200)
    parser.add_argument("--server-cycles", type=int, default=6)
    parser.add_argument("--binary", type=Path, default=ROOT / "build/host/sensor_hub_sim.exe")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/validation/lifecycle-stress-20261017.json")
    args = parser.parse_args()
    if args.cycles < 25 or args.server_cycles < 3:
        parser.error("require at least 25 factory cycles and 3 server cycles")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"factory": factory_cycles(args.cycles, args.output),
              "servers": asyncio.run(server_cycles(args.server_cycles, args.binary.resolve(), args.output)),
              "source_sha256": {
                  str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (Path(__file__).resolve(), ROOT / "gateway/src/sensor_monitor/app.py")
              }}
    report["passed"] = report["factory"]["passed"] and report["servers"]["passed"]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "output": str(args.output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
