"""A separate fake ESP32 process that registers and accepts light actions."""

from __future__ import annotations

import argparse
import asyncio
import json


def encode(message: dict) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode()


async def run(host: str, port: int, device_id: str) -> None:
    reader, writer = await asyncio.open_connection(host, port)
    state = {"power": "off"}
    writer.write(encode({
        "type": "register",
        "device_id": device_id,
        "name": "Desk light (fake ESP)",
        "device_type": "light",
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
    parser.add_argument("--id", default="desk_light")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port, args.id))
    except (KeyboardInterrupt, ConnectionRefusedError):
        print("Could not connect to JARVIS. Is the server running?")


if __name__ == "__main__":
    main()
