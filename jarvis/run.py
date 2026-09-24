"""Start and supervise the local JARVIS services with one command."""

from __future__ import annotations

import asyncio
import collections
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from .cli import request
from .config import Config, ROOT, load_config


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def http_healthy(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return b'"status":"ok"' in response.read().replace(b" ", b"")
    except OSError:
        return False


llm_healthy = http_healthy


def _whisper_server(model: Path, port: int) -> list[str]:
    # Same decoding as the CLI path in stt.py: Thai, greedy, no fallback, wake-word prompt.
    from .wake import WAKE_PROMPT

    return ["whisper-server", "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
            "-l", "th", "-bs", "1", "-bo", "1", "-nf", "-nt", "--prompt", WAKE_PROMPT]
TTS_PYTHON = ROOT / ".venv-tts" / "bin" / "python"


def device_online(device_id: str) -> bool:
    try:
        result = asyncio.run(request("127.0.0.1", 8765, {"type": "status"}))
        return any(d.get("device_id") == device_id for d in result.get("devices", []))
    except (OSError, ValueError, asyncio.TimeoutError):
        return False


def voice_pids() -> list[int]:
    if platform.system() == "Windows":
        return []
    # Only Python processes count: `pgrep -f` also matched shells whose command line merely
    # mentioned the voice command, so voice was reported as already running and never started.
    try:
        result = subprocess.run(["ps", "-Ao", "pid=,comm=,args="], capture_output=True, text=True, check=False)
    except OSError:
        return []
    pids = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, comm, args = int(parts[0]), Path(parts[1]).name.lower(), parts[2]
        if pid != os.getpid() and comm.startswith("python") and "-m jarvis.cli voice" in args:
            pids.append(pid)
    return pids


@dataclass
class Service:
    name: str
    label: str
    command: list[str]
    port: int | None = None
    ready_check: object = None
    process: subprocess.Popen[str] | None = None
    log: collections.deque[str] = field(default_factory=lambda: collections.deque(maxlen=300))
    started_at: float | None = None
    exit_code: int | None = None
    status: str = "stopped"
    reason: str | None = None
    external_pid: int | None = None
    ready_seen: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)


class Supervisor:
    def __init__(self, config: Config) -> None:
        self.config = config
        llm_port = urlsplit(config.llm_endpoint).port or 8080
        py = sys.executable
        self.services: dict[str, Service] = {
            "llm": Service("llm", "โมเดลภาษา", ["llama-server", "-m", str(config.llm_model),
                        "--port", str(llm_port), "-c", "4096", "-ngl", "99"], llm_port,
                        lambda: llm_healthy(llm_port)),
            "server": Service("server", "เซิร์ฟเวอร์อุปกรณ์", [py, "-m", "jarvis.server"], 8765,
                              lambda: port_open(8765)),
            "fake_light": Service("fake_light", "ไฟจำลอง", [py, "-m", "jarvis.fake_esp"],
                                  ready_check=lambda: device_online("desk_light")),
            "fake_garden": Service("fake_garden", "สวนจำลอง", [py, "-m", "jarvis.fake_esp", "--kind", "garden"],
                                   ready_check=lambda: device_online("garden")),
            "tts": Service("tts", "เสียงพูด F5-TTS", [str(TTS_PYTHON), "-m", "jarvis.tts_server"], config.f5_port,
                           lambda: http_healthy(config.f5_port)),
            "stt_wake": Service("stt_wake", "ฟังคำปลุก (Whisper)", _whisper_server(config.wake_model, config.wake_port),
                                config.wake_port, lambda: port_open(config.wake_port)),
            "stt_command": Service("stt_command", "ฟังคำสั่ง (Whisper)",
                                   _whisper_server(config.command_model, config.command_port),
                                   config.command_port, lambda: port_open(config.command_port)),
            "voice": Service("voice", "คำสั่งเสียง", [py, "-m", "jarvis.cli", "voice"],
                             ready_check=lambda: self.services["voice"].ready_seen),
        }
        self.order = (["server"] + [f"fake_{kind}" for kind in config.fake_devices if kind in {"light", "garden"}]
                      + ["llm", "tts", "stt_wake", "stt_command"])
        if config.autostart_voice:
            self.order.append("voice")
        self.closing = threading.Event()

    def _missing(self, name: str) -> str | None:
        if name == "llm":
            if not shutil.which("llama-server"):
                return "ไม่พบ llama-server"
            if not self.config.llm_model.is_file():
                return f"ไม่พบโมเดล {self.config.llm_model}"
        if name == "tts":
            if self.config.tts_engine != "f5":
                return "ปิดอยู่ ([tts] engine ไม่ใช่ f5)"
            if not TTS_PYTHON.is_file():
                return "ไม่พบ .venv-tts (ดู README หัวข้อ F5-TTS)"
            if not self.config.f5_ref_audio.is_file() or not self.config.f5_ref_audio.with_suffix(".txt").is_file():
                return f"ไม่พบเสียงต้นแบบ {self.config.f5_ref_audio.name} หรือไฟล์ .txt ของมัน"
        if name in {"stt_wake", "stt_command"}:
            port = self.config.wake_port if name == "stt_wake" else self.config.command_port
            model = self.config.wake_model if name == "stt_wake" else self.config.command_model
            if not port:
                return "ปิดอยู่ (port = 0 ใน config.toml)"
            if not shutil.which("whisper-server"):
                return "ไม่พบ whisper-server"
            if not model.is_file():
                return f"ไม่พบโมเดล {model.name}"
        if name == "voice":
            if not shutil.which("ffmpeg") or not shutil.which("whisper-cli"):
                return "ไม่พบ ffmpeg หรือ whisper-cli"
            if not self.config.wake_model.is_file():
                return f"ไม่พบโมเดลเสียง {self.config.wake_model}"
        return None

    def _external(self, service: Service) -> int | None:
        if service.name == "voice":
            pids = voice_pids()
            return pids[0] if pids else None
        if service.port and port_open(service.port):
            return 0
        if service.name == "fake_light" and device_online("desk_light"):
            return 0
        if service.name == "fake_garden" and device_online("garden"):
            return 0
        return None

    def _read_log(self, service: Service, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip("\r\n")
            with service.lock:
                service.log.append(line)
                if service.name == "voice" and service.process is process and "กำลังเปิดไมค์" in line:
                    service.ready_seen = True
            print(f"[{service.name}] {line}", flush=True)
        code = process.wait()
        with service.lock:
            if service.process is process:
                service.exit_code = code
                if service.status not in {"stopped", "missing"}:
                    service.status = "crashed"
                    service.reason = f"process exited ({code})"

    def _wait_ready(self, service: Service, process: subprocess.Popen[str], timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while process.poll() is None and not self.closing.is_set():
            try:
                if service.ready_check and service.ready_check():
                    with service.lock:
                        if service.process is process and service.status == "starting":
                            service.status = "running"
                            service.reason = None
                    return
            except (OSError, ValueError, asyncio.TimeoutError):
                pass
            if time.monotonic() >= deadline:
                with service.lock:
                    if service.process is process and service.status == "starting":
                        service.reason = "ยังรอความพร้อมอยู่"
            time.sleep(0.4)
        with service.lock:
            if service.process is process and service.status == "starting":
                if process.poll() is not None:
                    service.status, service.reason = "crashed", "process exited before ready"

    def start(self, name: str) -> dict:
        service = self.services[name]
        with service.lock:
            if service.process and service.process.poll() is None:
                return self.snapshot(name)
            external = self._external(service)
            if external is not None:
                service.status, service.reason, service.external_pid = "external", "เปิดอยู่ก่อนแล้ว", external or None
                return self.snapshot(name)
            missing = self._missing(name)
            if missing:
                service.status, service.reason = "missing", missing
                return self.snapshot(name)
            if name.startswith("fake_") and not port_open(8765):
                service.status, service.reason = "stopped", "รอเซิร์ฟเวอร์อุปกรณ์"
                return self.snapshot(name)
            if name == "voice" and (not port_open(8765) or not llm_healthy(urlsplit(self.config.llm_endpoint).port or 8080)):
                service.status, service.reason = "stopped", "รอเซิร์ฟเวอร์และโมเดลภาษา"
                return self.snapshot(name)
            env = dict(os.environ, PYTHONUNBUFFERED="1")
            try:
                process = subprocess.Popen(service.command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, bufsize=1,
                                           start_new_session=platform.system() != "Windows")
            except OSError as error:
                service.status, service.reason = "missing", str(error)
                return self.snapshot(name)
            service.process, service.started_at, service.exit_code = process, time.monotonic(), None
            service.ready_seen = False
            service.status, service.reason, service.external_pid = "starting", None, None
            threading.Thread(target=self._read_log, args=(service, process), daemon=True).start()
            threading.Thread(target=self._wait_ready, args=(service, process, 65 if name in {"llm", "tts"} else 12), daemon=True).start()
            return self.snapshot(name)

    def stop(self, name: str) -> dict:
        service = self.services[name]
        with service.lock:
            process = service.process
            if service.status == "external":
                return self.snapshot(name)
            service.status, service.reason = "stopped", None
            if process is None or process.poll() is not None:
                return self.snapshot(name)
            if platform.system() == "Windows":
                process.terminate()
            elif name == "voice":
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
        try:
            process.wait(timeout=3 if name == "voice" else 2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        return self.snapshot(name)

    def restart(self, name: str) -> dict:
        if self.services[name].status == "external":
            return self.snapshot(name)
        self.stop(name)
        return self.start(name)

    def snapshot(self, name: str) -> dict:
        service = self.services[name]
        with service.lock:
            process = service.process
            if service.status == "external" and self._external(service) is None:
                service.status, service.reason, service.external_pid = "stopped", None, None
            if process and process.poll() is not None and service.status in {"running", "starting"}:
                service.status, service.reason, service.exit_code = "crashed", "process exited", process.returncode
            return {"name": name, "label": service.label, "status": service.status,
                    "reason": service.reason, "pid": (process.pid if process and process.poll() is None else service.external_pid),
                    "uptime_s": int(time.monotonic() - service.started_at) if service.started_at and process and process.poll() is None else None,
                    "exit_code": service.exit_code}

    def startup(self) -> None:
        self.start("server")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and self.services["server"].status == "starting" and not self.closing.is_set():
            time.sleep(0.3)
        # LLM and TTS model loading run in parallel with fake device registration.
        self.start("llm")
        self.start("tts")
        self.start("stt_wake")
        self.start("stt_command")
        for name in self.order:
            if name in {"server", "llm", "tts", "stt_wake", "stt_command", "voice"}:
                continue
            if self.closing.is_set():
                return
            self.start(name)
        if self.config.autostart_voice:
            deadline = time.monotonic() + 70
            while time.monotonic() < deadline and not self.closing.is_set():
                # Voice works without F5 (falls back to `say`), so only wait while it is still loading.
                tts_loading = self.services["tts"].status == "starting"
                if (self.services["llm"].status in {"running", "external"} and not tts_loading
                        and llm_healthy(urlsplit(self.config.llm_endpoint).port or 8080)):
                    self.start("voice")
                    return
                if self.services["llm"].status in {"crashed", "missing"}:
                    self.services["voice"].reason = "รอโมเดลภาษา"
                    return
                time.sleep(0.4)

    def shutdown(self) -> None:
        self.closing.set()
        for name in ("voice", "fake_garden", "fake_light", "server", "stt_command", "stt_wake", "tts", "llm"):
            self.stop(name)


def main() -> None:
    from .dashboard import make_server

    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    config = load_config()
    supervisor = Supervisor(config)
    if config.dashboard_host not in {"127.0.0.1", "localhost"}:
        print("คำเตือน: dashboard ไม่มีระบบล็อกอิน อย่าเปิดบนเครือข่ายที่ไม่ไว้ใจ", flush=True)
    httpd = make_server(config.dashboard_host, config.dashboard_port, supervisor)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Dashboard: http://{config.dashboard_host}:{config.dashboard_port}/", flush=True)
    threading.Thread(target=supervisor.startup, daemon=True).start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("กำลังปิด JARVIS...", flush=True)
    finally:
        supervisor.shutdown()
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    main()
