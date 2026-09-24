"""Animated dashboard preview with in-memory sample data only.

This module never imports or starts the device server, microphone, or LLM.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

HTML = Path(__file__).with_name("dashboard.html")
CSS = Path(__file__).with_name("dashboard.css")
STARTED = time.monotonic()
LOCK = Lock()
LIGHT = {"power": "off"}
SERVICES = {
    "llm": {"label": "โมเดลภาษา (จำลอง)", "status": "stopped"},
    "server": {"label": "เซิร์ฟเวอร์อุปกรณ์ (จำลอง)", "status": "running"},
    "fake_light": {"label": "ไฟจำลอง", "status": "running"},
    "fake_garden": {"label": "สวนจำลอง", "status": "running"},
    "voice": {"label": "ไมโครโฟน (จำลอง)", "status": "stopped"},
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, content: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, code: int, value: object) -> None:
        self._send(code, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _allowed_host(self) -> bool:
        return self.headers.get("Host", "").lower() in {"127.0.0.1:8766", "localhost:8766"}

    def do_GET(self) -> None:
        if not self._allowed_host():
            self._json(403, {"error": "Host ไม่ได้รับอนุญาต"})
            return
        path = urlsplit(self.path)
        if path.path == "/":
            page = HTML.read_text(encoding="utf-8")
            page = page.replace("LOCAL SYSTEM</span>", "MOCK MODE</span>", 1)
            page = page.replace("VOICE INTERFACE // ONLINE READY", "VISUAL PREVIEW // NO MODEL RUNNING", 1)
            page = page.replace("พูด “จาวิส ปิดไฟที” ใส่ไมค์ แล้วดูข้อความที่ระบบได้ยินด้านล่าง",
                                "ข้อมูลตัวอย่างเท่านั้น — ไม่มีการเปิดไมค์หรือโมเดลจริง", 1)
            page = page.replace('<div id="error"',
                                '<div class="mock-notice">โหมดตัวอย่าง — ไม่ได้เปิดไมค์ โมเดล หรืออุปกรณ์จริง</div><div id="error"', 1)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path.path == "/dashboard.css":
            self._send(200, CSS.read_bytes(), "text/css; charset=utf-8")
        elif path.path == "/api/state":
            with LOCK:
                services = [{"name": name, "label": data["label"], "status": data["status"],
                             "reason": "ตัวอย่างเท่านั้น ไม่มี process จริง",
                             "uptime_s": int(time.monotonic() - STARTED) if data["status"] == "running" else None}
                            for name, data in SERVICES.items()]
                power = LIGHT["power"]
            self._json(200, {"mock": True, "services": services, "devices_error": None,
                             "devices": [
                                 {"device_id": "desk_light", "name": "ไฟโต๊ะ (จำลอง)",
                                  "device_type": "light", "online": True, "state": {"power": power}},
                                 {"device_id": "garden", "name": "สวน (จำลอง)",
                                  "device_type": "garden", "online": True,
                                  "state": {"soil_moisture": 62, "temperature": 27.4, "humidity": 68}},
                             ], "recent": [
                                 {"time": "ตัวอย่าง", "command": "จาวิส สถานะระบบเป็นอย่างไร",
                                  "intent": "status", "reply": "ระบบจำลองพร้อมแสดงผล"},
                             ]})
        elif path.path == "/api/logs":
            name = parse_qs(path.query).get("name", [""])[0]
            if name not in SERVICES:
                self._json(404, {"error": "ไม่พบ service"})
                return
            lines = (["[MOCK] ตัวอย่างเท่านั้น: ไม่มีการเปิดไมค์หรือบันทึกเสียง",
                      "[MOCK] คำสั่ง: จาวิส สถานะระบบเป็นอย่างไร"] if name == "voice"
                     else ["[MOCK] ไม่มี process จริงสำหรับบริการนี้"])
            self._json(200, {"name": name, "lines": lines})
        else:
            self._json(404, {"error": "ไม่พบหน้า"})

    def do_POST(self) -> None:
        if not self._allowed_host():
            self._json(403, {"error": "Host ไม่ได้รับอนุญาต"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._json(415, {"error": "ต้องส่ง application/json"})
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 <= length <= 4096:
                raise ValueError
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "JSON ไม่ถูกต้อง"})
            return
        path = urlsplit(self.path).path
        if path == "/api/device" and body.get("device_id") == "desk_light" and body.get("action") in {"turn_on", "turn_off"}:
            with LOCK:
                LIGHT["power"] = "on" if body["action"] == "turn_on" else "off"
            self._json(200, {"ok": True, "mock": True})
        elif path == "/api/service" and body.get("name") in SERVICES and body.get("op") in {"start", "stop", "restart"}:
            with LOCK:
                service = SERVICES[body["name"]]
                service["status"] = "stopped" if body["op"] == "stop" else "running"
                result = {"name": body["name"], "status": service["status"]}
            self._json(200, {"ok": True, "mock": True, "service": result})
        else:
            self._json(400, {"error": "คำสั่งจำลองไม่ถูกต้อง"})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    with ThreadingHTTPServer(("127.0.0.1", 8766), Handler) as server:
        print("JARVIS mock dashboard: http://127.0.0.1:8766/ (no model or microphone)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
