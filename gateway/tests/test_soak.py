from __future__ import annotations

import asyncio

from scripts.soak_test import LatencyHistogram, SoakConfig, acceptance_checks, run_soak


def test_short_host_soak_accounts_for_sessions_reconnects_and_slow_drops() -> None:
    config = SoakConfig(
        duration_seconds=0.12,
        rate_hz=200,
        warmup_seconds=0.02,
        memory_sample_seconds=0.01,
        history_size=3,
        subscriber_queue_size=2,
        reconnect_every=5,
        samples_per_boot=2,
        max_duration_overrun_fraction=5,  # Scheduling jitter dominates a 120 ms unit test.
    )
    report = asyncio.run(run_soak(config))
    assert report["passed"], report["checks"]
    metrics = report["metrics"]
    assert metrics["generated"] == metrics["frames_accepted"] == metrics["fast_consumed"] == 24
    assert metrics["websocket_drops"] == 22
    assert metrics["slow_retained_indices"] == [22, 23]
    assert metrics["source_reconnects"] == 4
    assert metrics["session_changes"] == 8
    assert metrics["history_sizes"] == {f"soak-{device}": 3 for device in range(4)}

    failed = acceptance_checks({
        **metrics,
        "frames_accepted": 23,
        "fast_latency_p95_upper_bound_ms": 51,
        "python_memory_growth_bytes": 5 * 1024 * 1024 + 1,
        "websocket_drops": 23,
    }, config)
    assert not failed["accepted_matches_generated"]
    assert not failed["fast_p95_within_limit"]
    assert not failed["python_memory_growth_within_limit"]
    assert not failed["slow_drops_fully_accounted"]


def test_latency_histogram_uses_conservative_bounded_percentile() -> None:
    histogram = LatencyHistogram()
    for _ in range(95):
        histogram.add(1.01)
    for _ in range(5):
        histogram.add(1500)
    assert histogram.p95_upper_bound() == 2
    histogram.add(1500)
    assert histogram.p95_upper_bound() is None
    assert len(histogram.bins) == 1001
    assert histogram.overflow == 6
