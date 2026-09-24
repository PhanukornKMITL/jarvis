"""Small command line client for checking and controlling devices."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
from dataclasses import replace
from pathlib import Path


async def request(host: str, port: int, message: dict) -> dict:
    reader, writer = await asyncio.open_connection(host, port)
    writer.write((json.dumps(message) + "\n").encode())
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), timeout=6))
    writer.close()
    await writer.wait_closed()
    return response


async def run(args: argparse.Namespace) -> int:
    if args.command == "devices":
        response = await request(args.host, args.port, {"type": "status"})
        devices = response.get("devices", [])
        if not devices:
            print("No devices connected")
        for device in devices:
            state = ", ".join(f"{key}={value}" for key, value in device["state"].items()) or "no state"
            print(f"{device['device_id']}\t{'online' if device['online'] else 'offline'}\t{state}")
        return 0
    if args.command == "status":
        response = await request(args.host, args.port, {"type": "status"})
        print(json.dumps(response.get("devices", []), indent=2))
        return 0
    if args.command in {"light-on", "light-off"}:
        response = await request(args.host, args.port, {
            "type": "action",
            "target": args.device,
            "action": "turn_on" if args.command == "light-on" else "turn_off",
        })
        if response.get("ok"):
            print(f"{args.device} power={response['state'].get('power')}")
            return 0
        print(response.get("error", "action failed"))
        return 1
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Talk to the local JARVIS prototype")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("devices", help="list connected devices")
    subparsers.add_parser("status", help="show device state")
    for command in ("light-on", "light-off"):
        action_parser = subparsers.add_parser(command)
        action_parser.add_argument("--device", default="desk_light")
    optimize_parser = subparsers.add_parser("optimize", help="quit apps listed in [optimize] quit_apps to free RAM")
    optimize_parser.add_argument("--dry-run", action="store_true", help="only show what would be quit")
    voice_parser = subparsers.add_parser("voice", help="listen locally for the Jarvis wake word")
    voice_parser.add_argument("--model", help="wake-word Whisper model (overrides config.toml)")
    # ":default" follows the macOS input setting; ":0" broke when BlackHole took index 0.
    voice_parser.add_argument("--audio-device", default=":default" if platform.system() != "Windows" else "Microphone",
                             help="macOS: AVFoundation device (':default', ':1' or ':<name>'); Windows: DirectShow microphone name")
    try:
        args = parser.parse_args()
        if args.command == "optimize":
            from .config import load_config
            from .optimize import optimize

            result = optimize(list(load_config().quit_apps), dry_run=args.dry_run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(0 if result.get("ok") else 1)
        if args.command == "voice":
            from .config import load_config
            from .voice import listen

            config = load_config()
            if args.model:
                config = replace(config, wake_model=Path(args.model).resolve())
            raise SystemExit(listen(args.host, args.port, args.audio_device, config))
        raise SystemExit(asyncio.run(run(args)))
    except (ConnectionRefusedError, asyncio.TimeoutError):
        parser.exit(1, "JARVIS server is not responding. Start it with: python -m jarvis.server\n")


if __name__ == "__main__":
    main()
