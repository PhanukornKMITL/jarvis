"""Small command line client for checking and controlling devices."""

from __future__ import annotations

import argparse
import asyncio
import json
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
    voice_parser = subparsers.add_parser("voice", help="listen locally for the Jarvis wake word")
    voice_parser.add_argument(
        "--model",
        default=str(Path(__file__).resolve().parent.parent / ".models" / "ggml-base.bin"),
        help="path to a local Whisper model",
    )
    voice_parser.add_argument("--audio-device", default=":0" if __import__("platform").system() != "Windows" else "Microphone",
                             help="macOS: AVFoundation device index; Windows: DirectShow microphone name")
    try:
        args = parser.parse_args()
        if args.command == "voice":
            from .voice import listen

            raise SystemExit(listen(args.host, args.port, args.model, args.audio_device))
        raise SystemExit(asyncio.run(run(args)))
    except (ConnectionRefusedError, asyncio.TimeoutError):
        parser.exit(1, "JARVIS server is not responding. Start it with: python -m jarvis.server\n")


if __name__ == "__main__":
    main()
