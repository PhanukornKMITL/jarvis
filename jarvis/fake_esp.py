"""A separate fake ESP32 process that registers and accepts light actions."""

from __future__ import annotations

import argparse
import asyncio
import json


def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


KINDS = {
    "light": ("Desk light (fake ESP)", {"power": "off"}),
    "garden": ("Garden sensor (fake ESP)", {"soil_moisture": 28, "temperature": 31.5, "humidity": 68}),
}


async def run(host: str, port: int, device_id: str, kind: str = "light") -> None:
    reader, writer = await asyncio.open_connection(host, port)
    name, state = KINDS[kind]
    writer.write(encode({
        "type": "register",
        "device_id": device_id,
        "name": name,
        "device_type": kind,
        "state": state,
    }))
    await writer.drain()
    print(f"Registered {device_id} with JARVIS at {host}:{port}", flush=True)
    try:
        while raw := await reader.readline():
            message = json.loads(raw)
            if message.get("type") != "action":
                continue
            action = message.get("action")
            if action == "turn_on":
                state["power"] = "on"
                result = {"type": "action_result", "action_id": message["action_id"], "ok": True, "state": state}
            elif action == "turn_off":
                state["power"] = "off"
                result = {"type": "action_result", "action_id": message["action_id"], "ok": True, "state": state}
            else:
                result = {"type": "action_result", "action_id": message["action_id"], "ok": False, "error": f"unsupported action: {action}"}
            writer.write(encode(result))
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one simulated ESP32 device")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--kind", choices=sorted(KINDS), default="light")
    parser.add_argument("--id", help="device id (default: desk_light for light, garden for garden)")
    args = parser.parse_args()
    device_id = args.id or ("desk_light" if args.kind == "light" else "garden")
    try:
        asyncio.run(run(args.host, args.port, device_id, args.kind))
    except (KeyboardInterrupt, ConnectionRefusedError):
        print("Could not connect to JARVIS. Is the server running?")


if __name__ == "__main__":
    main()
