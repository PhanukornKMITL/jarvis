"""TCP server for device discovery and simple actions (stdlib only)."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def encode(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


@dataclass
class DeviceSession:
    device_id: str
    name: str
    device_type: str
    state: dict[str, Any]
    writer: asyncio.StreamWriter
    last_seen: float = field(default_factory=time.monotonic)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: dict[str, Any]) -> None:
        async with self.write_lock:
            self.writer.write(encode(message))
            await self.writer.drain()


class JarvisServer:
    def __init__(self) -> None:
        self.devices: dict[str, DeviceSession] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        device_id: str | None = None
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=5)
            if not raw:
                return
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "register":
                device_id = str(message["device_id"])
                old = self.devices.get(device_id)
                if old:
                    old.writer.close()
                session = DeviceSession(
                    device_id=device_id,
                    name=str(message.get("name", device_id)),
                    device_type=str(message.get("device_type", "unknown")),
                    state=dict(message.get("state", {})),
                    writer=writer,
                )
                self.devices[device_id] = session
                await writer.drain()
                writer.write(encode({"type": "registered", "device_id": device_id}))
                await writer.drain()
                await self.read_device_messages(reader, session)
            elif kind == "status":
                writer.write(encode({"devices": self.status()}))
                await writer.drain()
            elif kind == "action":
                response = await self.dispatch(
                    str(message.get("target", "")),
                    str(message.get("action", "")),
                )
                writer.write(encode(response))
                await writer.drain()
            else:
                writer.write(encode({"error": "unknown request"}))
                await writer.drain()
        except (asyncio.TimeoutError, json.JSONDecodeError, KeyError, ConnectionError):
            pass
        finally:
            if device_id and self.devices.get(device_id, None) and self.devices[device_id].writer is writer:
                del self.devices[device_id]
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def read_device_messages(self, reader: asyncio.StreamReader, session: DeviceSession) -> None:
        while raw := await reader.readline():
            message = json.loads(raw)
            kind = message.get("type")
            session.last_seen = time.monotonic()
            if kind == "state":
                session.state = dict(message.get("state", {}))
            elif kind == "heartbeat":
                continue
            elif kind == "action_result":
                action_id = str(message.get("action_id", ""))
                future = self.pending.pop(action_id, None)
                if future and not future.done():
                    if message.get("ok"):
                        session.state = dict(message.get("state", session.state))
                        future.set_result({"ok": True, "device_id": session.device_id, "state": session.state})
                    else:
                        future.set_result({"ok": False, "error": str(message.get("error", "action failed"))})

    def status(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        return [
            {
                "device_id": d.device_id,
                "name": d.name,
                "device_type": d.device_type,
                "online": now - d.last_seen < 10,
                "state": d.state,
            }
            for d in sorted(self.devices.values(), key=lambda d: d.device_id)
        ]

    async def dispatch(self, target: str, action: str) -> dict[str, Any]:
        session = self.devices.get(target)
        if not session:
            return {"ok": False, "error": f"device '{target}' is not connected"}
        action_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[action_id] = future
        try:
            await session.send({"type": "action", "action_id": action_id, "action": action})
            return await asyncio.wait_for(future, timeout=5)
        except (asyncio.TimeoutError, ConnectionError):
            self.pending.pop(action_id, None)
            return {"ok": False, "error": "device did not respond"}


async def serve(host: str, port: int) -> None:
    core = JarvisServer()
    server = await asyncio.start_server(core.handle, host, port)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"JARVIS listening on {addresses}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the JARVIS device server")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(serve(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
