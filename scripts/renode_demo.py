"""Run the real ARM/FreeRTOS firmware in Renode and serve the live dashboard."""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
from datetime import datetime

from renode_runtime import DEFAULT_ELF, DEFAULT_RENODE, ROOT, Renode

sys.path.insert(0, str(ROOT / "gateway/src"))

import uvicorn
from sensor_monitor.app import create_app
from sensor_monitor.config import Settings
from sensor_monitor.tcp_source import TcpSource


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def run(args):
    uart_port = args.uart_port or free_port()
    directory = ROOT / "var/renode" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    emulator = (None if args.uart_port else
                Renode(directory, uart_port, binary=args.renode, elf=args.elf, capture=False))
    source = TcpSource("127.0.0.1", uart_port)
    app = create_app(Settings(port=args.port, log_dir=directory / "gateway-log"), source)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port,
                                         loop="asyncio", access_log=False))
    server_task = None
    try:
        if emulator:
            await emulator.start()
        server_task = asyncio.create_task(server.serve())
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("dashboard did not start")
            await asyncio.sleep(0.05)
        await asyncio.wait_for(source.connected.wait(), 5)
        if emulator:
            await emulator.command("start")
        print(f"ARM firmware -> Renode UART -> http://127.0.0.1:{args.port}", flush=True)
        print(f"Run evidence: {directory}", flush=True)
        await server_task
    finally:
        server.should_exit = True
        await source.close()
        if server_task is not None and not server_task.done():
            await server_task
        if emulator:
            await emulator.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renode", default=DEFAULT_RENODE)
    parser.add_argument("--elf", default=DEFAULT_ELF)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--uart-port", type=int,
                        help="connect to an already-running Renode GUI UART bridge")
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
