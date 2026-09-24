"""Local dashboard HTTP API. No third-party dependencies."""

from __future__ import annotations

import asyncio
import collections
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .cli import request
from .config import Config

HTML = Path(__file__).with_name("dashboard.html")
CSS = Path(__file__).with_name("dashboard.css")


def recent_commands(config: Config) -> list[dict]:
    if config.dataset_dir is None:
        return []
    path = config.dataset_dir / "index.jsonl"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as file:
        lines = collections.deque(file, maxlen=20)
    result = []
    for line in reversed(lines):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def make_server(host: str, port: int, supervisor) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _allowed_host(self) -> bool:
            local_ip = self.connection.getsockname()[0]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"{host}:{port}", f"{local_ip}:{port}"}
            return self.headers.get("Host", "").lower() in {item.lower() for item in allowed}

        def _json(self, code: int, value: object) -> None:
            data = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, code: int, message: str) -> None:
            self._json(code, {"ok": False, "error": message})

        def _body(self) -> dict | None:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._error(415, "ต้องส่ง application/json")
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
                if length < 0 or length > 4096:
                    self._error(413, "body ใหญ่เกิน 4 KB")
                    return None
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("JSON object required")
                return value
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "JSON ไม่ถูกต้อง")
                return None

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._error(403, "Host ไม่ได้รับอนุญาต")
                return
            path = urlsplit(self.path)
            if path.path == "/":
                data = HTML.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path.path == "/dashboard.css":
                data = CSS.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/css; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            elif path.path == "/api/state":
                try:
                    devices = asyncio.run(request("127.0.0.1", 8765, {"type": "status"})).get("devices", [])
                    devices_error = None
                except (OSError, ValueError, asyncio.TimeoutError):
                    devices, devices_error = [], "ติดต่อ server ไม่ได้"
                self._json(200, {"services": [supervisor.snapshot(name) for name in supervisor.services],
                                 "devices": devices, "devices_error": devices_error,
                                 "recent": recent_commands(supervisor.config)})
            elif path.path == "/api/logs":
                name = parse_qs(path.query).get("name", [""])[0]
                if name not in supervisor.services:
                    self._error(404, "ไม่พบ service")
                    return
                service = supervisor.services[name]
                with service.lock:
                    lines = list(service.log)[-200:]
                self._json(200, {"name": name, "lines": lines})
            else:
                self._error(404, "ไม่พบหน้า")

        def do_POST(self) -> None:
            if not self._allowed_host():
                self._error(403, "Host ไม่ได้รับอนุญาต")
                return
            body = self._body()
            if body is None:
                return
            path = urlsplit(self.path).path
            if path == "/api/device":
                target, action = body.get("device_id"), body.get("action")
                if not isinstance(target, str) or not target or action not in {"turn_on", "turn_off"}:
                    self._error(400, "device_id หรือ action ไม่ถูกต้อง")
                    return
                try:
                    result = asyncio.run(request("127.0.0.1", 8765,
                                                 {"type": "action", "target": target, "action": action}))
                    self._json(200 if result.get("ok") else 400, result)
                except (OSError, ValueError, asyncio.TimeoutError):
                    self._error(503, "ติดต่อ server ไม่ได้")
            elif path == "/api/service":
                name, op = body.get("name"), body.get("op")
                if not isinstance(name, str) or name not in supervisor.services or op not in {"start", "stop", "restart"}:
                    self._error(400, "name หรือ op ไม่ถูกต้อง")
                    return
                self._json(200, {"ok": True, "service": getattr(supervisor, op)(name)})
            else:
                self._error(404, "ไม่พบ API")

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
