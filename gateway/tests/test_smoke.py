from __future__ import annotations

import io
import json

import pytest

from scripts import smoke_test


@pytest.mark.parametrize("accepted,devices,readings", [
    (0, [], []),
    (1, [], []),
    (1, [{"device_id": "test"}], []),
    (1, [{"device_id": "test"}], [{"device_id": "other", "sequence": 0}]),
])
def test_smoke_rejects_process_without_confirmed_device_history(
    monkeypatch, accepted, devices, readings,
) -> None:
    responses = iter([
        {"status": "ok", "source": "simulator"},
        {"devices": devices, "stats": {"frames_accepted": accepted}}, readings,
    ])
    monkeypatch.setattr(smoke_test, "urlopen", lambda *a, **kw: io.BytesIO(
        json.dumps(next(responses)).encode()
    ))
    with pytest.raises(AssertionError):
        smoke_test.check("http://127.0.0.1:8000")


def test_smoke_confirms_acquisition_store_and_api(monkeypatch) -> None:
    responses = iter([
        {"status": "ok", "source": "simulator"},
        {"devices": [{"device_id": "test"}], "stats": {"frames_accepted": 3}},
        [{"device_id": "test", "sequence": 2}],
    ])
    urls = []

    def fetch(url, timeout):
        urls.append(url)
        assert timeout == 3
        return io.BytesIO(json.dumps(next(responses)).encode())

    monkeypatch.setattr(smoke_test, "urlopen", fetch)
    assert smoke_test.check("http://127.0.0.1:8000/") == {
        "source": "simulator", "accepted": 3, "devices": 1, "last_sequence": 2,
    }
    assert urls[-1] == "http://127.0.0.1:8000/api/v1/devices/test/readings?limit=1"
