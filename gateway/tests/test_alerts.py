from __future__ import annotations

from sensor_monitor.alerts import AlertEngine
from sensor_monitor.config import Settings

from .helpers import reading


def test_alerts_are_transition_based_and_include_recovery() -> None:
    engine = AlertEngine(Settings())
    first = engine.evaluate(reading(temperature_c=85.0))
    assert [(alert.code, alert.active) for alert in first] == [
        ("temperature_critical", True)
    ]
    assert engine.evaluate(reading(sequence=43, temperature_c=86.0)) == []
    recovered = engine.evaluate(reading(sequence=44, temperature_c=25.0))
    assert [(alert.code, alert.active) for alert in recovered] == [
        ("temperature_critical", False)
    ]


def test_fault_counters_and_low_battery_raise_independent_alerts() -> None:
    engine = AlertEngine(Settings())
    alerts = engine.evaluate(
        reading(battery_v=10.2, i2c_errors=3, queue_drops=2)
    )
    assert {alert.code for alert in alerts} == {
        "battery_low",
        "i2c_errors",
        "queue_drops",
    }


def test_warning_escalation_does_not_claim_false_recovery() -> None:
    engine = AlertEngine(Settings())
    warning = engine.evaluate(reading(temperature_c=65.0))
    assert [alert.code for alert in warning] == ["temperature_warning"]
    escalated = engine.evaluate(reading(sequence=43, temperature_c=85.0))
    assert [(alert.code, alert.active) for alert in escalated] == [
        ("temperature_critical", True)
    ]
