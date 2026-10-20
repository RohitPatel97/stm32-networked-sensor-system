"""Own a headless Renode process and control it through its private stdin pipe."""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import struct
import subprocess
import sys
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RENODE = ROOT / "build/tools/renode-1.17.0/renode_1.17.0-portable/renode.exe"
DEFAULT_ELF = ROOT / "build/stm32-on/target/stm32f401re/sensor_hub_firmware.elf"


def renode_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if any(c in value for c in '\r\n";'):
        raise ValueError("unsupported character in Renode path")
    return "@" + value.replace(" ", "\\ ")


class Renode:
    def __init__(self, directory: Path, uart_port: int, *, binary=DEFAULT_RENODE,
                 elf=DEFAULT_ELF, previous_boot=0, capture=True):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.binary, self.elf = Path(binary).resolve(), Path(elf).resolve()
        if not self.binary.is_file() or not self.elf.is_file():
            raise FileNotFoundError("Renode executable or compiled ARM ELF missing")
        if not 0 <= previous_boot < 2**64 - 1:
            raise ValueError("previous boot must leave room for another boot")
        self.uart_port, self.previous_boot = uart_port, previous_boot
        self.process = None
        self._reader = None
        self._waiters = {}
        self._lock = asyncio.Lock()
        self.log = None
        self.capture = capture
        self._error = None
        self._response = None

    async def start(self):
        journal = bytearray(b"\xff" * 0x20000)
        if self.previous_boot:
            low, high = self.previous_boot & 0xffffffff, self.previous_boot >> 32
            journal[:20] = struct.pack("<5I", low, high, low ^ 0xffffffff,
                                       high ^ 0xffffffff, 0x48425542)
        journal_path = self.directory / "initial-journal.bin"
        journal_path.write_bytes(journal)
        # Each new process represents a new board; MCU resets retain this UID and flash.
        uid = [secrets.randbits(32) for _ in range(3)]
        commands = [
            f"include {renode_path(ROOT / 'emulation/renode/HubSensors.cs')}",
            f"include {renode_path(ROOT / 'emulation/renode/HubTerminal.cs')}",
            f"include {renode_path(ROOT / 'emulation/renode/HubGPIOPort.cs')}",
            'mach create "sensor-hub"',
            f"machine LoadPlatformDescription {renode_path(ROOT / 'emulation/renode/stm32f401re.repl')}",
            "sysbus.cpu PerformanceInMips 84",
            f"sysbus LoadBinary {renode_path(journal_path)} 0x08060000",
            *[f"sysbus WriteDoubleWord {0x1fff7a10 + i * 4:#x} {word:#x}" for i, word in enumerate(uid)],
            f"emulation CreateHubTerminal {self.uart_port} \"hub_uart\"",
            "connector Connect sysbus.usart2 hub_uart",
            *([f"sysbus.usart2 CreateFileBackend {renode_path(self.directory / 'uart.jsonl')} true"] if self.capture else []),
            f"sysbus LoadELF {renode_path(self.elf)}",
            'macro reset "sysbus.cpu VectorTableOffset 0x08000000"',
        ]
        script = self.directory / "start.resc"
        script.write_text("\n".join(commands) + "\n", encoding="utf-8")
        self.log = RotatingFileHandler(self.directory / "renode.log", maxBytes=2_000_000,
                                       backupCount=3, encoding="utf-8")
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(self.binary), "--disable-gui", "--console", "-p", str(script),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            self._reader = asyncio.create_task(self._read_output())
            await self.command("hub_uart Port", timeout=30)
        except BaseException:
            await self.close()
            raise

    async def _read_output(self):
        try:
            while line := await self.process.stdout.readline():
                value = line.decode("utf-8", errors="replace")
                self.log.emit(logging.LogRecord("renode", logging.INFO, "", 0,
                                                value.rstrip(), (), None))
                if re.search(r"There was an error|Error E\d|Could not execute|Fatal error", value):
                    self._error = value.strip()
                token = value.strip()
                if self._response is not None and not token.startswith("HUB_ACK_") and len(self._response) < 512:
                    self._response.append(token)
                if token in self._waiters:
                    future = self._waiters[token]
                    if not future.done():
                        future.set_result(None)
        finally:
            for future in self._waiters.values():
                if not future.done():
                    future.set_exception(RuntimeError("Renode monitor ended; see renode.log"))

    async def command(self, command: str, timeout=15):
        async with self._lock:
            self._error = None
            self._response = []
            token = "HUB_ACK_" + secrets.token_hex(12)
            future = asyncio.get_running_loop().create_future()
            self._waiters[token] = future
            try:
                self.process.stdin.write(f'{command}\necho "{token}"\n'.encode())
                await self.process.stdin.drain()
                await asyncio.wait_for(future, timeout)
                # Renode can return exit code zero after a script error; check its log.
                if self._error:
                    raise RuntimeError(f"Renode command failed: {self._error}")
                return self._response.copy()
            finally:
                self._waiters.pop(token, None)
                self._response = None

    async def number(self, command):
        """Read one monitor scalar; never interpret the echoed command as a result."""
        lines = await self.command(command)
        for line in reversed(lines):
            if re.fullmatch(r"(?:0x[0-9a-fA-F]+|-?\d+)", line):
                return int(line, 16 if line.startswith("0x") else 10)
        raise RuntimeError(f"No scalar returned by {command}: {lines[-4:]}")

    async def registers(self, values):
        # One monitor acknowledgement for a batch of known register updates.
        await self.command("\n".join(values))

    async def run_for(self, seconds):
        await self.command(f'emulation RunFor "{seconds}"', timeout=seconds * 3 + 10)

    async def detach(self, sensor):
        if sensor not in ("mpu6050", "bmp280"):
            raise ValueError(sensor)
        await self.command(f"sysbus.i2c1 Unregister sysbus.i2c1.{sensor}")

    async def attach(self, sensor):
        model, address = {"mpu6050": ("HubMPU6050", "0x68"),
                          "bmp280": ("HubBMP280", "0x76")}[sensor]
        await self.command(f'machine LoadPlatformDescriptionFromString "{sensor}: Sensors.{model} @ i2c1 {address}"')

    async def close(self):
        if self.process is not None:
            if self.process.returncode is None:
                with suppress(OSError, RuntimeError):
                    self.process.stdin.write(b"quit\n")
                    await self.process.stdin.drain()
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            if self._reader:
                await self._reader
            self.process = None
        if self.log:
            self.log.close()
            self.log = None
