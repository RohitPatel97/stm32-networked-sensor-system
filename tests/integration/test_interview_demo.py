"""Interview controls must stay isolated, authenticated, and limited to named actions."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from interview_demo import make_app  # noqa: E402
from sensor_monitor.app import create_app  # noqa: E402
from sensor_monitor.config import Settings  # noqa: E402
from sensor_monitor.tcp_source import TcpSource  # noqa: E402


def test_controls_reject_untrusted_and_invalid_requests(tmp_path):
    async def check():
        app = make_app(SimpleNamespace(), TcpSource("127.0.0.1", 1), tmp_path, 8765)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1:8765") as client:
            info = (await client.get("/api/v1/demo")).json()
            assert info["ready"] is False
            assert (await client.post("/api/v1/demo/action", json={"action": "reboot"})).status_code == 403
            headers = {"x-demo-token": info["token"]}
            assert (await client.post("/api/v1/demo/action", headers=headers,
                                      json={"action": "quit"})).status_code == 422
            assert (await client.post("/api/v1/demo/action", headers=headers,
                                      json={"action": "reboot", "command": "quit"})).status_code == 422
            assert (await client.post("/api/v1/demo/action", headers=headers,
                                      json={"action": "reboot"})).status_code == 503
            assert (await client.get("/api/v1/demo", headers={"host": "untrusted.example"})).status_code == 400
            app.state.demo.ready = True
            async with app.state.demo.lock:
                assert (await client.post("/api/v1/demo/action", headers=headers,
                                          json={"action": "reboot"})).status_code == 409
    asyncio.run(check())


def test_production_gateway_has_no_renode_controls(tmp_path):
    async def check():
        app = create_app(Settings(log_dir=tmp_path), TcpSource("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://127.0.0.1") as client:
            assert (await client.get("/api/v1/demo")).status_code == 404
            assert (await client.post("/api/v1/demo/action", json={"action": "reboot"})).status_code == 404
            assert (await client.get("/api/v1/lab")).status_code == 404
            assert (await client.post("/api/v1/lab/run", json={"cases": ["reboot"]})).status_code == 404
    asyncio.run(check())
