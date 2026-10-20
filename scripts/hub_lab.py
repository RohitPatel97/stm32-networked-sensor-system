"""Documented Renode tests and durable, bounded-view run history for the desktop lab."""
from __future__ import annotations

import asyncio
import hashlib
import heapq
import html
import json
import math
import re
import secrets
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from renode_runtime import ROOT
from websockets.asyncio.client import connect

WEB = ROOT / "demo/web"
router = APIRouter()


def case(key, title, component, expected, method):
    return {"id": key, "title": title, "component": component,
            "expected": expected, "method": method}


CATALOG = [
    case("baseline", "Boot and calibrated readings", "CPU / RTOS", "Both sensors online; 25.08 C, 1006.53 hPa, [0,0,1] g, zero rotation; watchdog fed.", "Execute the ARM ELF and inspect validated firmware telemetry."),
    case("accel", "Accelerometer XYZ", "MPU6050", "[0.5, -0.25, 0.75] g on all three axes.", "Set signed MPU6050 acceleration registers through Renode."),
    case("gyro", "Gyroscope XYZ", "MPU6050", "[5, -20, 10] degrees/second on all three axes.", "Set signed gyroscope registers and verify firmware scaling."),
    case("imu_temp", "IMU die temperature", "MPU6050", "Raw 3400 converts to 46.53 C.", "Change the MPU6050 temperature register independently of the BMP280."),
    case("imu_limits", "Signed IMU endpoints", "MPU6050", "Signed 16-bit endpoint conversion on acceleration and gyro, including zero.", "Inject -32768, 0 and 32767 and check each decoded axis."),
    case("baro_temp", "Barometer compensation", "BMP280", "Raw temperature 530000 compensates to 28.25 C.", "Use the BMP280 calibration coefficients in the actual C driver."),
    case("altitude", "Pressure and derived altitude", "BMP280", "946.43 hPa and about 571.78 m; formula independently checked.", "Set pressure ADC value 450000 and verify firmware compensation and altitude."),
    case("imu_fault", "IMU disconnect and recovery", "I2C1", "IMU null/offline; BMP280 remains valid; recovery without reboot.", "Unregister the MPU6050 peripheral, then attach a fresh model."),
    case("baro_fault", "Barometer disconnect and recovery", "I2C1", "Barometer null/offline; IMU remains valid; recovery without reboot.", "Unregister the BMP280 peripheral, then attach a fresh model."),
    case("both_fault", "Both sensors disconnected", "I2C1", "Both measurements unavailable; diagnostic frames continue; automatic recovery.", "Remove both I2C slaves, then reattach them."),
    case("gpio", "GPIO alternate functions", "GPIO / clocks", "PB8/PB9 AF4 open drain; PA2/PA3 AF7; used GPIO modes configured.", "Read back GPIO mode, output-type and alternate-function registers."),
    case("clocks", "Peripheral clock enables", "GPIO / clocks", "GPIOA/B, TIM2, USART2 and I2C1 clocks enabled by firmware.", "Read RCC enable bits; this does not measure oscillator accuracy."),
    case("timer", "TIM2 and NVIC setup", "Timers / IRQ", "TIM2 enabled, PSC 8399, ARR 99; interrupt 28 enabled.", "Read TIM2/NVIC registers and observe advancing sample sequences."),
    case("systick", "SysTick and DWT", "Timers / IRQ", "SysTick enabled with interrupt and 83999 reload; DWT cycle counter enabled and advancing.", "Read core timing registers around fresh firmware telemetry."),
    case("cadence", "Acquisition and telemetry cadence", "CPU / RTOS", "About 100 samples and 10 frames per firmware second, with zero new queue drops.", "Compare sample, frame and uptime deltas over at least 2 firmware seconds."),
    case("memory", "RTOS heap and task stacks", "SRAM", "Positive free heap and nonzero high-water marks for all four tasks.", "Resolve ELF symbols and read FreeRTOS diagnostics from emulated SRAM."),
    case("flash", "Boot journal contents", "Flash / identity", "Committed journal magic with complementary boot words; UID is mapped and stable.", "Read flash journal and UID memory; no destructive flash writes."),
    case("reboot", "MCU reset and boot identity", "Flash / identity", "Same device ID, boot +1, sequence and uptime restart; watchdog health resumes.", "Issue a machine reset; inspect the next firmware session."),
    case("uart", "UART configuration and reconnect", "USART2", "USART2 TX/RX enabled, 115200-baud divider configured; bridge reconnects, boot unchanged.", "Read UART registers, disconnect the loopback UART bridge, and verify new telemetry."),
    case("watchdog", "Independent watchdog recovery", "IWDG", "Stalled acquisition triggers a new boot and healthy telemetry.", "Disable TIM2; let the modeled hardware IWDG reset the MCU."),
    case("websocket", "WebSocket snapshot and updates", "Gateway", "A new client receives a snapshot followed by validated live telemetry.", "Connect to the actual gateway WebSocket endpoint."),
    case("soak", "30 second stability check", "CPU / RTOS", "Stable boot, free heap and bounded queues; frames continue with no new decode/order/drop errors.", "Observe 30 seconds of wall time and compare heap, stacks and gateway counters."),
]
BY_ID = {item["id"]: item for item in CATALOG}
COVERAGE = [
    {"name": "STM32F401RE Cortex-M4F / FreeRTOS", "level": "Functional", "tests": ["baseline", "cadence", "soak"], "limit": "Executes the ARM ELF; throughput is emulated, not a silicon benchmark."},
    {"name": "MPU6050 at 0x68", "level": "Register model", "tests": ["accel", "gyro", "imu_temp", "imu_limits", "imu_fault"], "limit": "Digital register/driver behavior; no motion physics, drift or noise calibration."},
    {"name": "BMP280 at 0x76", "level": "Register model", "tests": ["baro_temp", "altitude", "baro_fault"], "limit": "Compensation and derived altitude; physical accuracy is not modeled."},
    {"name": "I2C1 and sensor recovery", "level": "Functional", "tests": ["imu_fault", "baro_fault", "both_fault"], "limit": "Slave removal exercises NACK/timeouts; pull-ups, rise time and wiring require hardware."},
    {"name": "GPIOA/B and RCC", "level": "Configuration", "tests": ["gpio", "clocks"], "limit": "Used alternate-function registers only; no pin voltage or PLL-lock timing validation."},
    {"name": "TIM2 / NVIC / SysTick / DWT", "level": "Functional", "tests": ["timer", "systick", "cadence"], "limit": "Configured interrupts and counters; latency is emulator-dependent."},
    {"name": "SRAM / task stacks / heap", "level": "Diagnostic", "tests": ["memory", "soak"], "limit": "Checks live RTOS memory margins; not an exhaustive RAM or leak qualification."},
    {"name": "Flash boot journal / UID", "level": "Functional", "tests": ["flash", "reboot"], "limit": "Retained across MCU resets in a session; no wear, power-cut or journal-reclamation simulation."},
    {"name": "USART2 / transport", "level": "Functional", "tests": ["uart", "websocket"], "limit": "Model feeds a loopback TCP bridge; physical UART timing and USB cabling need hardware."},
    {"name": "Independent watchdog", "level": "Functional", "tests": ["watchdog"], "limit": "Reset and recovery verified; RCC cause flags are not faithful in this model."},
    {"name": "RTC / PWR / bit-band aliases", "level": "Support only", "tests": [], "limit": "Platform support dependencies; no application RTC, sleep or bit-band feature to exercise."},
    {"name": "ADC / SPI / CAN / USB / user LED and button", "level": "Not used", "tests": [], "limit": "Not used by this hub firmware; no test or hardware-coverage claim."},
]


def utc():
    return datetime.now(UTC).isoformat()


class Reports:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._cache = None

    def path(self, run_id, suffix="json"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id) or suffix not in ("json", "html"):
            raise ValueError("invalid report identifier")
        return self.directory / f"{run_id}.{suffix}"

    def save(self, report):
        path = self.path(report["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        rows = "".join(
            f'<tr><td>{html.escape(c["title"])}</td><td>{html.escape(c["status"])}</td>'
            f'<td>{html.escape(c["expected"])}</td><td><pre>{html.escape(json.dumps(c.get("checks", []), indent=2))}</pre>'
            f'{html.escape(c.get("error", ""))}</td></tr>' for c in report["cases"])
        content = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>STM32 test report</title>'
                   '<style>body{font:15px system-ui;margin:40px;color:#172b35}table{border-collapse:collapse;width:100%}'
                   'td,th{border:1px solid #cbd5db;padding:12px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;font-size:12px}'
                   'h1{color:#087f75}@media print{body{margin:0}tr{break-inside:avoid}}</style>'
                   f'<h1>STM32 Sensor Lab test report</h1><p>{html.escape(report["id"])} · {html.escape(report["status"])}</p>'
                   f'<p>Started {html.escape(report["started_at"])} · ELF SHA256 {html.escape(report.get("elf_sha256", "unavailable"))}</p>'
                   '<p>Renode digital emulation. Physical electrical behavior and sensor accuracy require hardware testing.</p>'
                   '<table><tr><th>Test</th><th>Result</th><th>Expected</th><th>Observed checks</th></tr>' + rows + '</table>'
                   '<h2>Full evidence</h2><pre>' + html.escape(json.dumps(report, indent=2)) + '</pre></html>')
        target = self.path(report["id"], "html")
        temporary = target.with_suffix(".html.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        if self._cache is not None:
            summary = {k: report[k] for k in ("id", "status", "started_at", "kind")} | {"counts": counts(report)}
            self._cache = sorted([summary] + [r for r in self._cache if r["id"] != report["id"]],
                                 key=lambda r: r["id"], reverse=True)[:50]

    def recent(self):
        if self._cache is not None:
            return self._cache
        result = []
        for path in heapq.nlargest(50, self.directory.glob("*.json"), key=lambda p: p.name):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                result.append({k: report[k] for k in ("id", "status", "started_at", "kind")}
                              | {"counts": counts(report)})
            except (ValueError, KeyError, OSError):
                continue
        self._cache = result
        return result


def counts(report):
    return {status: sum(c["status"] == status for c in report["cases"])
            for status in ("passed", "failed", "running", "pending", "cancelled")}


class Lab:
    def __init__(self, demo, directory, port, reports=None):
        self.demo, self.directory, self.port = demo, Path(directory), port
        self.reports = Reports(reports or ROOT / "var/lab-reports")
        self.task = None
        self.cancel_requested = False
        self.current = None
        self.memory = None
        self._symbols = {}

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def new_report(self, items, kind):
        elf = getattr(self.demo.emulator, "elf", None)
        report = {"id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4),
                  "kind": kind, "status": "running", "started_at": utc(), "finished_at": None,
                  "elf_sha256": hashlib.sha256(elf.read_bytes()).hexdigest() if elf else "unavailable",
                  "run_log": str(self.directory), "coverage": COVERAGE,
                  "cases": [dict(item, status="pending", checks=[]) for item in items]}
        self.reports.save(report)
        self.current = report
        return report

    def start(self, selected, kind="suite"):
        if self.busy or self.demo.lock.locked():
            raise HTTPException(409, "A test or demonstration is already running")
        if not selected or len(selected) != len(set(selected)) or any(k not in BY_ID for k in selected):
            raise HTTPException(422, "Select unique known test IDs")
        self.cancel_requested = False
        report = self.new_report([BY_ID[k] for k in selected], kind)
        self.task = asyncio.create_task(self.execute(report), name="renode-test-suite")
        return report["id"]

    def check(self, entry, label, expected, actual, passed):
        entry["checks"].append({"name": label, "expected": expected, "actual": actual, "passed": bool(passed)})
        if not passed:
            raise AssertionError(f"{label}: expected {expected}; observed {actual}")

    async def fresh(self, predicate=lambda h, r: True):
        _, old = await self.demo.latest()
        identity = (old.boot_id, old.sequence) if old else None
        return await self.demo.wait_for(lambda h, r: (r.boot_id, r.sequence) != identity and predicate(h, r))

    async def set_values(self, sensor, values):
        await self.demo.emulator.registers([f"sysbus.i2c1.{sensor} {k} {v}" for k, v in values.items()])

    async def read_word(self, address):
        return await self.demo.emulator.number(f"sysbus ReadDoubleWord {address:#x}")

    async def read_symbol(self, symbol, length=1):
        if symbol not in self._symbols:
            self._symbols[symbol] = await self.demo.emulator.number(f'sysbus GetSymbolAddress "{symbol}"')
        address = self._symbols[symbol]
        if not 0x20000000 <= address < 0x20018000 - (length - 1) * 4:
            raise RuntimeError(f"{symbol} did not resolve inside SRAM")
        return [await self.read_word(address + i * 4) for i in range(length)]

    async def memory_snapshot(self):
        self.memory = {"observed_at": utc(), "free_heap_bytes": (await self.read_symbol("xFreeBytesRemaining"))[0],
                       "minimum_heap_bytes": (await self.read_symbol("xMinimumEverFreeBytesRemaining"))[0],
                       "stack_minimum_words": dict(zip(("acquisition", "processing", "telemetry", "health"),
                                                      await self.read_symbol("hub_stack_high_water_words", 4)))}
        return self.memory.copy()

    async def execute(self, report):
        try:
            for entry in report["cases"]:
                if self.cancel_requested:
                    break
                entry.update(status="running", started_at=utc())
                self.reports.save(report)
                started = time.monotonic()
                try:
                    # Isolated baseline makes any selected subset reproducible.
                    await self.demo.perform("rest")
                    _, before = await self.demo.latest()
                    entry["before"] = before.model_dump(mode="json")
                    await self.test(entry)
                    entry["status"] = "passed"
                except asyncio.CancelledError:
                    entry["status"] = "cancelled"
                    raise
                except Exception as error:  # noqa: BLE001 - record any test failure and continue the suite
                    entry.update(status="failed", error=str(error))
                finally:
                    entry.update(finished_at=utc(), duration_seconds=round(time.monotonic() - started, 3))
                    health, after = await self.demo.latest()
                    entry["after"] = after.model_dump(mode="json") if after else None
                    entry["health"] = health.model_dump(mode="json") if health else None
                    self.reports.save(report)
        except asyncio.CancelledError:
            self.cancel_requested = True
        except Exception as error:  # noqa: BLE001 - runner failures must never become passes
            report["runner_error"] = str(error)
        finally:
            for entry in report["cases"]:
                if entry["status"] in ("pending", "running"):
                    entry["status"] = "cancelled"
            try:
                await self.demo.perform("rest")
                report["cleanup"] = "sensors restored; board online"
            except Exception as error:  # noqa: BLE001 - preserve cleanup failures in evidence
                report["cleanup"] = str(error)
            report["status"] = ("cancelled" if self.cancel_requested else
                                "failed" if report.get("runner_error") or counts(report)["failed"] or counts(report)["cancelled"] or report["cleanup"] != "sensors restored; board online" else "passed")
            report["finished_at"] = utc()
            report["stats"] = self.demo.app.state.hub.stats().model_dump(mode="json")
            self.reports.save(report)

    async def manual(self, action):
        if self.busy or self.demo.lock.locked():
            raise HTTPException(409, "Stop or finish the test suite before changing the board")
        # Claim the task slot synchronously so parallel HTTP requests cannot race.
        report = self.new_report([case(action, action.replace("_", " "), "Manual demonstration",
                                      "The requested action is confirmed by fresh firmware telemetry.",
                                      "Controlled Renode monitor operation.")], "manual")
        self.cancel_requested = False
        self.task = asyncio.current_task()
        entry = report["cases"][0]
        entry.update(status="running", started_at=utc())
        started = time.monotonic()
        self.reports.save(report)
        try:
            _, before = await self.demo.latest()
            entry["before"] = before.model_dump(mode="json") if before else None
            result = await self.demo.perform(action)
            entry.update(status="passed", after=result["reading"], health=result["health"])
            entry["checks"] = [{"name": "Action observed", "expected": action, "actual": result["message"], "passed": True}]
            return result | {"report_id": report["id"]}
        except asyncio.CancelledError:
            entry["status"] = "cancelled"
            raise
        except Exception as error:
            entry.update(status="failed", error=str(error))
            raise
        finally:
            entry.update(finished_at=utc(), duration_seconds=round(time.monotonic() - started, 3))
            report.update(status=entry["status"], finished_at=utc())
            try:
                self.reports.save(report)
            finally:
                self.task = None

    async def test(self, entry):
        key = entry["id"]
        check = lambda label, expected, actual, passed: self.check(entry, label, expected, actual, passed)
        _, before = await self.demo.latest()
        if key == "baseline":
            h, r = await self.fresh(lambda h, r: r.firmware.health.watchdog == "fed")
            check("Calibrated sensors", [25.08, 1006.53, [0, 0, 1], [0, 0, 0]],
                  [r.temperature_c, r.pressure_hpa, r.accel_g, r.firmware.imu.gyro_dps],
                  r.temperature_c == 25.08 and r.pressure_hpa == 1006.53 and r.accel_g == (0, 0, 1) and r.firmware.imu.gyro_dps == (0, 0, 0))
            check("Task health", "online, watchdog enabled/fed", h.state, h.state == "online" and r.firmware.health.watchdog_enabled)
        elif key in ("accel", "gyro", "imu_temp", "imu_limits", "baro_temp", "altitude"):
            if key in ("accel", "gyro"):
                await self.demo.perform("motion")
                _, r = await self.demo.latest()
                actual, expected = (r.accel_g, (0.5, -0.25, 0.75)) if key == "accel" else (r.firmware.imu.gyro_dps, (5, -20, 10))
                check("All axes", expected, actual, actual == expected)
            elif key == "imu_temp":
                await self.set_values("mpu6050", {"Temperature": 3400})
                _, r = await self.fresh(lambda h, r: r.firmware.imu and abs(r.firmware.imu.temperature_c - 46.53) < .02)
                check("Die temperature", 46.53, r.firmware.imu.temperature_c, abs(r.firmware.imu.temperature_c - 46.53) < .02)
            elif key == "imu_limits":
                await self.set_values("mpu6050", {"AccelerationX": -32768, "AccelerationY": 0, "AccelerationZ": 32767,
                                                "GyroscopeX": 32767, "GyroscopeY": -32768, "GyroscopeZ": 0})
                _, r = await self.fresh(lambda h, r: r.accel_g and r.accel_g[0] == -2)
                expected = [-2, 0, 32767 / 16384, 32767 / 131, -32768 / 131, 0]
                actual = list(r.accel_g) + list(r.firmware.imu.gyro_dps)
                check("Signed conversion", expected, actual, all(abs(a - b) < .002 for a, b in zip(actual, expected)))
            elif key == "baro_temp":
                await self.set_values("bmp280", {"RawTemperature": 530000})
                _, r = await self.fresh(lambda h, r: r.temperature_c == 28.25)
                check("Bosch compensation", 28.25, r.temperature_c, r.temperature_c == 28.25)
            else:
                await self.demo.perform("altitude")
                _, r = await self.demo.latest()
                expected = 44330 * (1 - (r.pressure_hpa * 100 / 101325) ** .19029495)
                check("Pressure", 946.43, r.pressure_hpa, r.pressure_hpa == 946.43)
                check("Altitude formula", expected, r.firmware.baro.altitude_m, math.isclose(expected, r.firmware.baro.altitude_m, abs_tol=.03))
        elif key in ("imu_fault", "baro_fault", "both_fault"):
            mode = {"imu_fault": "imu_off", "baro_fault": "baro_off", "both_fault": "both_off"}[key]
            result = await self.demo.perform(mode)
            entry["fault_observation"] = result
            _, r = await self.demo.latest()
            check("Correct missing payloads", mode, [r.firmware.imu is None, r.firmware.baro is None],
                  (r.firmware.imu is None) == (key != "baro_fault") and (r.firmware.baro is None) == (key != "imu_fault"))
            check("I2C failure counted", "counter increased", r.i2c_errors - before.i2c_errors, r.i2c_errors > before.i2c_errors)
            await self.demo.perform("rest")
            h, r = await self.demo.latest()
            check("Recovered without reboot", str(before.boot_id), str(r.boot_id), r.boot_id == before.boot_id and h.state == "online")
        elif key in ("gpio", "clocks", "timer", "systick"):
            checks = {
                "gpio": [("PA2/3 mode", 0x40020000, 0xF0, 0xA0), ("PA2/3 AF7", 0x40020020, 0xFF00, 0x7700),
                         ("PB8/9 mode", 0x40020400, 0xF0000, 0xA0000), ("PB8/9 AF4", 0x40020424, 0xFF, 0x44),
                         ("PB8/9 open drain", 0x40020404, 0x300, 0x300)],
                "clocks": [("AHB1 GPIO enables", 0x40023830, 3, 3), ("APB1 used peripherals", 0x40023840, 0x220001, 0x220001)],
                "timer": [("TIM2 enabled", 0x40000000, 1, 1), ("TIM2 prescaler", 0x40000028, 0xFFFF, 8399),
                          ("TIM2 reload", 0x4000002C, 0xFFFFFFFF, 99), ("NVIC IRQ28", 0xE000E100, 1 << 28, 1 << 28)],
                "systick": [("SysTick enabled with IRQ", 0xE000E010, 3, 3), ("SysTick reload", 0xE000E014, 0xFFFFFF, 83999),
                            ("DWT cycle counter", 0xE0001000, 1, 1)],
            }[key]
            for label, address, mask, expected in checks:
                actual = await self.read_word(address)
                check(label, hex(expected), hex(actual & mask), actual & mask == expected)
            if key == "systick":
                first = await self.read_word(0xE0001004)
                await self.fresh()
                second = await self.read_word(0xE0001004)
                check("DWT advancement", "counter changes", [first, second], second != first)
        elif key == "cadence":
            _, r = await self.demo.wait_for(lambda h, r: r.firmware.uptime_ms >= before.firmware.uptime_ms + 2000)
            elapsed = (r.firmware.uptime_ms - before.firmware.uptime_ms) / 1000
            rates = [(r.firmware.seq - before.firmware.seq) / elapsed, (r.sequence - before.sequence) / elapsed]
            check("Sample/frame Hz", [100, 10], rates, 95 < rates[0] < 105 and 9 < rates[1] < 11)
            check("Queue drops", 0, r.queue_drops - before.queue_drops, r.queue_drops == before.queue_drops)
        elif key == "memory":
            await self.demo.wait_for(lambda h, r: r.firmware.uptime_ms > 1500)
            memory = await self.memory_snapshot()
            check("FreeRTOS heap", "positive current and minimum free bytes", memory, 0 < memory["minimum_heap_bytes"] <= memory["free_heap_bytes"] <= 24576)
            check("All task stack margins", "all four > 0 words", memory["stack_minimum_words"], all(v > 0 for v in memory["stack_minimum_words"].values()))
        elif key == "flash":
            words = [await self.read_word(0x08060000 + i * 4) for i in range(5)]
            check("Committed boot record", "complementary words and 0x48425542 magic", [hex(w) for w in words],
                  words[0] ^ words[2] == 0xFFFFFFFF and words[1] ^ words[3] == 0xFFFFFFFF and words[4] == 0x48425542)
            uid = [await self.read_word(0x1FFF7A10 + i * 4) for i in range(3)]
            expected_id = "stm32-" + "".join(f"{w:08x}" for w in uid) + "-0"
            check("UID matches telemetry", expected_id, before.device_id, expected_id == before.device_id)
        elif key in ("reboot", "watchdog", "uart"):
            if key == "uart":
                cr1, brr = await self.read_word(0x4000440C), await self.read_word(0x40004408)
                check("UART TX/RX enabled", "UE, TE, RE set", hex(cr1), cr1 & 0x200C == 0x200C)
                # STM32 HAL quantizes DIV to hundredths before fraction rounding.
                # 42 MHz APB1 / 115200 baud -> BRR 0x16C, about 115385 baud.
                check("UART divider", 364, brr, brr == 364)
            reconnects = self.demo.source.reconnects
            await self.demo.perform(key)
            h, r = await self.demo.latest()
            check("Board identity", before.device_id, r.device_id, r.device_id == before.device_id)
            expected_boot = before.boot_id + (key != "uart")
            check("Boot transition", str(expected_boot), str(r.boot_id), r.boot_id == expected_boot)
            if key == "uart":
                check("Bridge reconnected", "count increased", self.demo.source.reconnects - reconnects, self.demo.source.reconnects > reconnects)
            else:
                check("Fresh session healthy", "low uptime, sequence and fed watchdog", [r.sequence, r.firmware.uptime_ms, r.firmware.health.watchdog],
                      r.sequence < 100 and r.firmware.uptime_ms < 10000 and r.firmware.health.watchdog == "fed")
        elif key == "websocket":
            async with connect(f"ws://127.0.0.1:{self.port}/ws/telemetry") as ws:
                first = json.loads(await asyncio.wait_for(ws.recv(), 5))
                second = json.loads(await asyncio.wait_for(ws.recv(), 5))
                check("Snapshot then telemetry", ["snapshot", "telemetry"], [first["type"], second["type"]], first["type"] == "snapshot" and second["type"] == "telemetry")
                check("Lossless boot identity", str(before.boot_id), second["reading"]["boot_id"], second["reading"]["boot_id"] == str(before.boot_id))
        elif key == "soak":
            initial = await self.memory_snapshot()
            stats = self.demo.app.state.hub.stats().model_dump()
            for _ in range(30):
                if self.cancel_requested:
                    raise asyncio.CancelledError()
                await asyncio.sleep(1)
            final = await self.memory_snapshot()
            _, r = await self.demo.latest()
            check("Stable boot", str(before.boot_id), str(r.boot_id), r.boot_id == before.boot_id)
            check("Stable free heap", initial["free_heap_bytes"], final["free_heap_bytes"], initial["free_heap_bytes"] == final["free_heap_bytes"])
            check("Task stack margins after soak", "all four > 0 words", final["stack_minimum_words"], all(v > 0 for v in final["stack_minimum_words"].values()))
            check("No new queue drops", before.queue_drops, r.queue_drops, before.queue_drops == r.queue_drops)
            after = self.demo.app.state.hub.stats().model_dump()
            for field in ("parse_errors", "sequence_anomalies", "websocket_drops", "device_rejections"):
                check(field, 0, after[field] - stats[field], after[field] == stats[field])
            check("Frames continue", ">= 30 frames in 30 wall seconds", after["frames_accepted"] - stats["frames_accepted"], after["frames_accepted"] - stats["frames_accepted"] >= 30)
            readings = await self.demo.app.state.hub.store.readings(r.device_id, 10000)
            check("Bounded history", "<= 600 entries", len(readings), len(readings) <= 600)

    async def close(self):
        if self.busy:
            self.cancel_requested = True
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[str] = Field(min_length=1, max_length=len(CATALOG))


def authorized(request):
    demo = request.app.state.demo
    if not secrets.compare_digest(request.headers.get("x-demo-token", ""), demo.token):
        raise HTTPException(403, "Open the local dashboard before using controls")
    if not demo.ready:
        raise HTTPException(503, "Preflight is still running")
    return request.app.state.lab


@router.get("/lab", include_in_schema=False)
async def page():
    return FileResponse(WEB / "index.html")


@router.get("/api/v1/lab")
async def state(request: Request):
    lab = request.app.state.lab
    run = None if lab.current is None else {
        **{k: v for k, v in lab.current.items() if k not in ("cases", "coverage")},
        "cases": [{k: v for k, v in c.items() if k not in ("before", "after", "health", "fault_observation")}
                  for c in lab.current["cases"]]}
    return {"catalog": CATALOG, "coverage": COVERAGE, "active": lab.busy,
            "cancel_requested": lab.cancel_requested, "run": run,
            "memory": lab.memory, "reports": lab.reports.recent()}


@router.post("/api/v1/lab/run", status_code=202)
async def start(body: Selection, request: Request):
    lab = authorized(request)
    return {"run_id": lab.start(body.cases)}


@router.post("/api/v1/lab/cancel")
async def cancel(request: Request):
    lab = authorized(request)
    lab.cancel_requested = True
    return {"message": "Stop requested; the current check will finish, then sensors will be restored."}


@router.get("/api/v1/lab/reports/{run_id}")
async def report(run_id: str, request: Request, format: str = Query("json", pattern="^(json|html)$")):
    try:
        path = request.app.state.lab.reports.path(run_id, format)
    except ValueError as error:
        raise HTTPException(404, "Report not found") from error
    if not path.is_file():
        raise HTTPException(404, "Report not found")
    return FileResponse(path, filename=path.name, media_type="application/json" if format == "json" else "text/html")
