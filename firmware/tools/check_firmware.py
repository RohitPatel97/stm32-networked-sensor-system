"""Check the linked F401RE image layout; this does not execute MCU firmware."""

from __future__ import annotations

import argparse
import struct
import subprocess
from pathlib import Path


def check(elf: Path, binary: Path, nm: str) -> None:
    symbols = {}
    output = subprocess.run([nm, "--defined-only", str(elf)], check=True,
                            capture_output=True, text=True, timeout=30).stdout
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 3:
            address, kind, name = fields
            symbols[name] = (int(address, 16), kind)
    data = binary.read_bytes()
    if not 180 <= len(data) <= 384 * 1024:
        raise ValueError("image is missing vectors or overlaps the reserved boot journal")
    if struct.unpack_from("<I", data)[0] != 0x20018000:
        raise ValueError("initial stack pointer is not at the top of F401RE SRAM")
    vectors = {"Reset_Handler": 1, "SVC_Handler": 11, "PendSV_Handler": 14,
               "SysTick_Handler": 15, "TIM2_IRQHandler": 16 + 28}
    for name, index in vectors.items():
        if name not in symbols:
            raise ValueError(f"missing handler: {name}")
        address, kind = symbols[name]
        # The ST startup Reset_Handler is weak by design; RTOS/IRQ overrides
        # must be strong, not the startup file's Default_Handler aliases.
        if name != "Reset_Handler" and kind != "T":
            raise ValueError(f"{name} is not a strong code symbol")
        if struct.unpack_from("<I", data, index * 4)[0] != (address | 1):
            raise ValueError(f"vector does not point to Thumb handler {name}")
        if not 0x08000000 <= address < 0x08060000:
            raise ValueError(f"handler is outside flash: {name}")
    heap_start = symbols["__libc_heap_start__"][0]
    heap_end = symbols["__libc_heap_end__"][0]
    if not 0x20000000 <= heap_start < heap_end <= 0x20018000 - 2048:
        raise ValueError("libc heap overlaps the reserved interrupt stack")
    for name in ("HubRtos_Init", "xTaskCreate", "vTaskStartScheduler", "hub_telemetry_format_v3",
                 "hub_identity_init", "hub_boot_counter_next"):
        if name not in symbols:
            raise ValueError(f"application/kernel code missing: {name}")
    print(f"Firmware layout verified: {len(data)} flash bytes, 5 vectors, real FreeRTOS symbols")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--nm", default="arm-none-eabi-nm")
    args = parser.parse_args()
    check(args.elf, args.bin, args.nm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
