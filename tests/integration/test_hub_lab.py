"""Test evidence persistence, cancellation and control isolation without hardware."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from hub_lab import CATALOG, Lab, Reports
from interview_demo import make_app
from sensor_monitor.tcp_source import TcpSource


class Record:
    def model_dump(self, **kwargs):
        return {"boot_id": "1", "sequence": 42}


class FakeDemo:
    def __init__(self):
        self.emulator = SimpleNamespace()
        self.lock = asyncio.Lock()
        self.actions = []
        self.app = SimpleNamespace(state=SimpleNamespace(hub=SimpleNamespace(stats=Record)))

    async def latest(self):
        return Record(), Record()

    async def perform(self, action):
        self.actions.append(action)
        return {"reading": {"boot_id": "1"}, "health": {"state": "online"}, "message": "observed"}


def test_failure_is_documented_and_later_tests_continue(tmp_path):
    async def check():
        demo = FakeDemo()
        lab = Lab(demo, tmp_path, 1, reports=tmp_path)

        async def execute(entry):
            lab.check(entry, "driver conversion", 1, 0 if entry["id"] == "baseline" else 1,
                      entry["id"] != "baseline")

        lab.test = execute
        run_id = lab.start(["baseline", "accel"])
        with pytest.raises(HTTPException) as error:
            lab.start(["gyro"])
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            await lab.manual("motion")
        await lab.task
        report = json.loads(lab.reports.path(run_id).read_text())
        assert report["status"] == "failed"
        assert [c["status"] for c in report["cases"]] == ["failed", "passed"]
        assert report["cases"][0]["checks"][0]["actual"] == 0
        assert report["cases"][0]["before"]["boot_id"] == "1"
        assert report["cleanup"] == "sensors restored; board online"
        assert demo.actions == ["rest", "rest", "rest"]
        assert Reports(tmp_path).recent()[0]["counts"]["failed"] == 1
    asyncio.run(check())


def test_cancel_finishes_current_check_and_restores_sensors(tmp_path):
    async def check():
        demo = FakeDemo()
        lab = Lab(demo, tmp_path, 1, reports=tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def execute(entry):
            entered.set()
            await release.wait()
            lab.check(entry, "complete", True, True, True)

        lab.test = execute
        run_id = lab.start(["baseline", "accel"])
        await entered.wait()
        lab.cancel_requested = True
        release.set()
        await lab.task
        report = json.loads(lab.reports.path(run_id).read_text())
        assert report["status"] == "cancelled"
        assert [c["status"] for c in report["cases"]] == ["passed", "cancelled"]
        assert demo.actions[-1] == "rest"
        assert not lab.busy
    asyncio.run(check())


def test_shutdown_records_interrupted_case(tmp_path):
    async def check():
        lab = Lab(FakeDemo(), tmp_path, 1, reports=tmp_path)
        entered = asyncio.Event()

        async def execute(entry):
            entered.set()
            await asyncio.Event().wait()

        lab.test = execute
        run_id = lab.start(["baseline", "accel"])
        await entered.wait()
        await lab.close()
        report = json.loads(lab.reports.path(run_id).read_text())
        assert report["status"] == "cancelled"
        assert all(c["status"] == "cancelled" for c in report["cases"])
        assert report["cleanup"] == "sensors restored; board online"
    asyncio.run(check())


def test_manual_failure_is_saved_and_unlocks_next_run(tmp_path):
    async def check():
        demo = FakeDemo()
        lab = Lab(demo, tmp_path, 1, reports=tmp_path)

        async def failure(action):
            raise TimeoutError("No recovery frame <script>alert(1)</script>")

        demo.perform = failure
        with pytest.raises(TimeoutError):
            await lab.manual("motion")
        assert not lab.busy
        report = lab.current
        assert report["status"] == "failed"
        assert report["cases"][0]["before"]
        document = lab.reports.path(report["id"], "html").read_text(encoding="utf-8")
        assert "<script>" not in document
        assert "&lt;script&gt;" in document
    asyncio.run(check())


def test_history_is_bounded_and_report_paths_cannot_escape(tmp_path):
    reports = Reports(tmp_path)
    assert reports.recent() == []
    for i in range(55):
        report = {"id": f"run-{i:04}", "kind": "suite", "status": "passed", "started_at": "now",
                  "cases": [dict(CATALOG[0], status="passed")]}
        reports.save(report)
    assert len(reports.recent()) == 50
    assert reports.recent()[0]["id"] == "run-0054"
    report["status"] = "failed"
    reports.save(report)
    assert reports.recent()[0]["status"] == "failed"
    assert len(list(tmp_path.glob("*.json"))) == 55
    assert not list(tmp_path.glob("*.tmp"))
    for unsafe in ("../secret", "..\\secret", "C:\\secret", "a/b", "a" * 81):
        with pytest.raises(ValueError):
            reports.path(unsafe)


def test_runner_error_cannot_be_reported_as_a_pass(tmp_path):
    async def check():
        lab = Lab(FakeDemo(), tmp_path, 1, reports=tmp_path)
        original_save = lab.reports.save
        calls = 0

        def fail_once(report):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Test evidence write interrupted")
            original_save(report)

        lab.reports.save = fail_once
        run_id = lab.start(["baseline"])
        await lab.task
        report = json.loads(lab.reports.path(run_id).read_text())
        assert report["status"] == "failed"
        assert report["runner_error"] == "Test evidence write interrupted"
        assert report["cases"][0]["status"] == "cancelled"
    asyncio.run(check())


def test_suite_api_validates_auth_selection_and_busy_state(tmp_path):
    async def check():
        app = make_app(SimpleNamespace(), TcpSource("127.0.0.1", 1), tmp_path, 1)
        app.state.lab.reports = Reports(tmp_path / "reports")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            assert (await client.get("/lab")).status_code == 200
            assert (await client.get("/lab-assets/lab.js")).status_code == 200
            assert (await client.post("/api/v1/lab/run", json={"cases": ["baseline"]})).status_code == 403
            headers = {"x-demo-token": app.state.demo.token}
            assert (await client.post("/api/v1/lab/cancel", headers=headers)).status_code == 503
            app.state.demo.ready = True
            for ids in ([], ["unknown"], ["baseline", "baseline"]):
                assert (await client.post("/api/v1/lab/run", headers=headers, json={"cases": ids})).status_code == 422
            async with app.state.demo.lock:
                assert (await client.post("/api/v1/lab/run", headers=headers, json={"cases": ["baseline"]})).status_code == 409
            assert (await client.get("/api/v1/lab/reports/missing")).status_code == 404
            assert (await client.get("/api/v1/lab/reports/missing?format=exe")).status_code == 422
    asyncio.run(check())
