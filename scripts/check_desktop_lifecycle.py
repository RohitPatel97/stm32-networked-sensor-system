"""Exercise the desktop's real stdin lifecycle against Renode on an isolated port."""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
from renode_demo import free_port
from renode_runtime import ROOT


def launch(port):
    return subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "scripts/interview_demo.py"),
         "--port", str(port), "--no-browser", "--desktop-control"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def send(process, command):
    process.stdin.write(command + "\n")
    process.stdin.flush()


def ready(process, client):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(process.stdout.read())
        try:
            info = client.get("/api/v1/demo").json()
            if info["ready"]:
                return info
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.15)
    raise TimeoutError("desktop preflight did not complete")


def finish(process, command="shutdown"):
    if command:
        send(process, command)
    process.stdin.close()
    process.wait(timeout=30)
    output = process.stdout.read()
    assert process.returncode == 0, output
    assert "Traceback" not in output, output
    return output


def main():
    logging.getLogger("httpx").setLevel(logging.WARNING)
    results = {"started_at": datetime.now(UTC).isoformat(), "cases": []}
    processes = []
    directory = ROOT / "docs/validation/desktop-20261020"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        port = free_port()
        process = launch(port)
        processes.append(process)
        finish(process, command=None)
        results["cases"].append({"case": "parent_eof_before_start", "status": "passed"})

        process = launch(port)
        processes.append(process)
        send(process, "start")
        time.sleep(0.7)
        finish(process)
        results["cases"].append({"case": "close_during_startup", "status": "passed"})

        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
            process = launch(port)
            processes.append(process)
            send(process, "start")
            info = ready(process, client)
            response = client.post("/api/v1/lab/run", json={"cases": ["soak", "reboot"]},
                                   headers={"x-demo-token": info["token"]})
            response.raise_for_status()
            run_id = response.json()["run_id"]
            time.sleep(1.5)
            finish(process)
            saved = ROOT / "var/lab-reports" / (run_id + ".json")
            report = json.loads(saved.read_text(encoding="utf-8"))
            assert report["status"] == "cancelled", report
            assert all(c["status"] == "cancelled" for c in report["cases"]), report
            (directory / "cancelled-on-close.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
            results["cases"].append({"case": "close_during_suite_saves_cancellation_and_releases_port",
                                     "status": "passed", "run_id": run_id})

            process = launch(port)
            processes.append(process)
            send(process, "start")
            ready(process, client)
            saved_response = client.get("/api/v1/lab/reports/" + run_id)
            saved_response.raise_for_status()
            assert saved_response.json()["status"] == "cancelled"
            finish(process, command=None)
            results["cases"].append({"case": "restart_preserves_report_then_parent_eof_stops_gateway",
                                     "status": "passed"})
    finally:
        for process in processes:
            if process.poll() is None:
                try:
                    finish(process)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait()
        results["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "lifecycle.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
